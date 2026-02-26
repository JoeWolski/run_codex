from __future__ import annotations

import json
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from click.testing import CliRunner
from fastapi.testclient import TestClient

import sys

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import agent_hub.server as hub_server


class TestHubLifecycleApiIntegration:
    def setup_method(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.tmp.name)
        self.data_dir = self.tmp_path / "hub"
        self.config = self.tmp_path / "agent.config.toml"
        self.config.write_text("model = 'test'\n", encoding="utf-8")
        self.runner = CliRunner()

    def teardown_method(self) -> None:
        self.tmp.cleanup()

    def _build_app(self):
        with patch("agent_hub.server.uvicorn.run", return_value=None) as uvicorn_run:
            result = self.runner.invoke(
                hub_server.main,
                [
                    "--data-dir",
                    str(self.data_dir),
                    "--config-file",
                    str(self.config),
                    "--no-frontend-build",
                ],
            )
        assert result.exit_code == 0, result.output
        return uvicorn_run.call_args.args[0]

    @staticmethod
    def _seed_ready_project(state: hub_server.HubState, *, project_id: str = "project-1") -> dict[str, str]:
        now = hub_server._iso_now()
        project = {
            "id": project_id,
            "name": "project",
            "repo_url": "https://example.test/repo.git",
            "setup_script": "",
            "base_image_mode": "tag",
            "base_image_value": hub_server.DEFAULT_AGENT_IMAGE,
            "default_ro_mounts": [],
            "default_rw_mounts": [],
            "default_env_vars": [],
            "default_branch": "main",
            "created_at": now,
            "updated_at": now,
            "setup_snapshot_image": "",
            "build_status": "ready",
            "build_error": "",
            "build_started_at": now,
            "build_finished_at": now,
            "repo_head_sha": "abc123",
            "credential_binding": hub_server._normalize_project_credential_binding(None),
        }
        project["setup_snapshot_image"] = state._project_setup_snapshot_tag(project)
        state_data = state.load()
        state_data["projects"][project_id] = project
        state.save(state_data, reason="test_seed_ready_project")
        return {"project_id": project_id}

    def test_state_route_returns_status_payload(self) -> None:
        app = self._build_app()
        with TestClient(app) as client:
            response = client.get("/api/state")
        assert response.status_code == 200, response.text
        payload = response.json()
        assert "projects" in payload
        assert "chats" in payload
        assert "settings" in payload

    def test_project_chat_start_is_idempotent_with_request_id(self) -> None:
        app = self._build_app()
        state = app.state.hub_state
        ids = self._seed_ready_project(state)

        with patch("agent_hub.server._docker_image_exists", return_value=True), patch(
            "agent_hub.server._is_process_running", return_value=True
        ), patch.object(
            hub_server.HubState, "_ensure_chat_clone", return_value=Path.cwd()
        ), patch.object(hub_server.HubState, "_sync_checkout_to_remote", return_value=None), patch.object(
            hub_server.HubState, "_prepare_chat_runtime_config", return_value=Path.cwd() / "runtime.toml"
        ), patch.object(hub_server.HubState, "_spawn_chat_process", return_value=SimpleNamespace(pid=1234)):
            with TestClient(app) as client:
                first = client.post(
                    f"/api/projects/{ids['project_id']}/chats/start",
                    json={"request_id": "req-1", "agent_type": "codex", "agent_args": []},
                )
                second = client.post(
                    f"/api/projects/{ids['project_id']}/chats/start",
                    json={"request_id": "req-1", "agent_type": "codex", "agent_args": []},
                )

        assert first.status_code == 200, first.text
        assert second.status_code == 200, second.text
        first_chat = first.json()["chat"]
        second_chat = second.json()["chat"]
        assert first_chat["id"] == second_chat["id"]

    def test_chat_lifecycle_routes_and_events_snapshot(self) -> None:
        app = self._build_app()
        state = app.state.hub_state
        ids = self._seed_ready_project(state, project_id="project-lifecycle")
        chat = state.create_chat(
            project_id=ids["project_id"],
            profile="",
            ro_mounts=[],
            rw_mounts=[],
            env_vars=[],
            agent_args=[],
            agent_type=hub_server.AGENT_TYPE_CODEX,
        )

        with patch("agent_hub.server._docker_image_exists", return_value=True), patch(
            "agent_hub.server._is_process_running", side_effect=lambda pid: isinstance(pid, int)
        ), patch.object(hub_server.HubState, "_ensure_chat_clone", return_value=Path.cwd()), patch.object(
            hub_server.HubState, "_sync_checkout_to_remote", return_value=None
        ), patch.object(
            hub_server.HubState, "_prepare_chat_runtime_config", return_value=Path.cwd() / "runtime.toml"
        ), patch.object(hub_server.HubState, "_spawn_chat_process", return_value=SimpleNamespace(pid=7777)):
            with TestClient(app) as client:
                with client.websocket_connect("/api/events") as ws:
                    first_event = json.loads(ws.receive_text())
                    assert first_event["type"] == hub_server.EVENT_TYPE_SNAPSHOT

                    started = client.post(f"/api/chats/{chat['id']}/start")
                    assert started.status_code == 200, started.text
                    started_payload = started.json()["chat"]
                    assert started_payload["status"] == hub_server.CHAT_STATUS_RUNNING

                    # Simulate project snapshot drift so refresh-container is valid.
                    state_data = state.load()
                    project = dict(state_data["projects"][ids["project_id"]])
                    project["setup_script"] = "echo refreshed"
                    project["setup_snapshot_image"] = state._project_setup_snapshot_tag(project)
                    project["build_status"] = "ready"
                    state_data["projects"][ids["project_id"]] = project
                    state.save(state_data, reason="test_mark_project_outdated")

                    refreshed = client.post(f"/api/chats/{chat['id']}/refresh-container")
                    assert refreshed.status_code == 200, refreshed.text

                    closed = client.post(f"/api/chats/{chat['id']}/close")
                    assert closed.status_code == 200, closed.text
                    assert closed.json()["chat"]["status"] == hub_server.CHAT_STATUS_STOPPED

                    observed_state_change = False
                    for _ in range(8):
                        event = json.loads(ws.receive_text())
                        if event.get("type") == hub_server.EVENT_TYPE_STATE_CHANGED:
                            observed_state_change = True
                            break
                    assert observed_state_change

    def test_build_logs_endpoint_returns_failure_evidence(self) -> None:
        app = self._build_app()
        state = app.state.hub_state
        ids = self._seed_ready_project(state, project_id="project-build-logs")
        log_path = state.project_build_log(ids["project_id"])
        log_text = "docker run --group-add agent ...\nUnable to find group agent\n"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text(log_text, encoding="utf-8")

        with TestClient(app) as client:
            response = client.get(f"/api/projects/{ids['project_id']}/build-logs")

        assert response.status_code == 200, response.text
        assert "Unable to find group agent" in response.text
        assert "docker run --group-add agent" in response.text
