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


@pytest.mark.parametrize(
    "agent_type",
    [
        hub_server.AGENT_TYPE_CODEX,
        hub_server.AGENT_TYPE_CLAUDE,
        hub_server.AGENT_TYPE_GEMINI,
    ],
)
def test_all_supported_agents_reach_ack_ready_state(agent_type: str, hub_state: hub_server.HubState) -> None:
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
    chat_id = str(chat["id"])

    with patch("agent_hub.server._docker_image_exists", return_value=True), patch.object(
        hub_server.HubState, "_ensure_chat_clone", return_value=Path.cwd()
    ), patch.object(hub_server.HubState, "_sync_checkout_to_remote", return_value=None), patch.object(
        hub_server.HubState, "_prepare_chat_runtime_config", return_value=Path.cwd() / f"{agent_type}.toml"
    ), patch.object(hub_server.HubState, "_spawn_chat_process", return_value=SimpleNamespace(pid=31337)):
        started = hub_state.start_chat(chat_id)

    assert started["status"] == hub_server.CHAT_STATUS_RUNNING
    assert started["agent_type"] == agent_type
    assert started["ready_ack_guid"]

    token = f"token-{agent_type}"
    state_data = hub_state.load()
    state_data["chats"][chat_id]["agent_tools_token_hash"] = hub_server._hash_agent_tools_token(token)
    hub_state.save(state_data, reason=f"test_seed_{agent_type}_token")

    ack = hub_state.acknowledge_agent_tools_chat_ready(
        chat_id=chat_id,
        token=token,
        guid=started["ready_ack_guid"],
        stage=hub_server.AGENT_READY_ACK_STAGE_AGENT_PROCESS_STARTED,
        meta={"agent_type": agent_type},
    )
    assert ack["stage"] == hub_server.AGENT_READY_ACK_STAGE_AGENT_PROCESS_STARTED
    assert ack["acknowledged_at"]

    closed = hub_state.close_chat(chat_id)
    assert closed["status"] == hub_server.CHAT_STATUS_STOPPED
    assert closed["pid"] is None
