from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import sys

from click.testing import CliRunner
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import agent_hub.server as hub_server


class AgentToolsAckRouteIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.tmp.name)
        self.data_dir = self.tmp_path / "hub"
        self.config = self.tmp_path / "agent.config.toml"
        self.config.write_text("model = 'test'\n", encoding="utf-8")
        self.runner = CliRunner()

    def tearDown(self) -> None:
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
        self.assertEqual(result.exit_code, 0, msg=result.output)
        return uvicorn_run.call_args.args[0]

    def test_chat_ack_route_forwards_payload(self) -> None:
        app = self._build_app()
        ack_payload = {"chat_id": "chat-1", "guid": "guid-1", "stage": "container_bootstrapped", "acknowledged_at": "now", "meta": {}}
        with patch.object(hub_server.HubState, "acknowledge_agent_tools_chat_ready", return_value=ack_payload) as ack_method:
            with TestClient(app) as client:
                response = client.post(
                    "/api/chats/chat-1/agent-tools/ack",
                    headers={"authorization": "Bearer token-1"},
                    json={"guid": "guid-1", "stage": "container_bootstrapped", "meta": {}},
                )

        self.assertEqual(response.status_code, 200, msg=response.text)
        self.assertEqual(response.json(), {"ack": ack_payload})
        ack_method.assert_called_once_with(
            chat_id="chat-1",
            token="token-1",
            guid="guid-1",
            stage="container_bootstrapped",
            meta={},
        )

    def test_session_ack_route_forwards_payload(self) -> None:
        app = self._build_app()
        ack_payload = {"session_id": "session-1", "guid": "guid-1", "stage": "container_bootstrapped", "acknowledged_at": "now", "meta": {}}
        with patch.object(hub_server.HubState, "acknowledge_agent_tools_session_ready", return_value=ack_payload) as ack_method:
            with TestClient(app) as client:
                response = client.post(
                    "/api/agent-tools/sessions/session-1/ack",
                    headers={hub_server.AGENT_TOOLS_TOKEN_HEADER: "token-1"},
                    json={"guid": "guid-1", "stage": "container_bootstrapped", "meta": {}},
                )

        self.assertEqual(response.status_code, 200, msg=response.text)
        self.assertEqual(response.json(), {"ack": ack_payload})
        ack_method.assert_called_once_with(
            session_id="session-1",
            token="token-1",
            guid="guid-1",
            stage="container_bootstrapped",
            meta={},
        )
