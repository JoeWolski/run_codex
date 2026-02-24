from __future__ import annotations

import subprocess
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class WebFlexLayoutStateTests(unittest.TestCase):
    def test_reconcile_collapses_empty_sections_and_is_deterministic(self) -> None:
        node_script = textwrap.dedent(
            """
            import assert from "node:assert/strict";
            import {
              layoutJsonEquals,
              reconcileOuterFlexLayoutJson,
              reconcileProjectChatsFlexLayoutJson
            } from "./web/src/flexLayoutState.js";

            function collectTabsetIds(node, output = []) {
              if (!node || typeof node !== "object") {
                return output;
              }
              if (String(node.type || "") === "tabset") {
                output.push(String(node.id || ""));
              }
              if (Array.isArray(node.children)) {
                for (const child of node.children) {
                  collectTabsetIds(child, output);
                }
              }
              return output;
            }

            function collectTabIds(node, output = []) {
              if (!node || typeof node !== "object") {
                return output;
              }
              if (String(node.type || "") === "tab") {
                output.push(String(node.id || ""));
              }
              if (Array.isArray(node.children)) {
                for (const child of node.children) {
                  collectTabIds(child, output);
                }
              }
              return output;
            }

            const chats = [
              { id: "chat-a", display_name: "Chat A" },
              { id: "chat-b", display_name: "Chat B" }
            ];

            const staleProjectLayout = {
              global: {
                tabEnableClose: false,
                tabSetEnableDeleteWhenEmpty: false,
                tabSetEnableMaximize: false
              },
              borders: [],
              layout: {
                type: "row",
                children: [
                  {
                    type: "tabset",
                    id: "project-tabset-main",
                    active: true,
                    selected: 0,
                    children: [
                      {
                        type: "tab",
                        id: "chat-chat-a",
                        component: "project-chat-pane",
                        config: { chat_id: "chat-a" }
                      }
                    ]
                  },
                  {
                    type: "tabset",
                    id: "project-tabset-empty",
                    selected: 0,
                    children: []
                  }
                ]
              }
            };

            const reconciledProject = reconcileProjectChatsFlexLayoutJson(staleProjectLayout, chats, "project-1");
            assert.equal(reconciledProject.global.tabSetEnableDeleteWhenEmpty, true);
            assert.deepEqual(collectTabsetIds(reconciledProject.layout), ["project-tabset-main"]);
            assert.deepEqual(
              collectTabIds(reconciledProject.layout).sort(),
              ["chat-chat-a", "chat-chat-b"].sort()
            );

            const reconciledProjectSecondPass = reconcileProjectChatsFlexLayoutJson(
              reconciledProject,
              chats,
              "project-1"
            );
            assert.equal(layoutJsonEquals(reconciledProject, reconciledProjectSecondPass), true);

            const projects = [
              { id: "project-1", name: "Project One" },
              { id: "project-2", name: "Project Two" }
            ];
            const staleOuterLayout = {
              global: {
                tabEnableClose: false,
                tabSetEnableDeleteWhenEmpty: false,
                tabSetEnableMaximize: false
              },
              borders: [],
              layout: {
                type: "row",
                children: [
                  {
                    type: "tabset",
                    id: "outer-main",
                    active: true,
                    selected: 0,
                    children: [
                      {
                        type: "tab",
                        id: "project-project-1",
                        component: "project-chat-group",
                        config: { project_id: "project-1" }
                      },
                      {
                        type: "tab",
                        id: "orphan-chats",
                        component: "orphan-chat-group",
                        config: {}
                      }
                    ]
                  },
                  {
                    type: "tabset",
                    id: "outer-empty",
                    selected: 0,
                    children: []
                  }
                ]
              }
            };

            const reconciledOuter = reconcileOuterFlexLayoutJson(staleOuterLayout, projects, false);
            assert.equal(reconciledOuter.global.tabSetEnableDeleteWhenEmpty, true);
            assert.deepEqual(collectTabsetIds(reconciledOuter.layout), ["outer-main"]);
            assert.deepEqual(
              collectTabIds(reconciledOuter.layout).sort(),
              ["project-project-1", "project-project-2"].sort()
            );

            const reconciledOuterSecondPass = reconcileOuterFlexLayoutJson(reconciledOuter, projects, false);
            assert.equal(layoutJsonEquals(reconciledOuter, reconciledOuterSecondPass), true);
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
            msg=f"Node flexlayout state test failed:\\nSTDOUT:\\n{result.stdout}\\nSTDERR:\\n{result.stderr}",
        )


if __name__ == "__main__":
    unittest.main()
