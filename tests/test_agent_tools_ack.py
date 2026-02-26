from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import sys

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import agent_hub.agent_tools_mcp as agent_tools_mcp
import agent_hub.server as hub_server


class AgentToolsAckTests(unittest.TestCase):
    def test_mcp_ack_tool_calls_ack_endpoint(self) -> None:
        with patch(
            "agent_hub.agent_tools_mcp._api_request",
            return_value={"ack": {"guid": "guid-123", "stage": "container_bootstrapped"}},
        ) as api_request:
            response = agent_tools_mcp._handle_tool_call(
                "ack",
                {
                    "guid": "guid-123",
                    "stage": "container_bootstrapped",
                    "meta": {"source": "test"},
                },
            )

        self.assertFalse(response.get("isError"))
        api_request.assert_called_once_with(
            "/ack",
            method="POST",
            payload={
                "guid": "guid-123",
                "stage": "container_bootstrapped",
                "meta": {"source": "test"},
            },
        )

    def test_mcp_ack_tool_requires_guid(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "requires a non-empty guid"):
            agent_tools_mcp._handle_tool_call("ack", {})

    def test_mcp_ack_tool_rejects_non_object_meta(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "meta must be an object"):
            agent_tools_mcp._handle_tool_call("ack", {"guid": "guid-123", "meta": "invalid"})


class HubStateAckTests(unittest.TestCase):
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

    def tearDown(self) -> None:
        state_obj = getattr(self, "state", None)
        if state_obj is not None:
            startup_thread = getattr(state_obj, "_startup_reconcile_thread", None)
            if startup_thread is not None and startup_thread.is_alive():
                startup_thread.join(timeout=2.0)
        self.schedule_patcher.stop()
        self.snapshot_patcher.stop()
        self.tmp.cleanup()

    def test_acknowledge_chat_ready_updates_state(self) -> None:
        project = self.state.add_project(
            repo_url="https://example.com/org/repo.git",
            default_branch="main",
        )
        chat = self.state.create_chat(
            project_id=project["id"],
            profile="",
            ro_mounts=[],
            rw_mounts=[],
            env_vars=[],
            agent_args=[],
            agent_type=hub_server.AGENT_TYPE_CODEX,
        )
        state_data = self.state.load()
        state_data["chats"][chat["id"]]["agent_tools_token_hash"] = hub_server._hash_agent_tools_token("test-token")
        state_data["chats"][chat["id"]]["ready_ack_guid"] = "ack-guid"
        self.state.save(state_data, reason="test_seed_ready_ack")

        ack = self.state.acknowledge_agent_tools_chat_ready(
            chat["id"],
            token="test-token",
            guid="ack-guid",
            stage="agent_process_started",
            meta={"provider": "codex"},
        )
        self.assertEqual(ack["chat_id"], chat["id"])
        self.assertEqual(ack["guid"], "ack-guid")
        self.assertEqual(ack["stage"], "agent_process_started")
        self.assertEqual(ack["meta"], {"provider": "codex"})

        refreshed = self.state.chat(chat["id"])
        self.assertIsNotNone(refreshed)
        assert refreshed is not None
        self.assertEqual(refreshed["ready_ack_guid"], "ack-guid")
        self.assertEqual(refreshed["ready_ack_stage"], "agent_process_started")
        self.assertTrue(refreshed["ready_ack_at"])
        self.assertEqual(refreshed["ready_ack_meta"], {"provider": "codex"})

    def test_acknowledge_session_ready_updates_state(self) -> None:
        session_id, token = self.state._create_agent_tools_session(repo_url="https://example.com/org/repo.git")
        guid = self.state.issue_agent_tools_session_ready_ack_guid(session_id)
        ack = self.state.acknowledge_agent_tools_session_ready(
            session_id=session_id,
            token=token,
            guid=guid,
            stage="container_bootstrapped",
            meta={"source": "entrypoint"},
        )
        self.assertEqual(ack["session_id"], session_id)
        self.assertEqual(ack["guid"], guid)
        self.assertEqual(ack["stage"], "container_bootstrapped")
        self.assertEqual(ack["meta"], {"source": "entrypoint"})
