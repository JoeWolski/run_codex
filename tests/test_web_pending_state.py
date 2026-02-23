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

            const keepOnlyStarting = reconcilePendingChatStarts(
              {
                "chat-starting": true,
                "chat-stopped": true,
                "chat-running": true,
                "chat-falsey": false
              },
              new Map([
                ["chat-starting", { status: "starting", is_running: false }],
                ["chat-stopped", { status: "stopped", is_running: false }],
                ["chat-running", { status: "running", is_running: true }]
              ])
            );
            assert.deepEqual(
              keepOnlyStarting,
              { "chat-starting": true },
              "pending start should only remain for chats still in starting state"
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
