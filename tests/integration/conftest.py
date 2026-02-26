from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

import sys

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import agent_hub.server as hub_server


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
