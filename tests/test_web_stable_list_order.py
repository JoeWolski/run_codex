from __future__ import annotations

import subprocess
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class WebStableListOrderTests(unittest.TestCase):
    def test_first_seen_order_is_stable_and_alias_aware(self) -> None:
        node_script = textwrap.dedent(
            """
            import assert from "node:assert/strict";
            import {
              createFirstSeenOrderState,
              stableOrderItemsByFirstSeen
            } from "./web/src/stableListOrder.js";

            const state = createFirstSeenOrderState();
            const aliasByKey = new Map();

            const baseline = stableOrderItemsByFirstSeen(
              [
                { id: "project-a" },
                { id: "project-b" },
                { id: "project-c" }
              ],
              (item) => item.id,
              state,
              aliasByKey
            );
            assert.deepEqual(
              baseline.map((item) => item.id),
              ["project-a", "project-b", "project-c"],
              "baseline ordering should follow first appearance"
            );

            const shuffled = stableOrderItemsByFirstSeen(
              [
                { id: "project-c" },
                { id: "project-a" },
                { id: "project-b" }
              ],
              (item) => item.id,
              state,
              aliasByKey
            );
            assert.deepEqual(
              shuffled.map((item) => item.id),
              ["project-a", "project-b", "project-c"],
              "later server reorder should not reshuffle first-seen order"
            );

            const withNewItem = stableOrderItemsByFirstSeen(
              [
                { id: "project-d" },
                { id: "project-c" },
                { id: "project-a" },
                { id: "project-b" }
              ],
              (item) => item.id,
              state,
              aliasByKey
            );
            assert.deepEqual(
              withNewItem.map((item) => item.id),
              ["project-a", "project-b", "project-c", "project-d"],
              "new entries should append in first-seen order without moving existing entries"
            );

            const pendingState = createFirstSeenOrderState();
            const pendingAliasByKey = new Map();
            const pendingFirst = stableOrderItemsByFirstSeen(
              [{ id: "pending-auto-1" }],
              (item) => item.id,
              pendingState,
              pendingAliasByKey
            );
            assert.deepEqual(pendingFirst.map((item) => item.id), ["pending-auto-1"]);

            pendingAliasByKey.set("project-real-1", "pending-auto-1");
            const resolvedAfterCreate = stableOrderItemsByFirstSeen(
              [{ id: "project-real-1" }],
              (item) => item.id,
              pendingState,
              pendingAliasByKey
            );
            assert.deepEqual(
              resolvedAfterCreate.map((item) => item.id),
              ["project-real-1"],
              "resolved rows should retain pending row slot when alias is provided"
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
            msg=f"Node stable-list-order test failed:\\nSTDOUT:\\n{result.stdout}\\nSTDERR:\\n{result.stderr}",
        )


if __name__ == "__main__":
    unittest.main()
