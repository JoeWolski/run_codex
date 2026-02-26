from __future__ import annotations

import asyncio
import json
import socket
import subprocess
import time
from pathlib import Path
from typing import Any

import pytest
import websockets

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


def _seed_local_repo(repo_root: Path) -> str:
    repo_root.mkdir(parents=True, exist_ok=True)
    bare_repo = repo_root / "origin.git"
    worktree = repo_root / "seed-worktree"
    worktree.mkdir(parents=True, exist_ok=True)

    init_bare = _run(["git", "init", "--bare", str(bare_repo)])
    assert init_bare.returncode == 0, init_bare.stderr or init_bare.stdout

    init_work = _run(["git", "init", "-b", "main"], cwd=worktree)
    assert init_work.returncode == 0, init_work.stderr or init_work.stdout

    (worktree / "README.md").write_text("seed\n", encoding="utf-8")
    add = _run(["git", "add", "README.md"], cwd=worktree)
    assert add.returncode == 0, add.stderr or add.stdout

    commit = _run(
        ["git", "-c", "user.name=Seed User", "-c", "user.email=seed@example.com", "commit", "-m", "seed"],
        cwd=worktree,
    )
    assert commit.returncode == 0, commit.stderr or commit.stdout

    remote = _run(["git", "remote", "add", "origin", str(bare_repo)], cwd=worktree)
    assert remote.returncode == 0, remote.stderr or remote.stdout

    push = _run(["git", "push", "origin", "HEAD:main"], cwd=worktree)
    assert push.returncode == 0, push.stderr or push.stdout

    return bare_repo.as_uri()


def _free_port() -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])
    finally:
        sock.close()


def _http_json(method: str, url: str, *, payload: dict[str, Any] | None = None) -> tuple[int, dict[str, Any]]:
    import urllib.request

    data = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, method=method, headers=headers, data=data)
    with urllib.request.urlopen(req, timeout=20.0) as resp:
        body = resp.read().decode("utf-8", errors="replace")
        parsed = json.loads(body) if body.strip() else {}
        return int(resp.status), parsed if isinstance(parsed, dict) else {"value": parsed}


def _wait_for_state(base_url: str, *, timeout_sec: float = 30.0) -> dict[str, Any]:
    deadline = time.time() + timeout_sec
    last_error = ""
    while time.time() < deadline:
        try:
            status, payload = _http_json("GET", f"{base_url}/api/state")
            if status == 200:
                return payload
        except Exception as exc:  # pragma: no cover - diagnostic path
            last_error = str(exc)
        time.sleep(0.2)
    raise AssertionError(f"hub did not become ready: {last_error}")


async def _read_first_event(base_url: str) -> dict[str, Any]:
    ws_url = base_url.replace("http://", "ws://", 1) + "/api/events"
    async with websockets.connect(ws_url, open_timeout=10.0, close_timeout=10.0) as ws:
        message = await asyncio.wait_for(ws.recv(), timeout=10.0)
    parsed = json.loads(str(message))
    assert isinstance(parsed, dict)
    return parsed


def test_real_hub_process_lifecycle_and_launch_profile(integration_tmp_dir: Path) -> None:
    if not _docker_daemon_ready():
        pytest.skip("docker daemon is unavailable")

    data_dir = integration_tmp_dir / "hub-data"
    config_file = integration_tmp_dir / "agent.config.toml"
    config_file.write_text("model = 'test'\n", encoding="utf-8")

    seed_state = hub_server.HubState(data_dir, config_file)
    repo_url = _seed_local_repo(integration_tmp_dir / "repos")
    project = insert_ready_project(
        seed_state,
        project_id="project-real",
        name="project-real",
        repo_url=repo_url,
    )
    project_id = str(project["id"])

    chat = seed_state.create_chat(
        project_id=project_id,
        profile="",
        ro_mounts=[],
        rw_mounts=[],
        env_vars=[],
        agent_args=[],
        agent_type=hub_server.AGENT_TYPE_CODEX,
    )
    chat_id = str(chat["id"])

    snapshot_tag = seed_state._project_setup_snapshot_tag(project)
    tag = _run(["docker", "tag", "alpine:3.20", snapshot_tag])
    if tag.returncode != 0:
        pull = _run(["docker", "pull", "alpine:3.20"])
        assert pull.returncode == 0, f"failed to pull alpine image: {pull.stderr or pull.stdout}"
        tag = _run(["docker", "tag", "alpine:3.20", snapshot_tag])
        assert tag.returncode == 0, f"failed to tag snapshot image: {tag.stderr or tag.stdout}"

    port = _free_port()
    base_url = f"http://127.0.0.1:{port}"

    command = [
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
    ]
    process = subprocess.Popen(command, cwd=str(ROOT), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)

    try:
        state_payload = _wait_for_state(base_url)
        assert "projects" in state_payload

        event = asyncio.run(_read_first_event(base_url))
        assert event.get("type") == hub_server.EVENT_TYPE_SNAPSHOT

        project_status, project_profile = _http_json("GET", f"{base_url}/api/projects/{project_id}/launch-profile")
        assert project_status == 200
        assert project_profile["launch_profile"]["mode"] == "project_snapshot"
        assert "--prepare-snapshot-only" in project_profile["launch_profile"]["command"]

        chat_status, chat_profile = _http_json("GET", f"{base_url}/api/chats/{chat_id}/launch-profile")
        assert chat_status == 200
        env_vars = chat_profile["launch_profile"]["env_vars"]
        assert any(str(item).startswith("AGENT_HUB_READY_ACK_GUID=") for item in env_vars)

        start_status, started = _http_json("POST", f"{base_url}/api/chats/{chat_id}/start", payload={})
        assert start_status == 200
        assert started["chat"]["status"] in {
            hub_server.CHAT_STATUS_RUNNING,
            hub_server.CHAT_STATUS_FAILED,
        }

        close_status, closed = _http_json("POST", f"{base_url}/api/chats/{chat_id}/close", payload={})
        assert close_status == 200
        assert closed["chat"]["status"] == hub_server.CHAT_STATUS_STOPPED
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        _run(["docker", "rmi", "-f", snapshot_tag])
