#!/usr/bin/env python3

from __future__ import annotations

import os
import subprocess
from pathlib import Path
import sys


def _run(command: list[str], check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, check=check, text=True, capture_output=True)


def _run_success(command: list[str]) -> bool:
    result = _run(command, check=False)
    return result.returncode == 0


def _group_name_for_gid(gid: str) -> str | None:
    result = _run(["getent", "group", str(gid)], check=False)
    if result.returncode != 0 or not result.stdout:
        return None
    return result.stdout.split(":", 1)[0]


def _ensure_path_owner(path: Path, uid: int, gid: int) -> None:
    try:
        os.chown(path, uid, gid)
    except OSError:
        pass


def _ensure_runtime_home_paths(local_home: str, local_uid: int, local_gid: int) -> None:
    home_path = Path(local_home)
    cache_dir = home_path / ".cache"
    uv_cache_dir = cache_dir / "uv"
    projects_dir = home_path / "projects"

    for path in (home_path, cache_dir, uv_cache_dir, projects_dir):
        try:
            path.mkdir(parents=True, exist_ok=True)
        except OSError:
            continue
        _ensure_path_owner(path, local_uid, local_gid)


def _configure_git_identity(local_user: str) -> None:
    git_user_name = os.environ.get("AGENT_HUB_GIT_USER_NAME", "").strip()
    git_user_email = os.environ.get("AGENT_HUB_GIT_USER_EMAIL", "").strip()
    if not git_user_name and not git_user_email:
        return
    if not git_user_name or not git_user_email:
        raise RuntimeError(
            "AGENT_HUB_GIT_USER_NAME and AGENT_HUB_GIT_USER_EMAIL must be set together."
        )

    _run(["gosu", local_user, "git", "config", "--global", "user.name", git_user_name])
    _run(["gosu", local_user, "git", "config", "--global", "user.email", git_user_email])


def _ensure_user_and_groups() -> None:
    local_user = os.environ.get("LOCAL_USER", "agent")
    local_group = os.environ.get("LOCAL_GROUP", local_user)
    local_uid = int(os.environ.get("LOCAL_UID", "1000"))
    local_gid = int(os.environ.get("LOCAL_GID", "1000"))
    local_supp_gids = os.environ.get("LOCAL_SUPP_GIDS", "").strip()
    local_supp_groups = os.environ.get("LOCAL_SUPP_GROUPS", "").strip()
    local_home = os.environ.get("LOCAL_HOME", f"/home/{local_user}")
    local_umask = os.environ.get("LOCAL_UMASK", "0022")

    if local_umask and len(local_umask) in (3, 4) and local_umask.isdigit():
        os.umask(int(local_umask, 8))

    if not sys.argv[1:]:
        command: list[str] = ["codex"]
    else:
        command = list(sys.argv[1:])

    if os.geteuid() != 0:
        os.execvp(command[0], command)

    if not _run_success(["getent", "group", str(local_gid)]):
        if _run_success(["getent", "group", local_group]):
            _run(["groupmod", "--gid", str(local_gid), local_group])
        else:
            _run(["groupadd", "--gid", str(local_gid), local_group])

    if not _run_success(["id", "-u", local_user]):
        if Path(local_home).exists():
            _run(
                [
                    "useradd",
                    "--uid",
                    str(local_uid),
                    "--gid",
                    str(local_gid),
                    "--home-dir",
                    local_home,
                    "--no-create-home",
                    "--shell",
                    "/bin/bash",
                    local_user,
                ]
            )
        else:
            _run(
                [
                    "useradd",
                    "--uid",
                    str(local_uid),
                    "--gid",
                    str(local_gid),
                    "--home-dir",
                    local_home,
                    "--create-home",
                    "--shell",
                    "/bin/bash",
                    local_user,
                ]
            )

    if _run_success(["id", "-u", local_user]):
        current_uid = int(_run(["id", "-u", local_user], check=False).stdout.strip())
        if current_uid != local_uid:
            _run(["usermod", "--uid", str(local_uid), local_user])

    current_gid = int(_run(["id", "-g", local_user], check=False).stdout.strip())
    if current_gid != local_gid:
        _run(["usermod", "--gid", str(local_gid), local_user])

    if local_supp_gids:
        supp_gids = [gid for gid in local_supp_gids.split(",") if gid]
        supp_groups = [group for group in local_supp_groups.split(",") if group]
        supplemental_groups: list[str] = []
        for idx, gid in enumerate(supp_gids):
            if gid == str(local_gid) or not gid:
                continue

            group_name = _group_name_for_gid(gid)
            if group_name is None:
                candidate = supp_groups[idx] if idx < len(supp_groups) else f"hostgrp_{gid}"
                if _run_success(["getent", "group", candidate]):
                    candidate = f"{candidate}_{gid}"
                _run(["groupadd", "--gid", gid, candidate])
                group_name = candidate
            supplemental_groups.append(group_name)

        if supplemental_groups:
            deduped = []
            for group in supplemental_groups:
                if group not in deduped:
                    deduped.append(group)
            _run(["usermod", "--append", "--groups", ",".join(deduped), local_user])

    if _run_success(["which", "sudo"]):
        if not _run_success(["getent", "group", "sudo"]):
            _run(["groupadd", "--system", "sudo"])
        _run(["usermod", "--append", "--groups", "sudo", local_user])
        sudoers_file = Path(f"/etc/sudoers.d/90-{local_user}")
        sudoers_file.write_text(f"{local_user} ALL=(ALL:ALL) NOPASSWD:ALL\n")
        sudoers_file.chmod(0o440)

    _ensure_runtime_home_paths(local_home, local_uid, local_gid)
    _configure_git_identity(local_user)

    os.execvp("gosu", ["gosu", local_user, *command])


if __name__ == "__main__":
    _ensure_user_and_groups()
