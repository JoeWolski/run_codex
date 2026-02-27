#!/usr/bin/env python3

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import urllib.parse
import urllib.error
import urllib.request


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


def _configure_git_auth_from_env() -> None:
    github_token = os.environ.get("GITHUB_TOKEN", "").strip() or os.environ.get("GH_TOKEN", "").strip()
    if not github_token:
        return

    host = os.environ.get("AGENT_HUB_GIT_CREDENTIAL_HOST", "").strip().lower() or "github.com"
    scheme = os.environ.get("AGENT_HUB_GIT_CREDENTIAL_SCHEME", "").strip().lower() or "https"
    if scheme not in {"http", "https"}:
        raise RuntimeError(f"Unsupported AGENT_HUB_GIT_CREDENTIAL_SCHEME: {scheme}")

    username = os.environ.get("GITHUB_ACTOR", "").strip() or "x-access-token"
    encoded_username = urllib.parse.quote(username, safe="")
    encoded_token = urllib.parse.quote(github_token, safe="")
    credential_file = Path("/tmp/agent_hub_git_credentials")
    credential_file.write_text(f"{scheme}://{encoded_username}:{encoded_token}@{host}\n", encoding="utf-8")
    os.chmod(credential_file, 0o600)

    host_name = host.rsplit(":", 1)[0] if ":" in host else host
    git_prefix = f"{scheme}://{host}/"
    _run(["git", "config", "--global", "credential.helper", f"store --file={str(credential_file)}"])
    _run(["git", "config", "--global", "--add", f"url.{git_prefix}.insteadOf", f"git@{host_name}:"])
    _run(["git", "config", "--global", "--add", f"url.{git_prefix}.insteadOf", f"ssh://git@{host_name}/"])


def _ensure_workspace_tmp(*, workspace_tmp: Path | None = None) -> None:
    target = workspace_tmp or Path("/workspace/tmp")
    try:
        target.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise RuntimeError(
            "Workspace tmp bootstrap failed: "
            f"path={str(target)!r} unable to create directory ({exc})"
        ) from exc


def _set_umask() -> None:
    local_umask = os.environ.get("LOCAL_UMASK", "0022")
    if local_umask and len(local_umask) in (3, 4) and local_umask.isdigit():
        os.umask(int(local_umask, 8))


def _ensure_workspace_permissions() -> None:
    try:
        _run(["chmod", "-R", "g+rwx", "/workspace"], check=False)
    except Exception:
        pass


def _ensure_user_in_passwd() -> None:
    uid = os.getuid()
    gid = os.getgid()
    if uid == 0:
        return
    try:
        passwd_content = Path("/etc/passwd").read_text()
        if f":x:{uid}:{gid}:" in passwd_content:
            return
    except OSError:
        pass

    try:
        with Path("/etc/passwd").open("a") as f:
            f.write(f"agentuser:x:{uid}:{gid}:Mapped Runtime User:/workspace:/bin/bash\n")
    except OSError:
        pass

    try:
        with Path("/etc/shadow").open("a") as f:
            f.write(f"agentuser::19888:0:99999:7:::\n")
    except OSError:
        pass


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


def _ensure_claude_json_file(path: Path) -> None:
    try:
        if path.exists():
            if not path.is_file():
                raise RuntimeError(
                    "Claude config bootstrap failed: "
                    f"path={str(path)!r} is not a regular file."
                )
            raw = path.read_text(encoding="utf-8")
            try:
                json.loads(raw)
                return
            except json.JSONDecodeError:
                path.write_text("{}\n", encoding="utf-8")
                return

        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}\n", encoding="utf-8")
    except OSError as exc:
        raise RuntimeError(
            "Claude config bootstrap failed: "
            f"path={str(path)!r} unable to initialize config: {exc}"
        ) from exc
    except UnicodeError:
        path.write_text("{}\n", encoding="utf-8")


def _ack_runtime_ready() -> None:
    base_url = str(os.environ.get("AGENT_HUB_AGENT_TOOLS_URL") or "").strip().rstrip("/")
    token = str(os.environ.get("AGENT_HUB_AGENT_TOOLS_TOKEN") or "").strip()
    guid = str(os.environ.get("AGENT_HUB_READY_ACK_GUID") or "").strip()
    if not base_url or not token or not guid:
        return
    payload = {
        "guid": guid,
        "stage": "container_bootstrapped",
        "meta": {
            "entrypoint": "docker/agent_cli/docker-entrypoint.py",
            "pid": os.getpid(),
        },
    }
    request = urllib.request.Request(
        f"{base_url}/ack",
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Authorization": f"Bearer {token}",
            "x-agent-hub-agent-tools-token": token,
        },
        data=json.dumps(payload).encode("utf-8"),
    )
    try:
        with urllib.request.urlopen(request, timeout=5.0):
            return
    except (urllib.error.URLError, TimeoutError):
        return


def _entrypoint_main() -> None:
    command = list(sys.argv[1:]) if sys.argv[1:] else ["codex"]
    local_home = os.environ.get("LOCAL_HOME", "").strip() or os.environ.get("HOME", "").strip() or "/tmp"
    if not os.environ.get("HOME"):
        os.environ["HOME"] = local_home

    if command and Path(command[0]).name == "claude":
        _ensure_claude_json_file(Path(os.environ["HOME"]) / ".claude.json")

    _ensure_workspace_tmp()
    _set_umask()
    _ensure_user_in_passwd()
    _ensure_workspace_permissions()
    _ensure_claude_native_command_path(command=command, home=os.environ["HOME"])
    _configure_git_auth_from_env()
    _configure_git_identity()
    _ack_runtime_ready()

    os.execvp(command[0], command)


if __name__ == "__main__":
    _entrypoint_main()
