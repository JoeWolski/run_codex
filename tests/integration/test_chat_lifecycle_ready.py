from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

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


def _fake_spawn(*_args, **_kwargs):
    return SimpleNamespace(pid=4242)


def _startable_chat(hub_state: hub_server.HubState, *, agent_type: str) -> dict[str, str]:
    project = insert_ready_project(hub_state, project_id=f"project-{agent_type}", name=f"proj-{agent_type}")
    chat = hub_state.create_chat(
        project_id=str(project["id"]),
        profile="",
        ro_mounts=[],
        rw_mounts=[],
        env_vars=[],
        agent_args=[],
        agent_type=agent_type,
    )
    return {"project_id": str(project["id"]), "chat_id": str(chat["id"])}


def test_chat_readiness_ack_lifecycle_fields_are_consistent(hub_state: hub_server.HubState) -> None:
    ids = _startable_chat(hub_state, agent_type=hub_server.AGENT_TYPE_CODEX)
    chat_id = ids["chat_id"]

    with patch("agent_hub.server._docker_image_exists", return_value=True), patch.object(
        hub_server.HubState, "_ensure_chat_clone", return_value=Path.cwd()
    ), patch.object(hub_server.HubState, "_sync_checkout_to_remote", return_value=None), patch.object(
        hub_server.HubState, "_prepare_chat_runtime_config", return_value=Path.cwd() / "runtime.toml"
    ), patch.object(hub_server.HubState, "_spawn_chat_process", side_effect=_fake_spawn):
        started = hub_state.start_chat(chat_id)

    assert started["status"] == hub_server.CHAT_STATUS_RUNNING
    assert isinstance(started["pid"], int)
    guid = str(started["ready_ack_guid"])
    assert guid
    assert started["ready_ack_at"] == ""

    token = "tok-1"
    state_data = hub_state.load()
    state_data["chats"][chat_id]["agent_tools_token_hash"] = hub_server._hash_agent_tools_token(token)
    hub_state.save(state_data, reason="test_seed_token")

    ack = hub_state.acknowledge_agent_tools_chat_ready(
        chat_id=chat_id,
        token=token,
        guid=guid,
        stage=hub_server.AGENT_READY_ACK_STAGE_AGENT_PROCESS_STARTED,
        meta={"provider": "codex"},
    )
    assert ack["guid"] == guid
    assert ack["stage"] == hub_server.AGENT_READY_ACK_STAGE_AGENT_PROCESS_STARTED
    assert ack["acknowledged_at"]

    refreshed = hub_state.state_payload()
    chat_payload = next(entry for entry in refreshed["chats"] if entry["id"] == chat_id)
    assert chat_payload["ready_ack_guid"] == guid
    assert chat_payload["ready_ack_stage"] == hub_server.AGENT_READY_ACK_STAGE_AGENT_PROCESS_STARTED
    assert chat_payload["ready_ack_at"] == ack["acknowledged_at"]

    closed = hub_state.close_chat(chat_id)
    assert closed["status"] == hub_server.CHAT_STATUS_STOPPED
    assert closed["pid"] is None


def test_chat_launch_profile_uses_production_command_builder(hub_state: hub_server.HubState) -> None:
    ids = _startable_chat(hub_state, agent_type=hub_server.AGENT_TYPE_CODEX)
    chat_id = ids["chat_id"]

    with patch.object(hub_server.HubState, "_ensure_chat_clone", return_value=Path.cwd()), patch.object(
        hub_server.HubState, "_sync_checkout_to_remote", return_value=None
    ), patch.object(hub_server.HubState, "_prepare_chat_runtime_config", return_value=Path.cwd() / "runtime.toml"):
        launch_profile = hub_state.chat_launch_profile(chat_id, resume=False)

    assert launch_profile["mode"] == "chat_start"
    assert launch_profile["agent_type"] == hub_server.AGENT_TYPE_CODEX
    assert launch_profile["prepare_snapshot_only"] is False
    assert launch_profile["snapshot_tag"]
    assert launch_profile["runtime_config_file"].endswith("runtime.toml")
    assert launch_profile["command"][:4] == ["uv", "run", "--project", str(hub_server._repo_root())]
    assert any(entry.endswith(":/workspace/tmp") for entry in launch_profile["rw_mounts"])
    assert any(entry.startswith("AGENT_HUB_TMP_HOST_PATH=") for entry in launch_profile["env_vars"])
    assert any(entry.startswith("AGENT_HUB_READY_ACK_GUID=") for entry in launch_profile["env_vars"])


def test_project_snapshot_launch_profile_uses_prepare_snapshot_only(hub_state: hub_server.HubState) -> None:
    project = insert_ready_project(hub_state, project_id="project-snapshot")
    repo = Path.cwd()

    with patch.object(hub_server.HubState, "_ensure_project_clone", return_value=repo), patch.object(
        hub_server.HubState, "_sync_checkout_to_remote", return_value=None
    ), patch("agent_hub.server._run_for_repo", return_value=SimpleNamespace(stdout="deadbeef\n")):
        launch_profile = hub_state.project_snapshot_launch_profile(str(project["id"]))

    assert launch_profile["mode"] == "project_snapshot"
    assert launch_profile["prepare_snapshot_only"] is True
    assert "setup" in launch_profile["runtime_image"]
    assert "--prepare-snapshot-only" in launch_profile["command"]
    assert any(entry.endswith(":/workspace/tmp") for entry in launch_profile["rw_mounts"])
    assert any(entry.startswith("AGENT_HUB_TMP_HOST_PATH=") for entry in launch_profile["env_vars"])
