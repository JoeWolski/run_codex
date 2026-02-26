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


class HubLifecycleApiIntegrationTests(unittest.TestCase):
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

    def test_state_route_returns_status_payload(self) -> None:
        app = self._build_app()
        with TestClient(app) as client:
            response = client.get("/api/state")
        self.assertEqual(response.status_code, 200, msg=response.text)
        payload = response.json()
        self.assertIn("projects", payload)
        self.assertIn("chats", payload)
        self.assertIn("settings", payload)
