from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path


class IntegrationSuiteSelectorTests(unittest.TestCase):
    def test_selector_returns_mapped_suite_for_server_change(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        selector = repo_root / "tools" / "testing" / "select_integration_suites.py"
        result = subprocess.run(
            [
                sys.executable,
                str(selector),
                "--changed-file",
                "src/agent_hub/server.py",
            ],
            check=False,
            capture_output=True,
            text=True,
            cwd=repo_root,
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        suites = [line.strip() for line in result.stdout.splitlines() if line.strip()]
        self.assertIn("tests/integration/test_agent_tools_ack_routes.py", suites)
        self.assertIn("tests/integration/test_hub_chat_lifecycle_api.py", suites)

    def test_selector_returns_default_suite_when_no_changes(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        selector = repo_root / "tools" / "testing" / "select_integration_suites.py"
        result = subprocess.run(
            [
                sys.executable,
                str(selector),
            ],
            check=False,
            capture_output=True,
            text=True,
            cwd=repo_root,
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        suites = [line.strip() for line in result.stdout.splitlines() if line.strip()]
        self.assertEqual(suites, ["tests/integration/test_agent_tools_ack_routes.py"])
