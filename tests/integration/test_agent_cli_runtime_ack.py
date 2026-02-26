from __future__ import annotations

import unittest
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import agent_cli.cli as image_cli


class AgentCliRuntimeAckIntegrationTests(unittest.TestCase):
    def test_agent_tools_env_parser_keeps_ready_ack_guid(self) -> None:
        parsed = image_cli._agent_tools_env_from_entries(
            [
                "AGENT_HUB_AGENT_TOOLS_URL=http://host.docker.internal:8765/api/chats/chat-1/agent-tools",
                "AGENT_HUB_AGENT_TOOLS_TOKEN=test-token",
                "AGENT_HUB_AGENT_TOOLS_PROJECT_ID=project-1",
                "AGENT_HUB_AGENT_TOOLS_CHAT_ID=chat-1",
                "AGENT_HUB_READY_ACK_GUID=ready-guid-1",
            ]
        )
        self.assertEqual(parsed["AGENT_HUB_READY_ACK_GUID"], "ready-guid-1")
