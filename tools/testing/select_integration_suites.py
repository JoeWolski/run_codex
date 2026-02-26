#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path


SUITE_MAP: list[tuple[str, list[str]]] = [
    (
        "src/agent_cli/",
        [
            "tests/integration/test_agent_cli_runtime_ack.py",
        ],
    ),
    (
        "docker/agent_cli/",
        [
            "tests/integration/test_agent_cli_runtime_ack.py",
        ],
    ),
    (
        "src/agent_hub/agent_tools_mcp.py",
        [
            "tests/integration/test_agent_tools_ack_routes.py",
        ],
    ),
    (
        "src/agent_hub/server.py",
        [
            "tests/integration/test_agent_tools_ack_routes.py",
            "tests/integration/test_hub_chat_lifecycle_api.py",
            "tests/integration/test_provider_local_e2e.py",
        ],
    ),
    (
        "src/agent_hub/",
        [
            "tests/integration/test_provider_local_e2e.py",
        ],
    ),
    (
        "config/agent.config.toml",
        [
            "tests/integration/test_hub_chat_lifecycle_api.py",
        ],
    ),
    (
        "SYSTEM_PROMPT.md",
        [
            "tests/integration/test_hub_chat_lifecycle_api.py",
        ],
    ),
]

DEFAULT_CORE_SUITES = [
    "tests/integration/test_agent_tools_ack_routes.py",
]


def _normalized_path(value: str) -> str:
    return str(Path(value).as_posix()).lstrip("./")


def _selected_suites(changed_files: list[str]) -> list[str]:
    selected: set[str] = set()
    normalized_files = [_normalized_path(path) for path in changed_files if str(path).strip()]
    for changed in normalized_files:
        for prefix, suites in SUITE_MAP:
            normalized_prefix = _normalized_path(prefix)
            if changed == normalized_prefix or changed.startswith(normalized_prefix.rstrip("/") + "/"):
                selected.update(suites)
    if not selected:
        selected.update(DEFAULT_CORE_SUITES)
    return sorted(selected)


def main() -> int:
    parser = argparse.ArgumentParser(description="Select deterministic integration suites from changed files.")
    parser.add_argument(
        "--changed-file",
        action="append",
        default=[],
        help="Changed file path (repeatable).",
    )
    parser.add_argument(
        "--changed-files-from-stdin",
        action="store_true",
        help="Also read newline-delimited changed files from stdin.",
    )
    args = parser.parse_args()

    changed_files = [str(item) for item in args.changed_file if str(item).strip()]
    if args.changed_files_from_stdin:
        try:
            import sys

            for line in sys.stdin:
                candidate = line.strip()
                if candidate:
                    changed_files.append(candidate)
        except Exception:
            pass

    for suite in _selected_suites(changed_files):
        print(suite)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
