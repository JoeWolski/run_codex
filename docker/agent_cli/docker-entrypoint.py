#!/usr/bin/env python3

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys


def _run(command: list[str], check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, check=check, text=True, capture_output=True)


def _configure_git_identity() -> None:
    git_user_name = os.environ.get("AGENT_HUB_GIT_USER_NAME", "").strip()
    git_user_email = os.environ.get("AGENT_HUB_GIT_USER_EMAIL", "").strip()
    if not git_user_name and not git_user_email:
        return
    if not git_user_name or not git_user_email:
        raise RuntimeError(
            "AGENT_HUB_GIT_USER_NAME and AGENT_HUB_GIT_USER_EMAIL must be set together."
        )

    _run(["git", "config", "--global", "user.name", git_user_name])
    _run(["git", "config", "--global", "user.email", git_user_email])


def _prepare_git_credentials() -> None:
    # Keep runtime git auth deterministic: only credential helper entries are allowed.
    os.environ.pop("GH_TOKEN", None)
    os.environ.pop("GITHUB_TOKEN", None)
    os.environ.pop("GH_HOST", None)

    source_raw = os.environ.get("AGENT_HUB_GIT_CREDENTIALS_SOURCE", "").strip()
    target_raw = os.environ.get("AGENT_HUB_GIT_CREDENTIALS_FILE", "").strip()
    if not source_raw:
        return

    source_path = Path(source_raw)
    target_path = Path(target_raw or "/tmp/agent_hub_git_credentials")
    if source_path.is_file():
        try:
            credential_bytes = source_path.read_bytes()
        except OSError:
            credential_bytes = b""
        if credential_bytes:
            try:
                target_path.parent.mkdir(parents=True, exist_ok=True)
            except OSError:
                pass
            try:
                should_write = True
                if target_path.exists():
                    should_write = target_path.read_bytes() != credential_bytes
                if should_write:
                    target_path.write_bytes(credential_bytes)
                target_path.chmod(0o600)
            except OSError:
                pass


def _set_umask() -> None:
    local_umask = os.environ.get("LOCAL_UMASK", "0022")
    if local_umask and len(local_umask) in (3, 4) and local_umask.isdigit():
        os.umask(int(local_umask, 8))


def _ensure_claude_native_command_path(*, command: list[str], home: str, source_path: Path | None = None) -> None:
    if not command:
        return
    if Path(command[0]).name != "claude":
        return

    resolved_source_path = source_path or Path("/usr/local/bin/claude")
    target_path = Path(home) / ".local" / "bin" / "claude"
    if target_path.exists() or target_path.is_symlink():
        if target_path.is_file() and os.access(target_path, os.X_OK):
            return
        raise RuntimeError(
            "Claude native bootstrap failed: "
            f"command={command!r} home={home!r} target={str(target_path)!r} "
            "target exists but is not an executable file."
        )

    if not resolved_source_path.is_file() or not os.access(resolved_source_path, os.X_OK):
        raise RuntimeError(
            "Claude native bootstrap failed: "
            f"command={command!r} home={home!r} source={str(resolved_source_path)!r} "
            "source command is missing or not executable."
        )

    try:
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.symlink_to(resolved_source_path)
    except OSError as exc:
        raise RuntimeError(
            "Claude native bootstrap failed: "
            f"command={command!r} home={home!r} source={str(resolved_source_path)!r} target={str(target_path)!r} "
            f"symlink creation error={exc}"
        ) from exc


def _entrypoint_main() -> None:
    command = list(sys.argv[1:]) if sys.argv[1:] else ["codex"]
    local_home = os.environ.get("LOCAL_HOME", "").strip() or os.environ.get("HOME", "").strip() or "/tmp"
    if not os.environ.get("HOME"):
        os.environ["HOME"] = local_home

    _set_umask()
    _ensure_claude_native_command_path(command=command, home=os.environ["HOME"])
    _prepare_git_credentials()
    _configure_git_identity()

    os.execvp(command[0], command)


if __name__ == "__main__":
    _entrypoint_main()
