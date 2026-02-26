from __future__ import annotations

import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

import pytest

import sys

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import agent_hub.server as hub_server


def _docker_daemon_available() -> bool:
    if shutil.which("docker") is None:
        return False
    result = subprocess.run(
        ["docker", "info", "--format", "{{.ServerVersion}}"],
        check=False,
        text=True,
        capture_output=True,
    )
    return result.returncode == 0 and bool(result.stdout.strip())


@pytest.fixture(scope="session")
def docker_daemon_available() -> bool:
    return _docker_daemon_available()


@pytest.fixture()
def integration_tmp_dir() -> Iterator[Path]:
    workspace_tmp = Path("/workspace/tmp")
    workspace_tmp.mkdir(parents=True, exist_ok=True)
    tmp = tempfile.TemporaryDirectory(prefix="agent-hub-int-", dir=str(workspace_tmp))
    try:
        yield Path(tmp.name)
    finally:
        tmp.cleanup()


@dataclass
class ProcessRegistry:
    processes: list[subprocess.Popen[object]] = field(default_factory=list)

    def track(self, process: subprocess.Popen[object]) -> subprocess.Popen[object]:
        self.processes.append(process)
        return process

    def cleanup(self) -> None:
        for process in reversed(self.processes):
            if process.poll() is not None:
                continue
            try:
                process.terminate()
                process.wait(timeout=5)
            except Exception:
                try:
                    process.kill()
                except Exception:
                    pass


@pytest.fixture()
def process_registry() -> Iterator[ProcessRegistry]:
    registry = ProcessRegistry()
    try:
        yield registry
    finally:
        registry.cleanup()


@pytest.fixture()
def hub_state() -> hub_server.HubState:
    tmp = tempfile.TemporaryDirectory()
    tmp_path = Path(tmp.name)
    config = tmp_path / "agent.config.toml"
    config.write_text("model = 'test'\n", encoding="utf-8")
    state = hub_server.HubState(tmp_path / "hub", config)
    try:
        yield state
    finally:
        startup_thread = getattr(state, "_startup_reconcile_thread", None)
        if startup_thread is not None and startup_thread.is_alive():
            startup_thread.join(timeout=2.0)
        tmp.cleanup()
