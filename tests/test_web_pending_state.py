from __future__ import annotations

import subprocess
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class WebPendingStateTests(unittest.TestCase):
    def test_pending_state_reconciliation(self) -> None:
        node_script = textwrap.dedent(
            """
            import assert from "node:assert/strict";
            import {
              isChatStarting,
              PENDING_CHAT_START_STALE_MS,
              PENDING_SESSION_STALE_MS,
              reconcilePendingSessions,
              reconcilePendingChatStarts
            } from "./web/src/chatPendingState.js";

            const baseTimeMs = 1_000_000;

            const staleUnseen = reconcilePendingSessions(
              [
                {
                  ui_id: "pending-1",
                  project_id: "project-a",
                  server_chat_id: "chat-a",
                  seen_on_server: false,
                  created_at_ms: baseTimeMs,
                  server_chat_id_set_at_ms: baseTimeMs
                }
              ],
              new Map(),
              baseTimeMs + PENDING_SESSION_STALE_MS + 1
            );
            assert.equal(staleUnseen.length, 0, "stale pending session should be dropped");

            const freshUnseen = reconcilePendingSessions(
              [
                {
                  ui_id: "pending-2",
                  project_id: "project-a",
                  server_chat_id: "chat-b",
                  seen_on_server: false,
                  created_at_ms: baseTimeMs,
                  server_chat_id_set_at_ms: baseTimeMs
                }
              ],
              new Map(),
              baseTimeMs + PENDING_SESSION_STALE_MS - 1
            );
            assert.equal(freshUnseen.length, 1, "fresh pending session should be preserved");

            const seenThenMissing = reconcilePendingSessions(
              [
                {
                  ui_id: "pending-3",
                  project_id: "project-a",
                  server_chat_id: "chat-c",
                  seen_on_server: true,
                  created_at_ms: baseTimeMs,
                  server_chat_id_set_at_ms: baseTimeMs
                }
              ],
              new Map(),
              baseTimeMs + 10
            );
            assert.equal(seenThenMissing.length, 0, "session should be removed once chat disappears after being seen");

            const keepPendingWithinGrace = reconcilePendingChatStarts(
              {
                "chat-starting": baseTimeMs,
                "chat-stopped": baseTimeMs,
                "chat-running": baseTimeMs,
                "chat-failed": baseTimeMs,
                "chat-missing": baseTimeMs,
                "chat-falsey": false
              },
              new Map([
                ["chat-starting", { status: "starting", is_running: false }],
                ["chat-stopped", { status: "stopped", is_running: false }],
                ["chat-running", { status: "running", is_running: true }],
                ["chat-failed", { status: "failed", is_running: false }]
              ]),
              baseTimeMs + 5_000
            );
            assert.deepEqual(
              keepPendingWithinGrace,
              {
                "chat-starting": baseTimeMs,
                "chat-stopped": baseTimeMs,
                "chat-missing": baseTimeMs
              },
              "pending start should stay during grace while chat startup is still in-flight"
            );

            const dropAfterGrace = reconcilePendingChatStarts(
              {
                "chat-stopped": baseTimeMs,
                "chat-missing": baseTimeMs
              },
              new Map([
                ["chat-stopped", { status: "stopped", is_running: false }]
              ]),
              baseTimeMs + PENDING_CHAT_START_STALE_MS + 1
            );
            assert.deepEqual(
              dropAfterGrace,
              {},
              "pending start should expire after grace timeout"
            );

            assert.equal(
              isChatStarting("stopped", false, true),
              true,
              "stopped chats with an in-flight pending start should render as starting"
            );
            assert.equal(
              isChatStarting("stopped", false, false),
              false,
              "stopped chats with no pending start should not render as starting"
            );
            assert.equal(
              isChatStarting("starting", false, false),
              true,
              "starting status should render as starting"
            );
            assert.equal(
              isChatStarting("running", true, true),
              false,
              "running chat should not render as starting"
            );
            """
        )

        result = subprocess.run(
            ["node", "--input-type=module", "-e", node_script],
            capture_output=True,
            text=True,
            cwd=str(ROOT),
            check=False,
        )
        self.assertEqual(
            result.returncode,
            0,
            msg=f"Node pending state test failed:\\nSTDOUT:\\n{result.stdout}\\nSTDERR:\\n{result.stderr}",
        )


if __name__ == "__main__":
    unittest.main()
