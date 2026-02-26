from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def _run_selector(*args: str) -> subprocess.CompletedProcess[str]:
    repo_root = Path(__file__).resolve().parents[1]
    selector = repo_root / "tools" / "testing" / "select_integration_suites.py"
    return subprocess.run(
        [sys.executable, str(selector), *args],
        check=False,
        capture_output=True,
        text=True,
        cwd=repo_root,
    )


def test_selector_returns_mapped_suites_for_server_change() -> None:
    result = _run_selector("--changed-file", "src/agent_hub/server.py")
    assert result.returncode == 0, result.stderr
    suites = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    assert "tests/integration/test_chat_lifecycle_ready.py" in suites
    assert "tests/integration/test_hub_chat_lifecycle_api.py" in suites
    assert "tests/integration/test_provider_local_e2e.py" in suites


def test_selector_supports_changed_files_argument() -> None:
    result = _run_selector("--changed-files", "docker/agent_cli/docker-entrypoint.py", "src/agent_cli/cli.py")
    assert result.returncode == 0, result.stderr
    suites = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    assert "tests/integration/test_agent_cli_runtime_ack.py" in suites
    assert "tests/integration/test_snapshot_builds.py" in suites


def test_selector_unknown_path_falls_back_to_core_set() -> None:
    result = _run_selector("--changed-file", "docs/new-unmapped-file.md")
    assert result.returncode == 0, result.stderr
    suites = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    assert suites == [
        "tests/integration/test_agent_tools_ack_routes.py",
        "tests/integration/test_chat_lifecycle_ready.py",
        "tests/integration/test_hub_chat_lifecycle_api.py",
        "tests/integration/test_snapshot_builds.py",
    ]


def test_selector_json_output_contains_markers() -> None:
    result = _run_selector("--json", "--changed-file", "src/agent_hub/agent_tools_mcp.py")
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert "tests/integration/test_agent_tools_ack_routes.py" in payload["suites"]
    assert "integration.agent-tools" in payload["markers"]
