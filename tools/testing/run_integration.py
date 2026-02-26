#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SELECTOR = REPO_ROOT / "tools" / "testing" / "select_integration_suites.py"
PREFLIGHT = REPO_ROOT / "tools" / "testing" / "preflight_integration_env.py"
DEFAULT_SELECTION_OUTPUT = Path("/workspace/tmp/agent-hub/integration-suite-selection.json")
MODE_ALL = "all"
MODE_DIRECT_AGENT_CLI = "direct-agent-cli"
MODE_HUB_API_E2E = "hub-api-e2e"
SUPPORTED_MODES = (
    MODE_ALL,
    MODE_DIRECT_AGENT_CLI,
    MODE_HUB_API_E2E,
)
SUITES_BY_MODE: dict[str, set[str]] = {
    MODE_DIRECT_AGENT_CLI: {
        "tests/integration/test_agent_cli_runtime_ack.py",
        "tests/integration/test_agent_cli_snapshot_repro_real.py",
        "tests/integration/test_entrypoint_auto_ack.py",
        "tests/integration/test_snapshot_builds.py",
        "tests/integration/test_chat_lifecycle_ready.py",
        "tests/integration/test_agent_matrix.py",
    },
    MODE_HUB_API_E2E: {
        "tests/integration/test_hub_api_real_process.py",
        "tests/integration/test_hub_chat_lifecycle_api.py",
        "tests/integration/test_agent_tools_ack_routes.py",
        "tests/integration/test_provider_local_e2e.py",
        "tests/integration/test_provider_local_api_real_process.py",
        "tests/integration/test_chat_lifecycle_ready.py",
        "tests/integration/test_agent_matrix.py",
    },
}
REQUIRED_SUITES_BY_MODE: dict[str, set[str]] = {
    MODE_DIRECT_AGENT_CLI: {
        "tests/integration/test_snapshot_builds.py",
        "tests/integration/test_agent_cli_runtime_ack.py",
        "tests/integration/test_agent_cli_snapshot_repro_real.py",
        "tests/integration/test_entrypoint_auto_ack.py",
    },
    MODE_HUB_API_E2E: {
        "tests/integration/test_hub_chat_lifecycle_api.py",
        "tests/integration/test_agent_tools_ack_routes.py",
        "tests/integration/test_hub_api_real_process.py",
        "tests/integration/test_provider_local_api_real_process.py",
    },
}


def _changed_files_from_git(base_ref: str) -> list[str]:
    result = subprocess.run(
        ["git", "diff", "--name-only", base_ref],
        cwd=REPO_ROOT,
        check=False,
        text=True,
        capture_output=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"git diff failed: {result.stderr.strip() or result.stdout.strip()}")
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def _select_payload(changed_files: list[str]) -> dict[str, list[str]]:
    cmd = [sys.executable, str(SELECTOR), "--json"]
    if changed_files:
        cmd.extend(["--changed-files", *changed_files])
    result = subprocess.run(
        cmd,
        cwd=REPO_ROOT,
        check=False,
        text=True,
        capture_output=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"suite selector failed: {result.stderr.strip() or result.stdout.strip()}")
    try:
        parsed = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"suite selector returned invalid JSON: {result.stdout.strip()}") from exc
    if not isinstance(parsed, dict):
        raise RuntimeError("suite selector JSON payload must be an object")
    suites = parsed.get("suites") if isinstance(parsed.get("suites"), list) else []
    markers = parsed.get("markers") if isinstance(parsed.get("markers"), list) else []
    matches = parsed.get("matched_changed_files") if isinstance(parsed.get("matched_changed_files"), list) else []
    return {
        "suites": [str(item) for item in suites if str(item).strip()],
        "markers": [str(item) for item in markers if str(item).strip()],
        "matched_changed_files": [str(item) for item in matches if str(item).strip()],
    }


def _write_selection_artifact(path: Path, payload: dict[str, list[str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _filtered_suites_for_mode(suites: list[str], mode: str) -> list[str]:
    if mode == MODE_ALL:
        return list(suites)
    allowed = SUITES_BY_MODE.get(mode, set())
    required = REQUIRED_SUITES_BY_MODE.get(mode, set())
    filtered = set(suite for suite in suites if suite in allowed)
    filtered.update(required)
    if filtered:
        return sorted(filtered)
    # Preserve deterministic behavior if selector output does not overlap with chosen mode.
    return sorted(allowed)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run deterministic integration suites.")
    parser.add_argument("--base-ref", default="origin/master...HEAD", help="git diff base expression.")
    parser.add_argument(
        "--changed-file",
        action="append",
        default=[],
        help="Changed file path (repeatable). Overrides --base-ref discovery when provided.",
    )
    parser.add_argument(
        "--changed-files",
        nargs="*",
        default=[],
        help="Space-delimited changed file paths.",
    )
    parser.add_argument(
        "--selection-output",
        default=str(DEFAULT_SELECTION_OUTPUT),
        help="Path to write deterministic suite selection artifact JSON.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Only print selected suites.",
    )
    parser.add_argument(
        "--mode",
        choices=SUPPORTED_MODES,
        default=MODE_ALL,
        help="Harness mode: run all suites, direct agent_cli launch coverage, or hub API E2E coverage.",
    )
    parser.add_argument(
        "--preflight",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Run Docker/daemon mount-network preflight before selecting/running suites.",
    )
    args = parser.parse_args()

    if args.preflight:
        preflight_cmd = [sys.executable, str(PREFLIGHT)]
        preflight = subprocess.run(preflight_cmd, cwd=REPO_ROOT, check=False, text=True, capture_output=True)
        if preflight.stdout.strip():
            print(preflight.stdout.rstrip())
        if preflight.returncode != 0:
            detail = preflight.stderr.strip() or "integration preflight failed"
            raise RuntimeError(detail)

    changed_files = [str(item) for item in args.changed_file if str(item).strip()]
    changed_files.extend(str(item) for item in args.changed_files if str(item).strip())
    if not changed_files:
        changed_files = _changed_files_from_git(args.base_ref)

    selection = _select_payload(changed_files)
    selection["suites"] = _filtered_suites_for_mode(selection["suites"], args.mode)
    _write_selection_artifact(Path(args.selection_output), selection)

    suites = selection["suites"]
    if not suites:
        print("No integration suites selected.")
        return 0

    print(f"Harness mode: {args.mode}")
    print("Selected integration suites:")
    for suite in suites:
        print(f"- {suite}")
    print("Selected integration markers:")
    for marker in selection["markers"]:
        print(f"- {marker}")
    print(f"Selection artifact: {args.selection_output}")

    if args.dry_run:
        return 0

    pytest_cmd = ["uv", "run", "pytest", *suites]
    result = subprocess.run(pytest_cmd, cwd=REPO_ROOT, check=False)
    return int(result.returncode)


if __name__ == "__main__":
    raise SystemExit(main())
