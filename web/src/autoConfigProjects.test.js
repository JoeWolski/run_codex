import test from "node:test";
import assert from "node:assert/strict";
import {
  markPendingAutoConfigProjectFailed,
  projectRowFromPendingAutoConfig,
  removePendingAutoConfigProject
} from "./autoConfigProjects.js";

test("projectRowFromPendingAutoConfig maps failed status and error fields", () => {
  const row = projectRowFromPendingAutoConfig({
    id: "pending-auto-1",
    stable_order_key: "pending-auto-1",
    name: "demo",
    repo_url: "https://example.com/demo.git",
    default_branch: "main",
    auto_config_status: "failed",
    auto_config_error: "build failed",
    auto_config_log: "log text"
  });

  assert.deepEqual(row, {
    id: "pending-auto-1",
    stable_order_key: "pending-auto-1",
    name: "demo",
    repo_url: "https://example.com/demo.git",
    default_branch: "main",
    build_status: "failed",
    build_error: "build failed",
    auto_config_log: "log text",
    is_auto_config_pending: true
  });
});

test("markPendingAutoConfigProjectFailed updates only the matching request and appends newline once", () => {
  const updated = markPendingAutoConfigProjectFailed(
    [
      {
        id: "pending-auto-1",
        auto_config_status: "running",
        auto_config_error: "",
        auto_config_log: "existing\n"
      },
      {
        id: "pending-auto-2",
        auto_config_status: "running",
        auto_config_error: "",
        auto_config_log: "other\n"
      }
    ],
    "pending-auto-1",
    "Build validation failed"
  );

  assert.equal(updated[0].auto_config_status, "failed");
  assert.equal(updated[0].auto_config_error, "Build validation failed");
  assert.equal(updated[0].auto_config_log, "existing\nBuild validation failed\n");
  assert.equal(updated[1].auto_config_status, "running");
  assert.equal(updated[1].auto_config_error, "");
  assert.equal(updated[1].auto_config_log, "other\n");
});

test("removePendingAutoConfigProject removes only the selected request id", () => {
  const updated = removePendingAutoConfigProject(
    [
      { id: "pending-auto-1", name: "first" },
      { id: "pending-auto-2", name: "second" }
    ],
    "pending-auto-1"
  );

  assert.deepEqual(updated, [{ id: "pending-auto-2", name: "second" }]);
});
