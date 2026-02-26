#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path


SUITE_MAP: list[tuple[str, list[str], list[str]]] = [
    (
        "src/agent_cli/",
        [
            "tests/integration/test_snapshot_builds.py",
            "tests/integration/test_agent_cli_runtime_ack.py",
            "tests/integration/test_agent_matrix.py",
        ],
        ["integration.snapshot", "integration.launch", "integration.agent-matrix"],
    ),
    (
        "docker/agent_cli/",
        [
            "tests/integration/test_agent_cli_runtime_ack.py",
            "tests/integration/test_agent_cli_snapshot_repro_real.py",
            "tests/integration/test_entrypoint_auto_ack.py",
            "tests/integration/test_chat_lifecycle_ready.py",
        ],
        ["integration.bootstrap", "integration.readiness"],
    ),
    (
        "src/agent_hub/agent_tools_mcp.py",
        [
            "tests/integration/test_agent_tools_ack_routes.py",
            "tests/integration/test_chat_lifecycle_ready.py",
        ],
        ["integration.agent-tools", "integration.readiness"],
    ),
    (
        "src/agent_hub/server.py",
        [
            "tests/integration/test_chat_lifecycle_ready.py",
            "tests/integration/test_hub_chat_lifecycle_api.py",
            "tests/integration/test_hub_api_real_process.py",
            "tests/integration/test_provider_local_e2e.py",
            "tests/integration/test_provider_local_api_real_process.py",
            "tests/integration/test_agent_matrix.py",
            "tests/integration/test_snapshot_builds.py",
        ],
        ["integration.lifecycle", "integration.provider", "integration.readiness"],
    ),
    (
        "config/agent.config.toml",
        [
            "tests/integration/test_hub_chat_lifecycle_api.py",
            "tests/integration/test_chat_lifecycle_ready.py",
        ],
        ["integration.prompt-bootstrap"],
    ),
    (
        "SYSTEM_PROMPT.md",
        [
            "tests/integration/test_hub_chat_lifecycle_api.py",
            "tests/integration/test_chat_lifecycle_ready.py",
        ],
        ["integration.prompt-bootstrap"],
    ),
    (
        "AGENTS.md",
        [
            "tests/integration/test_hub_chat_lifecycle_api.py",
            "tests/integration/test_chat_lifecycle_ready.py",
        ],
        ["integration.prompt-bootstrap"],
    ),
]

CORE_INTEGRATION_SUITES = [
    "tests/integration/test_snapshot_builds.py",
    "tests/integration/test_chat_lifecycle_ready.py",
    "tests/integration/test_hub_chat_lifecycle_api.py",
    "tests/integration/test_agent_tools_ack_routes.py",
]


def _normalized_path(value: str) -> str:
    return str(Path(value).as_posix()).lstrip("./")


def _matches_prefix(path: str, prefix: str) -> bool:
    normalized_path = _normalized_path(path)
    normalized_prefix = _normalized_path(prefix)
    return normalized_path == normalized_prefix or normalized_path.startswith(normalized_prefix.rstrip("/") + "/")


def _selection(changed_files: list[str]) -> dict[str, list[str]]:
    selected_suites: set[str] = set()
    selected_markers: set[str] = set()
    matched_paths: set[str] = set()

    normalized_files = [_normalized_path(path) for path in changed_files if str(path).strip()]
    for changed in normalized_files:
        path_matched = False
        for prefix, suites, markers in SUITE_MAP:
            if _matches_prefix(changed, prefix):
                selected_suites.update(suites)
                selected_markers.update(markers)
                matched_paths.add(changed)
                path_matched = True
        if not path_matched:
            selected_suites.update(CORE_INTEGRATION_SUITES)
            selected_markers.add("integration.core-fallback")

    if not normalized_files:
        selected_suites.update(CORE_INTEGRATION_SUITES)
        selected_markers.add("integration.core-fallback")

    return {
        "suites": sorted(selected_suites),
        "markers": sorted(selected_markers),
        "matched_changed_files": sorted(matched_paths),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Select deterministic integration suites from changed files.")
    parser.add_argument(
        "--changed-file",
        action="append",
        default=[],
        help="Changed file path (repeatable).",
    )
    parser.add_argument(
        "--changed-files",
        nargs="*",
        default=[],
        help="Space-delimited changed file paths.",
    )
    parser.add_argument(
        "--changed-files-from-stdin",
        action="store_true",
        help="Also read newline-delimited changed files from stdin.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print JSON payload with suites/markers/matches.",
    )
    args = parser.parse_args()

    changed_files = [str(item) for item in args.changed_file if str(item).strip()]
    changed_files.extend(str(item) for item in args.changed_files if str(item).strip())
    if args.changed_files_from_stdin:
        try:
            import sys

            for line in sys.stdin:
                candidate = line.strip()
                if candidate:
                    changed_files.append(candidate)
        except Exception:
            pass

    selection = _selection(changed_files)
    if args.json:
        print(json.dumps(selection, sort_keys=True))
        return 0

    for suite in selection["suites"]:
        print(suite)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
