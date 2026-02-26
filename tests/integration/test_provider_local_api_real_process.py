from __future__ import annotations

import json
import socket
import subprocess
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError

import pytest

import sys

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import agent_hub.server as hub_server

HELPERS_DIR = Path(__file__).resolve().parent
if str(HELPERS_DIR) not in sys.path:
    sys.path.insert(0, str(HELPERS_DIR))

from helpers import insert_ready_project
from test_provider_local_e2e import LocalForgeServer, _seed_bare_repo


def _run(cmd: list[str], *, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=str(cwd) if cwd is not None else None,
        check=False,
        text=True,
        capture_output=True,
    )


def _docker_daemon_ready() -> bool:
    probe = _run(["docker", "info", "--format", "{{.ServerVersion}}"])
    return probe.returncode == 0 and bool(probe.stdout.strip())


def _free_port() -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])
    finally:
        sock.close()


def _api_json(method: str, url: str, *, payload: dict[str, Any] | None = None) -> tuple[int, dict[str, Any]]:
    import urllib.request

    data = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, method=method, headers=headers, data=data)
    try:
        with urllib.request.urlopen(req, timeout=20.0) as response:
            body = response.read().decode("utf-8", errors="replace")
            parsed = json.loads(body) if body.strip() else {}
            if not isinstance(parsed, dict):
                parsed = {"value": parsed}
            return int(response.status), parsed
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        parsed = json.loads(body) if body.strip() else {}
        if not isinstance(parsed, dict):
            parsed = {"value": parsed}
        return int(exc.code), parsed


def _wait_for_state(base_url: str, *, timeout_sec: float = 30.0) -> dict[str, Any]:
    deadline = time.time() + timeout_sec
    last_status = 0
    last_payload: dict[str, Any] = {}
    while time.time() < deadline:
        try:
            status, payload = _api_json("GET", f"{base_url}/api/state")
            last_status = status
            last_payload = payload
            if status == 200:
                return payload
        except Exception as exc:  # pragma: no cover - startup timing path
            last_payload = {"error": str(exc)}
        time.sleep(0.2)
    raise AssertionError(f"hub state unavailable: status={last_status} payload={last_payload}")


def test_local_forge_provider_flow_over_real_hub_api(integration_tmp_dir: Path) -> None:
    if not _docker_daemon_ready():
        pytest.skip("docker daemon is unavailable")

    token = "glpat_local_test_token"
    repos_root = integration_tmp_dir / "forge-repos"
    bare_repo = repos_root / "repo.git"
    _seed_bare_repo(bare_repo)

    forge = LocalForgeServer(
        repo_root=repos_root,
        token=token,
        username="gitlab-user",
        email="gitlab-user@example.com",
        display_name="GitLab User",
        account_id=44,
    )
    forge.start()

    data_dir = integration_tmp_dir / "hub-data"
    config_file = integration_tmp_dir / "agent.config.toml"
    config_file.write_text("model = 'test'\n", encoding="utf-8")

    seed_state = hub_server.HubState(data_dir, config_file)
    seeded_project = insert_ready_project(
        seed_state,
        project_id="project-provider-real",
        name="project-provider-real",
        repo_url=f"{forge.base_url}/repo.git",
        default_branch="master",
    )
    snapshot_tag = seed_state._project_setup_snapshot_tag(seeded_project)

    tag = _run(["docker", "tag", "alpine:3.20", snapshot_tag])
    if tag.returncode != 0:
        pull = _run(["docker", "pull", "alpine:3.20"])
        assert pull.returncode == 0, pull.stderr or pull.stdout
        tag = _run(["docker", "tag", "alpine:3.20", snapshot_tag])
        assert tag.returncode == 0, tag.stderr or tag.stdout

    port = _free_port()
    base_url = f"http://127.0.0.1:{port}"
    process = subprocess.Popen(
        [
            "uv",
            "run",
            "agent_hub",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--data-dir",
            str(data_dir),
            "--config-file",
            str(config_file),
            "--no-frontend-build",
        ],
        cwd=str(ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )

    try:
        _wait_for_state(base_url)

        status_auth, payload_auth = _api_json(
            "POST",
            f"{base_url}/api/settings/auth/gitlab-tokens/connect",
            payload={"personal_access_token": token, "host": forge.base_url},
        )
        assert status_auth == 200, payload_auth
        status_auth_state, payload_auth_state = _api_json("GET", f"{base_url}/api/settings/auth")
        assert status_auth_state == 200, payload_auth_state
        credential_catalog = payload_auth_state.get("credential_catalog")
        assert isinstance(credential_catalog, list), payload_auth_state
        gitlab_credentials = [
            entry for entry in credential_catalog if isinstance(entry, dict) and str(entry.get("provider") or "") == hub_server.GIT_PROVIDER_GITLAB
        ]
        assert gitlab_credentials, payload_auth_state

        status_create, payload_create = _api_json(
            "POST",
            f"{base_url}/api/projects",
            payload={
                "repo_url": f"{forge.base_url}/repo.git",
                "default_branch": "master",
                "setup_script": "",
            },
        )
        assert status_create == 200, payload_create
        created_project = payload_create.get("project")
        assert isinstance(created_project, dict)
        assert created_project.get("id")

        project_id = str(seeded_project["id"])
        status_binding_get, payload_binding_get = _api_json("GET", f"{base_url}/api/projects/{project_id}/credential-binding")
        assert status_binding_get == 200, payload_binding_get
        available = payload_binding_get.get("available_credentials")
        assert isinstance(available, list) and available
        first_credential = available[0]
        assert isinstance(first_credential, dict)
        credential_id = str(first_credential.get("credential_id") or "")
        assert credential_id

        status_binding_set, payload_binding_set = _api_json(
            "POST",
            f"{base_url}/api/projects/{project_id}/credential-binding",
            payload={"mode": "single", "credential_ids": [credential_id]},
        )
        assert status_binding_set == 200, payload_binding_set
        binding_payload = payload_binding_set.get("binding")
        assert isinstance(binding_payload, dict)
        assert binding_payload.get("mode") == "single"

        status_start_1, payload_start_1 = _api_json(
            "POST",
            f"{base_url}/api/projects/{project_id}/chats/start",
            payload={"request_id": "req-provider-1", "agent_type": "codex", "agent_args": []},
        )
        assert status_start_1 == 200, payload_start_1
        chat_1 = payload_start_1.get("chat")
        assert isinstance(chat_1, dict)

        status_start_2, payload_start_2 = _api_json(
            "POST",
            f"{base_url}/api/projects/{project_id}/chats/start",
            payload={"request_id": "req-provider-1", "agent_type": "codex", "agent_args": []},
        )
        assert status_start_2 == 200, payload_start_2
        chat_2 = payload_start_2.get("chat")
        assert isinstance(chat_2, dict)
        assert str(chat_1.get("id") or "") == str(chat_2.get("id") or "")

        chat_id = str(chat_1.get("id") or "")
        assert chat_id
        status_close, payload_close = _api_json("POST", f"{base_url}/api/chats/{chat_id}/close", payload={})
        assert status_close == 200, payload_close
        closed_chat = payload_close.get("chat")
        assert isinstance(closed_chat, dict)
        assert closed_chat.get("status") == hub_server.CHAT_STATUS_STOPPED
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        forge.close()
        _run(["docker", "rmi", "-f", snapshot_tag])
