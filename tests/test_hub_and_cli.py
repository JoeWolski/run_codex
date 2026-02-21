from __future__ import annotations

import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace

from click.testing import CliRunner
from fastapi import HTTPException

import sys

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import agent_hub.server as hub_server
import agent_cli.cli as image_cli


class HubStateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.tmp.name)
        self.config_file = self.tmp_path / "config.toml"
        self.config_file.write_text("model = 'test'\n", encoding="utf-8")
        self.snapshot_patcher = patch.object(
            hub_server.HubState,
            "_prepare_project_snapshot_for_project",
            lambda state_obj, project, **_kwargs: state_obj._project_setup_snapshot_tag(project),
        )
        self.snapshot_patcher.start()
        self.schedule_patcher = patch.object(
            hub_server.HubState,
            "_schedule_project_build",
            lambda state_obj, project_id: state_obj._build_project_snapshot(project_id),
        )
        self.schedule_patcher.start()
        self.state = hub_server.HubState(self.tmp_path / "hub", self.config_file)
        self.host_ro = self.tmp_path / "host_ro"
        self.host_rw = self.tmp_path / "host_rw"
        self.host_ro.mkdir(parents=True, exist_ok=True)
        self.host_rw.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        self.snapshot_patcher.stop()
        self.schedule_patcher.stop()
        self.tmp.cleanup()

    def test_project_defaults_are_persisted(self) -> None:
        project = self.state.add_project(
            repo_url="https://example.com/org/repo.git",
            name="demo",
            default_branch="main",
            setup_script="echo hi",
            base_image_mode="tag",
            base_image_value="nvidia/cuda:12.2.2-cudnn8-devel-ubuntu22.04",
            default_ro_mounts=[f"{self.host_ro}:/data_ro"],
            default_rw_mounts=[f"{self.host_rw}:/data_rw"],
            default_env_vars=["FOO=bar"],
        )
        payload = self.state.state_payload()
        loaded = next(item for item in payload["projects"] if item["id"] == project["id"])
        self.assertEqual(loaded["default_ro_mounts"], [f"{self.host_ro}:/data_ro"])
        self.assertEqual(loaded["default_rw_mounts"], [f"{self.host_rw}:/data_rw"])
        self.assertEqual(loaded["default_env_vars"], ["FOO=bar"])
        self.assertEqual(loaded["base_image_mode"], "tag")
        self.assertEqual(loaded["setup_snapshot_image"], self.state._project_setup_snapshot_tag(project))
        self.assertEqual(loaded["build_status"], "ready")

    def test_openai_credentials_round_trip_status(self) -> None:
        initial = self.state.openai_auth_status()
        self.assertFalse(initial["connected"])
        self.assertEqual(initial["key_hint"], "")

        with patch("agent_hub.server._verify_openai_api_key", return_value=None) as verify_call:
            saved = self.state.connect_openai("sk-test-abcdefghijklmnopqrstuvwxyz1234")

        verify_call.assert_called_once_with("sk-test-abcdefghijklmnopqrstuvwxyz1234")
        self.assertTrue(saved["connected"])
        self.assertTrue(saved["key_hint"].startswith("sk-tes"))
        self.assertTrue(saved["updated_at"])
        self.assertTrue(self.state.openai_credentials_file.exists())

        mode = self.state.openai_credentials_file.stat().st_mode & 0o777
        self.assertEqual(mode, 0o600)

        payload = self.state.auth_settings_payload()
        self.assertIn("providers", payload)
        self.assertIn("openai", payload["providers"])
        self.assertTrue(payload["providers"]["openai"]["connected"])

        disconnected = self.state.disconnect_openai()
        self.assertFalse(disconnected["connected"])
        self.assertFalse(self.state.openai_credentials_file.exists())

    def test_connect_openai_skips_verification_when_requested(self) -> None:
        with patch("agent_hub.server._verify_openai_api_key") as verify_call:
            saved = self.state.connect_openai("sk-test-abcdefghijklmnopqrstuvwxyz1234", verify=False)
        verify_call.assert_not_called()
        self.assertTrue(saved["connected"])

    def test_connect_openai_verify_failure_does_not_persist_key(self) -> None:
        with patch(
            "agent_hub.server._verify_openai_api_key",
            side_effect=HTTPException(status_code=400, detail="OpenAI rejected the API key."),
        ):
            with self.assertRaises(HTTPException):
                self.state.connect_openai("sk-test-abcdefghijklmnopqrstuvwxyz1234")
        self.assertFalse(self.state.openai_credentials_file.exists())

    def test_first_url_in_text_trims_trailing_punctuation(self) -> None:
        value = hub_server._first_url_in_text(
            "Starting local login server on http://localhost:1455.",
            "http://localhost",
        )
        self.assertEqual(value, "http://localhost:1455")

    def test_parse_local_callback_allows_trailing_period(self) -> None:
        local_url, callback_port, callback_path = hub_server._parse_local_callback("http://localhost:1455.")
        self.assertEqual(callback_port, 1455)
        self.assertEqual(callback_path, "/auth/callback")
        self.assertTrue(local_url.startswith("http://localhost:1455"))

    def test_openai_auth_status_reports_account_credentials(self) -> None:
        self.state.openai_codex_auth_file.parent.mkdir(parents=True, exist_ok=True)
        self.state.openai_codex_auth_file.write_text(
            json.dumps(
                {
                    "auth_mode": "chatgpt",
                    "tokens": {
                        "refresh_token": "rt-test",
                    },
                }
            ),
            encoding="utf-8",
        )
        status = self.state.openai_auth_status()
        self.assertTrue(status["account_connected"])
        self.assertEqual(status["account_auth_mode"], "chatgpt")
        self.assertTrue(status["account_updated_at"])

    def test_start_openai_account_login_uses_host_network(self) -> None:
        captured: dict[str, list[str]] = {}

        def fake_popen(cmd: list[str], **kwargs):
            del kwargs
            captured["cmd"] = list(cmd)
            return SimpleNamespace(pid=4321, stdout=None, wait=lambda: 0, poll=lambda: None)

        with patch("agent_hub.server.shutil.which", return_value="/usr/bin/docker"), patch(
            "agent_hub.server._docker_image_exists",
            return_value=True,
        ), patch(
            "agent_hub.server.subprocess.Popen",
            side_effect=fake_popen,
        ), patch.object(
            hub_server.HubState,
            "_start_openai_login_reader",
            return_value=None,
        ):
            payload = self.state.start_openai_account_login(method="browser_callback")

        cmd = captured["cmd"]
        self.assertIn("--network", cmd)
        self.assertIn("host", cmd)
        self.assertIn("codex", cmd)
        self.assertIn("login", cmd)
        self.assertNotIn("--device-auth", cmd)
        self.assertIn("session", payload)

    def test_start_openai_account_login_device_auth_includes_flag(self) -> None:
        captured: dict[str, list[str]] = {}

        def fake_popen(cmd: list[str], **kwargs):
            del kwargs
            captured["cmd"] = list(cmd)
            return SimpleNamespace(pid=4322, stdout=None, wait=lambda: 0, poll=lambda: None)

        with patch("agent_hub.server.shutil.which", return_value="/usr/bin/docker"), patch(
            "agent_hub.server._docker_image_exists",
            return_value=True,
        ), patch(
            "agent_hub.server.subprocess.Popen",
            side_effect=fake_popen,
        ), patch.object(
            hub_server.HubState,
            "_start_openai_login_reader",
            return_value=None,
        ):
            payload = self.state.start_openai_account_login(method="device_auth")

        cmd = captured["cmd"]
        self.assertIn("--device-auth", cmd)
        self.assertIn("session", payload)

    def test_forward_openai_account_callback_proxies_to_local_server(self) -> None:
        captured: dict[str, str] = {}

        class CallbackHandler(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                captured["path"] = self.path
                self.send_response(200)
                self.send_header("Content-Type", "text/plain")
                self.end_headers()
                self.wfile.write(b"ok")

            def log_message(self, format: str, *args) -> None:  # noqa: A003
                del format, args
                return

        server = HTTPServer(("127.0.0.1", 0), CallbackHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        try:
            callback_port = int(server.server_address[1])
            self.state._openai_login_session = hub_server.OpenAIAccountLoginSession(
                id="session-test",
                process=SimpleNamespace(pid=9991, poll=lambda: None),
                container_name="container-test",
                started_at="2026-02-21T00:00:00Z",
                status="waiting_for_browser",
                callback_port=callback_port,
                callback_path="/auth/callback",
            )
            with patch("agent_hub.server._is_process_running", return_value=True):
                result = self.state.forward_openai_account_callback("code=abc&state=xyz", path="/auth/callback")
            self.assertTrue(result["forwarded"])
            self.assertEqual(result["status_code"], 200)
            self.assertEqual(captured["path"], "/auth/callback?code=abc&state=xyz")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=1.0)

    def test_parse_env_vars_rejects_openai_api_key(self) -> None:
        with self.assertRaises(HTTPException):
            hub_server._parse_env_vars(["OPENAI_API_KEY=sk-test-abcdef"])

    def test_start_chat_filters_reserved_openai_env_vars(self) -> None:
        project = self.state.add_project(
            repo_url="https://example.com/org/repo.git",
            default_branch="main",
            setup_script="echo setup",
        )
        chat = self.state.create_chat(
            project["id"],
            profile="",
            ro_mounts=[],
            rw_mounts=[],
            env_vars=["OPENAI_API_KEY=should_not_pass", "FOO=bar"],
            agent_args=[],
        )

        captured: dict[str, list[str]] = {}

        def fake_clone(_: hub_server.HubState, chat_obj: dict[str, str], __: dict[str, str]) -> Path:
            workspace = self.state.chat_workdir(chat_obj["id"])
            workspace.mkdir(parents=True, exist_ok=True)
            return workspace

        class DummyProc:
            pid = 4242

        def fake_spawn(_: hub_server.HubState, _chat_id: str, cmd: list[str]) -> DummyProc:
            captured["cmd"] = list(cmd)
            return DummyProc()

        with patch.object(hub_server.HubState, "_ensure_chat_clone", fake_clone), patch.object(
            hub_server.HubState, "_sync_checkout_to_remote", lambda *args, **kwargs: None
        ), patch(
            "agent_hub.server._docker_image_exists",
            return_value=True,
        ), patch.object(
            hub_server.HubState,
            "_spawn_chat_process",
            fake_spawn,
        ):
            self.state.start_chat(chat["id"])

        cmd = captured["cmd"]
        self.assertNotIn("OPENAI_API_KEY=should_not_pass", cmd)
        self.assertIn("FOO=bar", cmd)

    def test_start_chat_builds_cmd_with_mounts_env_and_repo_base_path(self) -> None:
        project = self.state.add_project(
            repo_url="https://example.com/org/repo.git",
            default_branch="main",
            base_image_mode="repo_path",
            base_image_value="docker/base",
            setup_script="echo setup",
        )
        chat = self.state.create_chat(
            project["id"],
            profile="fast",
            ro_mounts=[f"{self.host_ro}:/ro_data"],
            rw_mounts=[f"{self.host_rw}:/rw_data"],
            env_vars=["FOO=bar", "EMPTY="],
            agent_args=["--model", "gpt-5", "-c", 'model_reasoning_effort="high"'],
        )

        captured: dict[str, list[str]] = {}

        def fake_clone(_: hub_server.HubState, chat_obj: dict[str, str], __: dict[str, str]) -> Path:
            workspace = self.state.chat_workdir(chat_obj["id"])
            (workspace / "docker" / "base").mkdir(parents=True, exist_ok=True)
            return workspace

        class DummyProc:
            pid = 4242

        def fake_spawn(_: hub_server.HubState, _chat_id: str, cmd: list[str]) -> DummyProc:
            captured["cmd"] = list(cmd)
            return DummyProc()

        with patch.object(hub_server.HubState, "_ensure_chat_clone", fake_clone), patch.object(
            hub_server.HubState, "_sync_checkout_to_remote", lambda *args, **kwargs: None
        ), patch(
            "agent_hub.server._docker_image_exists",
            return_value=True,
        ), patch.object(
            hub_server.HubState,
            "_spawn_chat_process",
            fake_spawn,
        ):
            self.state.start_chat(chat["id"])

        cmd = captured["cmd"]
        workspace = self.state.chat_workdir(chat["id"])
        self.assertIn("--base", cmd)
        self.assertIn(str(workspace / "docker" / "base"), cmd)
        self.assertIn("--credentials-file", cmd)
        self.assertIn(str(self.state.openai_credentials_file), cmd)
        self.assertIn("--ro-mount", cmd)
        self.assertIn(f"{self.host_ro}:/ro_data", cmd)
        self.assertIn("--rw-mount", cmd)
        self.assertIn(f"{self.host_rw}:/rw_data", cmd)
        self.assertIn("--env-var", cmd)
        self.assertIn("FOO=bar", cmd)
        self.assertIn("EMPTY=", cmd)
        self.assertIn("--snapshot-image-tag", cmd)
        self.assertIn(self.state._project_setup_snapshot_tag(project), cmd)
        self.assertIn("--", cmd)
        self.assertIn("--model", cmd)
        self.assertIn("gpt-5", cmd)
        self.assertIn('model_reasoning_effort="high"', cmd)

    def test_start_chat_rejects_base_path_outside_workspace(self) -> None:
        project = self.state.add_project(
            repo_url="https://example.com/org/repo.git",
            default_branch="main",
            base_image_mode="repo_path",
            base_image_value="../outside",
        )
        chat = self.state.create_chat(
            project["id"],
            profile="",
            ro_mounts=[],
            rw_mounts=[],
            env_vars=[],
            agent_args=[],
        )

        def fake_clone(_: hub_server.HubState, chat_obj: dict[str, str], __: dict[str, str]) -> Path:
            workspace = self.state.chat_workdir(chat_obj["id"])
            workspace.mkdir(parents=True, exist_ok=True)
            return workspace

        with patch.object(hub_server.HubState, "_ensure_chat_clone", fake_clone), patch.object(
            hub_server.HubState, "_sync_checkout_to_remote", lambda *args, **kwargs: None
        ):
            with self.assertRaises(HTTPException):
                self.state.start_chat(chat["id"])

    def test_create_and_start_chat_rejects_when_project_build_is_not_ready(self) -> None:
        project = self.state.add_project(
            repo_url="https://example.com/org/repo.git",
            default_branch="main",
            setup_script="echo setup",
        )
        state_data = self.state.load()
        state_data["projects"][project["id"]]["build_status"] = "building"
        state_data["projects"][project["id"]]["setup_snapshot_image"] = ""
        self.state.save(state_data)

        with self.assertRaises(HTTPException):
            self.state.create_and_start_chat(project["id"])

    def test_create_and_start_chat_passes_agent_args(self) -> None:
        project = self.state.add_project(
            repo_url="https://example.com/org/repo.git",
            default_branch="main",
            setup_script="echo setup",
        )
        captured: dict[str, object] = {}

        def fake_create(
            _: hub_server.HubState,
            project_id: str,
            profile: str | None,
            ro_mounts: list[str],
            rw_mounts: list[str],
            env_vars: list[str],
            agent_args: list[str] | None = None,
        ) -> dict[str, str]:
            captured["project_id"] = project_id
            captured["profile"] = profile
            captured["ro_mounts"] = list(ro_mounts)
            captured["rw_mounts"] = list(rw_mounts)
            captured["env_vars"] = list(env_vars)
            captured["agent_args"] = list(agent_args or [])
            return {"id": "chat-created"}

        def fake_start(_: hub_server.HubState, chat_id: str) -> dict[str, str]:
            captured["started_chat_id"] = chat_id
            return {"id": chat_id, "status": "running"}

        with patch.object(hub_server.HubState, "create_chat", fake_create), patch.object(
            hub_server.HubState, "start_chat", fake_start
        ):
            result = self.state.create_and_start_chat(
                project["id"],
                agent_args=["--model", "gpt-5.3-codex", "-c", 'model_reasoning_effort="high"'],
            )

        self.assertEqual(captured["project_id"], project["id"])
        self.assertEqual(captured["profile"], "")
        self.assertEqual(captured["agent_args"], ["--model", "gpt-5.3-codex", "-c", 'model_reasoning_effort="high"'])
        self.assertEqual(captured["started_chat_id"], "chat-created")
        self.assertEqual(result["id"], "chat-created")

    def test_start_chat_rejects_when_stored_snapshot_tag_is_stale(self) -> None:
        project = self.state.add_project(
            repo_url="https://example.com/org/repo.git",
            default_branch="main",
            setup_script="echo setup",
        )

        state_data = self.state.load()
        state_data["projects"][project["id"]]["setup_snapshot_image"] = "stale-snapshot-tag"
        self.state.save(state_data)

        chat = self.state.create_chat(
            project["id"],
            profile="",
            ro_mounts=[],
            rw_mounts=[],
            env_vars=[],
            agent_args=[],
        )

        with patch(
            "agent_hub.server._docker_image_exists",
            return_value=True,
        ):
            with self.assertRaises(HTTPException):
                self.state.start_chat(chat["id"])

    def test_clean_start_clears_chat_artifacts_and_preserves_projects(self) -> None:
        project = self.state.add_project(
            repo_url="https://example.com/org/repo.git",
            default_branch="main",
            setup_script="echo setup",
        )
        chat = self.state.create_chat(
            project["id"],
            profile="",
            ro_mounts=[],
            rw_mounts=[],
            env_vars=[],
            agent_args=[],
        )

        chat_workspace = self.state.chat_workdir(chat["id"])
        project_workspace = self.state.project_workdir(project["id"])
        chat_workspace.mkdir(parents=True, exist_ok=True)
        project_workspace.mkdir(parents=True, exist_ok=True)
        self.state.chat_log(chat["id"]).write_text("log", encoding="utf-8")

        state_before = self.state.load()
        state_before["projects"][project["id"]]["setup_snapshot_image"] = "project-snapshot"
        state_before["chats"][chat["id"]]["setup_snapshot_image"] = "chat-snapshot"
        self.state.save(state_before)

        with patch("agent_hub.server._docker_remove_images") as docker_rm:
            summary = self.state.clean_start()

        self.assertEqual(summary["cleared_chats"], 1)
        self.assertGreaterEqual(summary["projects_reset"], 1)
        self.assertEqual(summary["docker_images_requested"], 2)

        state_after = self.state.load()
        self.assertIn(project["id"], state_after["projects"])
        self.assertEqual(state_after["chats"], {})
        self.assertEqual(state_after["projects"][project["id"]]["setup_snapshot_image"], "")
        self.assertEqual(state_after["projects"][project["id"]]["build_status"], "pending")
        self.assertTrue(self.state.chat_dir.exists())
        self.assertTrue(self.state.project_dir.exists())
        self.assertTrue(self.state.log_dir.exists())
        self.assertEqual(list(self.state.chat_dir.iterdir()), [])
        self.assertEqual(list(self.state.project_dir.iterdir()), [])
        self.assertEqual(list(self.state.log_dir.iterdir()), [])

        docker_rm.assert_called_once()
        prefixes, tags = docker_rm.call_args[0]
        self.assertEqual(prefixes, ("agent-hub-setup-", "agent-base-"))
        self.assertIn("project-snapshot", tags)
        self.assertIn("chat-snapshot", tags)

    def test_ensure_project_setup_snapshot_builds_once(self) -> None:
        project = self.state.add_project(
            repo_url="https://example.com/org/repo.git",
            default_branch="main",
            setup_script="echo setup",
            base_image_mode="repo_path",
            base_image_value="docker/base",
            default_ro_mounts=[f"{self.host_ro}:/ro_data"],
            default_rw_mounts=[f"{self.host_rw}:/rw_data"],
            default_env_vars=["FOO=bar"],
        )
        workspace = self.tmp_path / "workspace"
        (workspace / "docker" / "base").mkdir(parents=True, exist_ok=True)

        executed: list[list[str]] = []

        def fake_run(cmd: list[str], cwd: Path | None = None, capture: bool = False, check: bool = True):
            del cwd, capture, check
            executed.append(list(cmd))
            class Dummy:
                returncode = 0
                stdout = ""
                stderr = ""
            return Dummy()

        with patch("agent_hub.server._docker_image_exists", side_effect=[False, True]), patch(
            "agent_hub.server._run", side_effect=fake_run
        ):
            first = self.state._ensure_project_setup_snapshot(workspace, project)
            second = self.state._ensure_project_setup_snapshot(workspace, project)

        self.assertEqual(first, second)
        self.assertEqual(len(executed), 1)
        cmd = executed[0]
        self.assertIn("agent_cli", cmd)
        self.assertIn("--prepare-snapshot-only", cmd)
        self.assertIn("--snapshot-image-tag", cmd)
        self.assertIn("--setup-script", cmd)
        self.assertIn("--credentials-file", cmd)
        self.assertIn(str(self.state.openai_credentials_file), cmd)

    def test_resize_terminal_sets_pty_size(self) -> None:
        runtime = hub_server.ChatRuntime(process=SimpleNamespace(pid=1), master_fd=42)
        with patch.object(hub_server.HubState, "_runtime_for_chat", return_value=runtime), patch(
            "agent_hub.server.fcntl.ioctl"
        ) as ioctl_mock:
            self.state.resize_terminal("chat-1", 120, 40)
        self.assertEqual(ioctl_mock.call_count, 1)

    def test_chat_workspace_uses_project_name_plus_chat_id(self) -> None:
        project = self.state.add_project(
            repo_url="https://example.com/org/repo.git",
            name="Demo Project",
            default_branch="main",
        )
        chat = self.state.create_chat(
            project["id"],
            profile="",
            ro_mounts=[],
            rw_mounts=[],
            env_vars=[],
            agent_args=[],
        )
        workspace = Path(chat["workspace"])
        self.assertEqual(workspace.name, f"Demo_Project_{chat['id']}")
        self.assertEqual(self.state.chat_workdir(chat["id"]), workspace)

    def test_close_chat_stops_runtime_and_keeps_workspace_and_chat_record(self) -> None:
        project = self.state.add_project(
            repo_url="https://example.com/org/repo.git",
            default_branch="main",
        )
        chat = self.state.create_chat(
            project["id"],
            profile="",
            ro_mounts=[],
            rw_mounts=[],
            env_vars=[],
            agent_args=[],
        )
        workspace = self.state.chat_workdir(chat["id"])
        workspace.mkdir(parents=True, exist_ok=True)
        (workspace / "sentinel.txt").write_text("data", encoding="utf-8")
        state_data = self.state.load()
        state_data["chats"][chat["id"]]["status"] = "running"
        state_data["chats"][chat["id"]]["pid"] = 9876
        self.state.save(state_data)

        with patch("agent_hub.server._stop_process") as stop_process, patch.object(
            hub_server.HubState, "_close_runtime"
        ) as close_runtime:
            result = self.state.close_chat(chat["id"])

        stop_process.assert_called_once_with(9876)
        close_runtime.assert_called_once_with(chat["id"])
        self.assertEqual(result["status"], "stopped")
        self.assertIsNone(result["pid"])
        self.assertTrue(workspace.exists())
        self.assertTrue((workspace / "sentinel.txt").exists())
        self.assertIn(chat["id"], self.state.load()["chats"])

    def test_state_payload_prunes_finished_chats(self) -> None:
        project = self.state.add_project(
            repo_url="https://example.com/org/repo.git",
            default_branch="main",
        )
        chat = self.state.create_chat(
            project["id"],
            profile="",
            ro_mounts=[],
            rw_mounts=[],
            env_vars=[],
            agent_args=[],
        )
        state_data = self.state.load()
        state_data["chats"][chat["id"]]["status"] = "running"
        state_data["chats"][chat["id"]]["pid"] = 424242
        self.state.save(state_data)

        with patch("agent_hub.server._is_process_running", return_value=False), patch(
            "agent_hub.server._stop_process"
        ):
            payload = self.state.state_payload()

        self.assertEqual(payload["chats"], [])
        self.assertNotIn(chat["id"], self.state.load()["chats"])

    def test_state_payload_keeps_new_stopped_chat(self) -> None:
        project = self.state.add_project(
            repo_url="https://example.com/org/repo.git",
            default_branch="main",
        )
        chat = self.state.create_chat(
            project["id"],
            profile="",
            ro_mounts=[],
            rw_mounts=[],
            env_vars=[],
            agent_args=[],
        )
        with patch("agent_hub.server._is_process_running", return_value=False):
            payload = self.state.state_payload()
        self.assertEqual(len(payload["chats"]), 1)
        self.assertEqual(payload["chats"][0]["id"], chat["id"])
        self.assertEqual(payload["chats"][0]["status"], "stopped")
        self.assertIn(chat["id"], self.state.load()["chats"])

    def test_state_payload_sets_chat_display_name_and_subtitle(self) -> None:
        project = self.state.add_project(
            repo_url="https://example.com/org/repo.git",
            default_branch="main",
        )
        chat = self.state.create_chat(
            project["id"],
            profile="",
            ro_mounts=[],
            rw_mounts=[],
            env_vars=[],
            agent_args=[],
        )
        self.state.chat_log(chat["id"]).write_text(
            "Tip: example\n> how do i run tests?\nUse uv run python -m unittest discover -s tests -v\n",
            encoding="utf-8",
        )
        state_data = self.state.load()
        state_data["chats"][chat["id"]]["title_cached"] = "Run python unit tests"
        state_data["chats"][chat["id"]]["title_status"] = "ready"
        state_data["chats"][chat["id"]]["status"] = "running"
        state_data["chats"][chat["id"]]["pid"] = 1111
        self.state.save(state_data)

        with patch("agent_hub.server._is_process_running", return_value=True):
            payload = self.state.state_payload()

        chat_payload = next(item for item in payload["chats"] if item["id"] == chat["id"])
        self.assertEqual(chat_payload["display_name"], "Run python unit tests")
        self.assertTrue(chat_payload["display_subtitle"].startswith("Use uv run python -m unittest"))

    def test_write_terminal_input_records_prompt_only_on_submit(self) -> None:
        project = self.state.add_project(
            repo_url="https://example.com/org/repo.git",
            default_branch="main",
        )
        chat = self.state.create_chat(
            project["id"],
            profile="",
            ro_mounts=[],
            rw_mounts=[],
            env_vars=[],
            agent_args=[],
        )
        runtime = hub_server.ChatRuntime(process=SimpleNamespace(pid=1234), master_fd=42)

        with patch.object(hub_server.HubState, "_runtime_for_chat", return_value=runtime), patch(
            "agent_hub.server.os.write", return_value=1
        ), patch.object(
            hub_server.HubState, "_schedule_chat_title_generation"
        ) as schedule_title:
            self.state.write_terminal_input(chat["id"], "fix flaky login tests")
            schedule_title.assert_not_called()
            self.state.write_terminal_input(chat["id"], "\r")
            schedule_title.assert_called_once_with(chat["id"])

        updated = self.state.load()["chats"][chat["id"]]
        self.assertEqual(updated["title_user_prompts"][-1], "fix flaky login tests")

    def test_write_terminal_input_treats_application_keypad_enter_as_submit(self) -> None:
        project = self.state.add_project(
            repo_url="https://example.com/org/repo.git",
            default_branch="main",
        )
        chat = self.state.create_chat(
            project["id"],
            profile="",
            ro_mounts=[],
            rw_mounts=[],
            env_vars=[],
            agent_args=[],
        )
        runtime = hub_server.ChatRuntime(process=SimpleNamespace(pid=1234), master_fd=42)

        with patch.object(hub_server.HubState, "_runtime_for_chat", return_value=runtime), patch(
            "agent_hub.server.os.write", return_value=1
        ), patch.object(
            hub_server.HubState, "_schedule_chat_title_generation"
        ) as schedule_title:
            self.state.write_terminal_input(chat["id"], "summarize deploy failures")
            schedule_title.assert_not_called()
            self.state.write_terminal_input(chat["id"], "\x1bOM")
            schedule_title.assert_called_once_with(chat["id"])

        updated = self.state.load()["chats"][chat["id"]]
        self.assertEqual(updated["title_user_prompts"][-1], "summarize deploy failures")

    def test_write_terminal_input_ignores_terminal_control_payload(self) -> None:
        project = self.state.add_project(
            repo_url="https://example.com/org/repo.git",
            default_branch="main",
        )
        chat = self.state.create_chat(
            project["id"],
            profile="",
            ro_mounts=[],
            rw_mounts=[],
            env_vars=[],
            agent_args=[],
        )
        runtime = hub_server.ChatRuntime(process=SimpleNamespace(pid=1234), master_fd=42)
        control_payload = "\x1b]10;rgb:e7e7/eded/f7f7\x1b\\\x1b]11;rgb:0b0b/1010/1818\x1b\\\r"

        with patch.object(hub_server.HubState, "_runtime_for_chat", return_value=runtime), patch(
            "agent_hub.server.os.write", return_value=1
        ), patch.object(
            hub_server.HubState, "_schedule_chat_title_generation"
        ) as schedule_title:
            self.state.write_terminal_input(chat["id"], control_payload)
            schedule_title.assert_not_called()

        updated = self.state.load()["chats"][chat["id"]]
        self.assertEqual(updated.get("title_user_prompts"), [])

    def test_generate_and_store_chat_title_uses_openai_once_per_prompt_fingerprint(self) -> None:
        project = self.state.add_project(
            repo_url="https://example.com/org/repo.git",
            default_branch="main",
        )
        chat = self.state.create_chat(
            project["id"],
            profile="",
            ro_mounts=[],
            rw_mounts=[],
            env_vars=[],
            agent_args=[],
        )
        state_data = self.state.load()
        state_data["chats"][chat["id"]]["title_user_prompts"] = ["first prompt", "second prompt"]
        self.state.save(state_data)

        with patch("agent_hub.server._read_openai_api_key", return_value="sk-test"), patch(
            "agent_hub.server._openai_generate_chat_title",
            return_value="Fix flaky login tests in auth flow",
        ) as generate_title:
            self.state._generate_and_store_chat_title(chat["id"])
            self.state._generate_and_store_chat_title(chat["id"])

        self.assertEqual(generate_title.call_count, 1)
        updated = self.state.load()["chats"][chat["id"]]
        self.assertEqual(updated["title_cached"], "Fix flaky login tests in auth flow")
        self.assertEqual(updated["title_source"], "openai")
        self.assertEqual(updated["title_status"], "ready")
        self.assertEqual(updated["title_error"], "")
        self.assertTrue(updated["title_prompt_fingerprint"])

    def test_generate_and_store_chat_title_records_openai_error(self) -> None:
        project = self.state.add_project(
            repo_url="https://example.com/org/repo.git",
            default_branch="main",
        )
        chat = self.state.create_chat(
            project["id"],
            profile="",
            ro_mounts=[],
            rw_mounts=[],
            env_vars=[],
            agent_args=[],
        )
        state_data = self.state.load()
        state_data["chats"][chat["id"]]["title_user_prompts"] = ["debug websocket reconnect issue"]
        self.state.save(state_data)

        with patch("agent_hub.server._read_openai_api_key", return_value="sk-test"), patch(
            "agent_hub.server._openai_generate_chat_title",
            side_effect=RuntimeError("OpenAI title generation failed"),
        ):
            self.state._generate_and_store_chat_title(chat["id"])

        updated = self.state.load()["chats"][chat["id"]]
        self.assertEqual(updated["title_status"], "error")
        self.assertEqual(updated["title_source"], "openai")
        self.assertIn("OpenAI title generation failed", updated["title_error"])

    def test_generate_and_store_chat_title_records_missing_api_key_error(self) -> None:
        project = self.state.add_project(
            repo_url="https://example.com/org/repo.git",
            default_branch="main",
        )
        chat = self.state.create_chat(
            project["id"],
            profile="",
            ro_mounts=[],
            rw_mounts=[],
            env_vars=[],
            agent_args=[],
        )
        state_data = self.state.load()
        state_data["chats"][chat["id"]]["title_user_prompts"] = ["build a release checklist"]
        self.state.save(state_data)

        with patch("agent_hub.server._read_openai_api_key", return_value=""):
            self.state._generate_and_store_chat_title(chat["id"])

        updated = self.state.load()["chats"][chat["id"]]
        self.assertEqual(updated["title_status"], "error")
        self.assertEqual(updated["title_source"], "openai")
        self.assertIn("OpenAI API key is not configured", updated["title_error"])

    def test_state_payload_does_not_call_openai_title_generation_from_log_changes(self) -> None:
        project = self.state.add_project(
            repo_url="https://example.com/org/repo.git",
            default_branch="main",
        )
        chat = self.state.create_chat(
            project["id"],
            profile="",
            ro_mounts=[],
            rw_mounts=[],
            env_vars=[],
            agent_args=[],
        )
        self.state.chat_log(chat["id"]).write_text(
            "> refine the Dockerfile caching strategy\nassistant output keeps changing...\n",
            encoding="utf-8",
        )

        with patch("agent_hub.server._openai_generate_chat_title") as generate_title:
            payload = self.state.state_payload()

        self.assertEqual(generate_title.call_count, 0)
        chat_payload = next(item for item in payload["chats"] if item["id"] == chat["id"])
        self.assertEqual(chat_payload["display_name"], chat["name"])

    def test_state_payload_discards_cached_terminal_control_title(self) -> None:
        project = self.state.add_project(
            repo_url="https://example.com/org/repo.git",
            default_branch="main",
        )
        chat = self.state.create_chat(
            project["id"],
            profile="",
            ro_mounts=[],
            rw_mounts=[],
            env_vars=[],
            agent_args=[],
        )
        state_data = self.state.load()
        state_data["chats"][chat["id"]]["title_cached"] = "]10;rgb:e7e7/eded/f7f7\\"
        state_data["chats"][chat["id"]]["title_user_prompts"] = ["implement auth retry logic"]
        self.state.save(state_data)

        payload = self.state.state_payload()
        chat_payload = next(item for item in payload["chats"] if item["id"] == chat["id"])
        self.assertEqual(chat_payload["display_name"], chat["name"])

    def test_shutdown_stops_running_chats_and_persists_state(self) -> None:
        project = self.state.add_project(
            repo_url="https://example.com/org/repo.git",
            default_branch="main",
        )
        running_chat = self.state.create_chat(
            project["id"],
            profile="",
            ro_mounts=[],
            rw_mounts=[],
            env_vars=[],
            agent_args=[],
        )
        stopped_chat = self.state.create_chat(
            project["id"],
            profile="",
            ro_mounts=[],
            rw_mounts=[],
            env_vars=[],
            agent_args=[],
        )

        state_data = self.state.load()
        state_data["chats"][running_chat["id"]]["status"] = "running"
        state_data["chats"][running_chat["id"]]["pid"] = 5001
        state_data["chats"][stopped_chat["id"]]["status"] = "stopped"
        state_data["chats"][stopped_chat["id"]]["pid"] = None
        self.state.save(state_data)

        with patch.object(hub_server.HubState, "_close_runtime"), patch(
            "agent_hub.server._is_process_running",
            side_effect=lambda pid: pid == 5001,
        ), patch(
            "agent_hub.server._stop_processes",
            return_value=1,
        ) as stop_many:
            summary = self.state.shutdown()

        self.assertEqual(summary["stopped_chats"], 1)
        self.assertEqual(summary["closed_chats"], 1)
        stop_many.assert_called_once_with([5001], timeout_seconds=4.0)

        post = self.state.load()
        self.assertNotIn(running_chat["id"], post["chats"])
        self.assertIn(stopped_chat["id"], post["chats"])


class CliEnvVarTests(unittest.TestCase):
    def test_parse_env_var_valid(self) -> None:
        self.assertEqual(image_cli._parse_env_var("FOO=bar", "--env-var"), "FOO=bar")
        self.assertEqual(image_cli._parse_env_var("EMPTY=", "--env-var"), "EMPTY=")

    def test_parse_env_var_invalid(self) -> None:
        with self.assertRaises(Exception):
            image_cli._parse_env_var("NO_EQUALS", "--env-var")

    def test_snapshot_commit_resets_entrypoint_and_cmd(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            project = tmp_path / "project"
            project.mkdir(parents=True, exist_ok=True)
            config = tmp_path / "agent.config.toml"
            config.write_text("model = 'test'\n", encoding="utf-8")

            commands: list[list[str]] = []

            def fake_run(cmd: list[str], cwd: Path | None = None) -> None:
                del cwd
                commands.append(list(cmd))

            runner = CliRunner()
            with patch("agent_cli.cli.shutil.which", return_value="/usr/bin/docker"), patch(
                "agent_cli.cli._read_openai_api_key", return_value=None
            ), patch(
                "agent_cli.cli._docker_image_exists", return_value=False
            ), patch(
                "agent_cli.cli._docker_rm_force", return_value=None
            ), patch(
                "agent_cli.cli._run", side_effect=fake_run
            ):
                result = runner.invoke(
                    image_cli.main,
                    [
                        "--project",
                        str(project),
                        "--config-file",
                        str(config),
                        "--snapshot-image-tag",
                        "snapshot:test",
                        "--setup-script",
                        "echo hello",
                        "--prepare-snapshot-only",
                    ],
                )

            self.assertEqual(result.exit_code, 0, msg=result.output)
            setup_cmd = next((cmd for cmd in commands if len(cmd) >= 2 and cmd[:2] == ["docker", "run"]), None)
            self.assertIsNotNone(setup_cmd)
            assert setup_cmd is not None
            self.assertIn("--entrypoint", setup_cmd)
            self.assertIn("bash", setup_cmd)
            setup_script = setup_cmd[-1]
            self.assertIn("git config --system --add safe.directory '*'", setup_script)
            self.assertIn('chown -R "${LOCAL_UID}:${LOCAL_GID}" "${CONTAINER_PROJECT_PATH}" || true', setup_script)
            commit_cmd = next((cmd for cmd in commands if len(cmd) >= 3 and cmd[0:2] == ["docker", "commit"]), None)
            self.assertIsNotNone(commit_cmd)
            assert commit_cmd is not None
            self.assertIn("--change", commit_cmd)
            self.assertIn('ENTRYPOINT ["/usr/local/bin/docker-entrypoint.py"]', commit_cmd)
            self.assertIn('CMD ["codex"]', commit_cmd)

    def test_cached_snapshot_skips_runtime_build(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            project = tmp_path / "project"
            project.mkdir(parents=True, exist_ok=True)
            config = tmp_path / "agent.config.toml"
            config.write_text("model = 'test'\n", encoding="utf-8")

            commands: list[list[str]] = []

            def fake_run(cmd: list[str], cwd: Path | None = None) -> None:
                del cwd
                commands.append(list(cmd))

            runner = CliRunner()
            with patch("agent_cli.cli.shutil.which", return_value="/usr/bin/docker"), patch(
                "agent_cli.cli._read_openai_api_key", return_value=None
            ), patch(
                "agent_cli.cli._docker_image_exists", return_value=True
            ), patch(
                "agent_cli.cli._run", side_effect=fake_run
            ):
                result = runner.invoke(
                    image_cli.main,
                    [
                        "--project",
                        str(project),
                        "--config-file",
                        str(config),
                        "--snapshot-image-tag",
                        "snapshot:test",
                        "--prepare-snapshot-only",
                    ],
                )

            self.assertEqual(result.exit_code, 0, msg=result.output)
            self.assertEqual(commands, [])

    def test_agent_hub_main_clean_start_invokes_state_cleanup(self) -> None:
        runner = CliRunner()
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            data_dir = tmp_path / "hub"
            config = tmp_path / "agent.config.toml"
            config.write_text("model = 'test'\n", encoding="utf-8")

            with patch("agent_hub.server.uvicorn.run", return_value=None), patch.object(
                hub_server.HubState,
                "clean_start",
                return_value={
                    "stopped_chats": 0,
                    "cleared_chats": 0,
                    "projects_reset": 0,
                    "docker_images_requested": 0,
                },
            ) as clean_patch:
                result = runner.invoke(
                    hub_server.main,
                    [
                        "--data-dir",
                        str(data_dir),
                        "--config-file",
                        str(config),
                        "--no-frontend-build",
                        "--clean-start",
                    ],
                )

            self.assertEqual(result.exit_code, 0, msg=result.output)
            self.assertEqual(clean_patch.call_count, 1)
            self.assertIn("Clean start completed", result.output)


if __name__ == "__main__":
    unittest.main()
