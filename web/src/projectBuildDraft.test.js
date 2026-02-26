import test from "node:test";
import assert from "node:assert/strict";
import { selectProjectBuildDraft } from "./projectBuildDraft.js";

test("selectProjectBuildDraft prefers server values when project card is not editing", () => {
  const selected = selectProjectBuildDraft({
    isEditing: false,
    cachedDraft: { baseImageValue: "ubuntu:24.04", setupScript: "old" },
    serverProjectDraft: { baseImageValue: "agent-cli-base", setupScript: "new" }
  });
  assert.deepEqual(selected, { baseImageValue: "agent-cli-base", setupScript: "new" });
});

test("selectProjectBuildDraft prefers edit draft values while editing", () => {
  const selected = selectProjectBuildDraft({
    isEditing: true,
    cachedDraft: { baseImageValue: "agent-cli-base", setupScript: "edited" },
    serverProjectDraft: { baseImageValue: "ubuntu:24.04", setupScript: "old" }
  });
  assert.deepEqual(selected, { baseImageValue: "agent-cli-base", setupScript: "edited" });
});

test("selectProjectBuildDraft falls back to available draft when one source is missing", () => {
  assert.deepEqual(
    selectProjectBuildDraft({
      isEditing: false,
      cachedDraft: { baseImageValue: "agent-cli-base" },
      serverProjectDraft: null
    }),
    { baseImageValue: "agent-cli-base" }
  );
  assert.deepEqual(
    selectProjectBuildDraft({
      isEditing: true,
      cachedDraft: null,
      serverProjectDraft: { baseImageValue: "agent-cli-base" }
    }),
    { baseImageValue: "agent-cli-base" }
  );
});
