from __future__ import annotations

import io
import json
import tempfile
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import sys

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import agent_hub.server as hub_server

REAL_UUID4 = uuid.uuid4

class _FakeProcess:
    def __init__(self, stdout_text: str = "") -> None:
        self.stdout = io.StringIO(stdout_text)

    def wait(self, timeout: float | None = None) -> int:
        del timeout
        return 0

    def kill(self) -> None:
        return


def test_temporary_auto_config_session_ack_and_cleanup(hub_state: hub_server.HubState) -> None:
    output_uuid = "a" * 32
    session_uuid = "b" * 32
    output_filename = f".agent-hub-auto-config-{output_uuid}.json"
    captured_ack: dict[str, str] = {}

    with tempfile.TemporaryDirectory(prefix="agent-hub-auto-config-", dir="/workspace/tmp") as tmp_dir:
        workspace = Path(tmp_dir)
        output_file = workspace / output_filename
        recommendation = {
            "base_image_mode": "tag",
            "base_image_value": hub_server.DEFAULT_AGENT_IMAGE,
            "setup_script": "echo auto-configured",
            "default_ro_mounts": [],
            "default_rw_mounts": [],
            "default_env_vars": [],
            "notes": "ok",
        }

        def fake_popen(cmd: list[str], **kwargs) -> _FakeProcess:
            del kwargs
            env_vars: dict[str, str] = {}
            for index, token in enumerate(cmd):
                if token != "--env-var" or index + 1 >= len(cmd):
                    continue
                entry = str(cmd[index + 1])
                if "=" not in entry:
                    continue
                key, value = entry.split("=", 1)
                env_vars[key] = value

            ready_guid = str(env_vars.get("AGENT_HUB_READY_ACK_GUID") or "")
            tools_token = str(env_vars.get("AGENT_HUB_AGENT_TOOLS_TOKEN") or "")
            tools_url = str(env_vars.get("AGENT_HUB_AGENT_TOOLS_URL") or "").rstrip("/")
            assert ready_guid
            assert tools_token
            assert tools_url.startswith("http")
            session_id = tools_url.rsplit("/", 1)[-1]
            assert session_id

            acknowledged = hub_state.acknowledge_agent_tools_session_ready(
                session_id=session_id,
                token=tools_token,
                guid=ready_guid,
                stage=hub_server.AGENT_READY_ACK_STAGE_AGENT_PROCESS_STARTED,
                meta={"source": "test"},
            )
            captured_ack.update(
                {
                    "session_id": str(acknowledged["session_id"]),
                    "guid": str(acknowledged["guid"]),
                    "stage": str(acknowledged["stage"]),
                    "acknowledged_at": str(acknowledged["acknowledged_at"]),
                }
            )
            output_file.write_text(json.dumps(recommendation), encoding="utf-8")
            return _FakeProcess(stdout_text="temporary auto-config completed\n")

        sequence = iter(
            [
                SimpleNamespace(hex=output_uuid),
                SimpleNamespace(hex=session_uuid),
            ]
        )

        def fake_uuid4() -> SimpleNamespace:
            try:
                return next(sequence)
            except StopIteration:
                return SimpleNamespace(hex=REAL_UUID4().hex)

        with patch("agent_hub.server.uuid.uuid4", side_effect=fake_uuid4), patch(
            "agent_hub.server.subprocess.Popen", side_effect=fake_popen
        ):
            result = hub_state._run_temporary_auto_config_chat(
                workspace=workspace,
                repo_url="https://example.test/repo.git",
                branch="main",
                agent_type=hub_server.AGENT_TYPE_CLAUDE,
                agent_args=[],
            )

        assert result["payload"]["setup_script"] == "echo auto-configured"
        assert captured_ack["session_id"] == session_uuid
        assert captured_ack["guid"]
        assert captured_ack["stage"] == hub_server.AGENT_READY_ACK_STAGE_AGENT_PROCESS_STARTED
        assert captured_ack["acknowledged_at"]
        assert not output_file.exists()
        with hub_state._agent_tools_sessions_lock:
            assert session_uuid not in hub_state._agent_tools_sessions
