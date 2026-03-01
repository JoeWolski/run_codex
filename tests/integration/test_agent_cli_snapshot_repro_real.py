from __future__ import annotations

import subprocess
import uuid
from pathlib import Path

import pytest

import sys

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import agent_cli.cli as image_cli

def _run(cmd: list[str], *, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=str(cwd) if cwd is not None else None,
        check=False,
        text=True,
        capture_output=True,
    )



def _docker_daemon_ready() -> bool:
    probe = _run(["docker", "info", "--format", "{{.ServerVersion}}"])
    return probe.returncode == 0 and bool(probe.stdout.strip())



def test_prepare_snapshot_reproduces_missing_agent_group_failure(integration_tmp_dir: Path) -> None:
    if not _docker_daemon_ready():
        pytest.skip("docker daemon is unavailable")

    project_dir = integration_tmp_dir / "project"
    project_dir.mkdir(parents=True, exist_ok=True)
    (project_dir / "README.md").write_text("snapshot repro\n", encoding="utf-8")

    config_file = integration_tmp_dir / "agent.config.toml"
    config_file.write_text("model = 'test'\n", encoding="utf-8")

    snapshot_tag = f"agent-hub-test:snapshot-{uuid.uuid4().hex[:12]}"
    setup_runtime_tag = image_cli._snapshot_setup_runtime_image_for_snapshot(snapshot_tag)

    try:
        pull = _run(["docker", "pull", "alpine:3.20"])
        assert pull.returncode == 0, f"docker pull failed: {pull.stderr or pull.stdout}"

        tag = _run(["docker", "tag", "alpine:3.20", setup_runtime_tag])
        assert tag.returncode == 0, f"docker tag failed: {tag.stderr or tag.stdout}"

        cmd = [
            "uv",
            "run",
            "--project",
            str(ROOT),
            "agent_cli",
            "--project",
            str(project_dir),
            "--config-file",
            str(config_file),
            "--system-prompt-file",
            str(ROOT / "SYSTEM_PROMPT.md"),
            "--snapshot-image-tag",
            snapshot_tag,
            "--prepare-snapshot-only",
            "--setup-script",
            "echo setup",
        ]
        result = _run(cmd, cwd=ROOT)

        combined = f"{result.stdout}\n{result.stderr}"
        assert result.returncode != 0, combined
        assert (
            "must be daemon-visible as a file" in combined
            or "Unable to find group agent" in combined
        ), combined
        if "Unable to find group agent" in combined:
            assert "docker run" in combined
    finally:
        _run(["docker", "rmi", "-f", setup_runtime_tag])
        _run(["docker", "rmi", "-f", snapshot_tag])
