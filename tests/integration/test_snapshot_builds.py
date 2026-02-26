from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import click
import pytest

import sys

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import agent_cli.cli as image_cli
import agent_hub.server as hub_server

HELPERS_DIR = Path(__file__).resolve().parent
if str(HELPERS_DIR) not in sys.path:
    sys.path.insert(0, str(HELPERS_DIR))

from helpers import insert_ready_project


def test_snapshot_build_failure_captures_deterministic_evidence(hub_state: hub_server.HubState) -> None:
    project = insert_ready_project(hub_state, setup_script="echo build")
    project_id = str(project["id"])

    def fake_prepare(_self, _project, *, log_path=None):
        assert log_path is not None
        log_path.write_text(
            "$ docker run --group-add agent agent-ubuntu2204-setup:latest bash -lc 'echo bootstrap'\n"
            "docker: Error response from daemon: Unable to find group agent: no matching entries in group file.\n",
            encoding="utf-8",
        )
        raise hub_server.HTTPException(
            status_code=400,
            detail=(
                "Snapshot build failed: missing 'agent' group in runtime image. "
                "Failing docker invocation: docker run --group-add agent ..."
            ),
        )

    with patch.object(hub_server.HubState, "_prepare_project_snapshot_for_project", fake_prepare):
        result = hub_state._build_project_snapshot(project_id)

    assert result["build_status"] == "failed"
    assert "missing 'agent' group" in str(result["build_error"])
    assert "docker run" in str(result["build_error"])
    build_log = hub_state.project_build_log(project_id).read_text(encoding="utf-8")
    assert "Unable to find group agent" in build_log
    assert "docker run --group-add agent" in build_log


def test_daemon_visible_mount_validation_rejects_tmp_paths() -> None:
    with patch("agent_cli.cli._is_running_inside_container", return_value=True):
        with pytest.raises(click.ClickException, match="daemon-visible path"):
            image_cli._validate_daemon_visible_mount_source(Path("/tmp/runtime-config.toml"), label="--config-file")


def test_rw_mount_preflight_owner_mismatch_fails() -> None:
    with pytest.raises(click.ClickException, match="owner uid does not match runtime uid"):
        image_cli._validate_rw_mount(
            Path.cwd(),
            "/workspace/cache",
            runtime_uid=os.getuid() + 12345,
            runtime_gid=os.getgid(),
        )


def test_rw_mount_preflight_unwritable_root_fails() -> None:
    with patch("agent_cli.cli._ensure_rw_mount_owner", return_value=None), patch("agent_cli.cli.os.access", return_value=False):
        with pytest.raises(click.ClickException, match="not writable/executable"):
            image_cli._validate_rw_mount(
                Path.cwd(),
                "/workspace/cache",
                runtime_uid=os.getuid(),
                runtime_gid=os.getgid(),
            )


def test_rw_mount_preflight_success_for_writable_mount() -> None:
    image_cli._validate_rw_mount(
        Path.cwd(),
        "/workspace/cache",
        runtime_uid=os.getuid(),
        runtime_gid=os.getgid(),
    )
