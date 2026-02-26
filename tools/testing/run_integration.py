#!/usr/bin/env python3
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SELECTOR = REPO_ROOT / "tools" / "testing" / "select_integration_suites.py"


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


def _select_suites(changed_files: list[str]) -> list[str]:
    cmd = [sys.executable, str(SELECTOR)]
    for path in changed_files:
        cmd.extend(["--changed-file", path])
    result = subprocess.run(
        cmd,
        cwd=REPO_ROOT,
        check=False,
        text=True,
        capture_output=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"suite selector failed: {result.stderr.strip() or result.stdout.strip()}")
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


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
        "--dry-run",
        action="store_true",
        help="Only print selected suites.",
    )
    args = parser.parse_args()

    changed_files = [str(item) for item in args.changed_file if str(item).strip()]
    if not changed_files:
        changed_files = _changed_files_from_git(args.base_ref)

    suites = _select_suites(changed_files)
    if not suites:
        print("No integration suites selected.")
        return 0

    print("Selected integration suites:")
    for suite in suites:
        print(f"- {suite}")
    if args.dry_run:
        return 0

    pytest_cmd = ["uv", "run", "pytest", *suites]
    result = subprocess.run(pytest_cmd, cwd=REPO_ROOT, check=False)
    return int(result.returncode)


if __name__ == "__main__":
    raise SystemExit(main())
