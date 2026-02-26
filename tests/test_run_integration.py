from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def _run_runner(*args: str) -> subprocess.CompletedProcess[str]:
    repo_root = Path(__file__).resolve().parents[1]
    runner = repo_root / "tools" / "testing" / "run_integration.py"
    return subprocess.run(
        [sys.executable, str(runner), "--dry-run", *args],
        check=False,
        capture_output=True,
        text=True,
        cwd=repo_root,
    )


def test_runner_direct_mode_filters_to_direct_agent_cli_suites() -> None:
    output_path = Path("/workspace/tmp/agent-hub/run-integration-direct.json")
    result = _run_runner(
        "--mode",
        "direct-agent-cli",
        "--changed-file",
        "src/agent_hub/server.py",
        "--selection-output",
        str(output_path),
    )
    assert result.returncode == 0, result.stderr
    assert "Harness mode: direct-agent-cli" in result.stdout
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert "tests/integration/test_snapshot_builds.py" in payload["suites"]
    assert "tests/integration/test_hub_chat_lifecycle_api.py" not in payload["suites"]


def test_runner_hub_api_mode_filters_to_api_e2e_suites() -> None:
    output_path = Path("/workspace/tmp/agent-hub/run-integration-api.json")
    result = _run_runner(
        "--mode",
        "hub-api-e2e",
        "--changed-file",
        "src/agent_cli/cli.py",
        "--selection-output",
        str(output_path),
    )
    assert result.returncode == 0, result.stderr
    assert "Harness mode: hub-api-e2e" in result.stdout
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert "tests/integration/test_hub_chat_lifecycle_api.py" in payload["suites"]
    assert "tests/integration/test_agent_cli_runtime_ack.py" not in payload["suites"]
