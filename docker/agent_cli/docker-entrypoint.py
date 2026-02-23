#!/usr/bin/env python3

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import urllib.parse


def _run(command: list[str], check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, check=check, text=True, capture_output=True)


def _ensure_runtime_home_paths(local_home: str) -> None:
    home_path = Path(local_home)
    cache_dir = home_path / ".cache"
    uv_cache_dir = cache_dir / "uv"
    projects_dir = home_path / "projects"

    for path in (home_path, cache_dir, uv_cache_dir, projects_dir):
        try:
            path.mkdir(parents=True, exist_ok=True)
        except OSError:
            continue


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


def _read_git_credential_secret(credentials_path: Path, host: str) -> str | None:
    normalized_host = str(host or "").strip().lower()
    if not normalized_host or not credentials_path.is_file():
        return None
    try:
        lines = credentials_path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError:
        return None

    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        parsed = urllib.parse.urlparse(line)
        if parsed.scheme != "https":
            continue
        if str(parsed.hostname or "").strip().lower() != normalized_host:
            continue
        if parsed.password is None:
            continue
        secret = urllib.parse.unquote(parsed.password).strip()
        if secret:
            return secret
    return None


def _prepare_git_credentials() -> None:
    source_raw = os.environ.get("AGENT_HUB_GIT_CREDENTIALS_SOURCE", "").strip()
    target_raw = os.environ.get("AGENT_HUB_GIT_CREDENTIALS_FILE", "").strip()
    host_raw = os.environ.get("AGENT_HUB_GIT_CREDENTIAL_HOST", "").strip()
    if not source_raw:
        if os.environ.get("GH_TOKEN") and not os.environ.get("GITHUB_TOKEN"):
            os.environ["GITHUB_TOKEN"] = str(os.environ["GH_TOKEN"])
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

        if not os.environ.get("GH_TOKEN"):
            secret = _read_git_credential_secret(source_path, host_raw)
            if secret:
                os.environ["GH_TOKEN"] = secret

    if os.environ.get("GH_TOKEN") and not os.environ.get("GITHUB_TOKEN"):
        os.environ["GITHUB_TOKEN"] = str(os.environ["GH_TOKEN"])
    normalized_host = host_raw.strip().lower()
    if normalized_host and normalized_host != "github.com" and not os.environ.get("GH_HOST"):
        os.environ["GH_HOST"] = normalized_host


def _set_umask() -> None:
    local_umask = os.environ.get("LOCAL_UMASK", "0022")
    if local_umask and len(local_umask) in (3, 4) and local_umask.isdigit():
        os.umask(int(local_umask, 8))


def _entrypoint_main() -> None:
    command = list(sys.argv[1:]) if sys.argv[1:] else ["codex"]
    local_home = os.environ.get("LOCAL_HOME", "").strip() or os.environ.get("HOME", "").strip() or "/tmp"
    if not os.environ.get("HOME"):
        os.environ["HOME"] = local_home

    _set_umask()
    _ensure_runtime_home_paths(local_home)
    _prepare_git_credentials()
    _configure_git_identity()

    os.execvp(command[0], command)


if __name__ == "__main__":
    _entrypoint_main()
