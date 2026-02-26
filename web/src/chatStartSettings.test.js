import test from "node:test";
import assert from "node:assert/strict";
import {
  buildChatStartConfig,
  normalizeChatStartSettings,
  normalizeModeOptions,
  resolveProjectChatStartSettings
} from "./chatStartSettings.js";

const TEST_CAPABILITIES = {
  agents: [
    {
      agentType: "codex",
      models: ["default", "gpt-5-codex", "gpt-5-mini"],
      reasoningModes: ["default", "low", "medium", "high"]
    },
    {
      agentType: "claude",
      models: ["default", "claude-3-7-sonnet"],
      reasoningModes: ["default"]
    },
    {
      agentType: "gemini",
      models: ["default", "gemini-2.0-pro"],
      reasoningModes: ["default"]
    }
  ]
};

test("resolveProjectChatStartSettings prefers latest per-project settings over fallback snapshots", () => {
  const resolved = resolveProjectChatStartSettings(
    "project-alpha",
    {
      "project-alpha": {
        agentType: "codex",
        model: "gpt-5-codex",
        reasoning: "high"
      }
    },
    "codex",
    TEST_CAPABILITIES,
    {
      agentType: "codex",
      model: "default",
      reasoning: "default"
    }
  );

  assert.deepEqual(resolved, {
    agentType: "codex",
    model: "gpt-5-codex",
    reasoning: "high"
  });
});

test("resolveProjectChatStartSettings falls back when a project has no saved settings", () => {
  const resolved = resolveProjectChatStartSettings(
    "project-missing",
    {},
    "codex",
    TEST_CAPABILITIES,
    {
      agentType: "claude",
      model: "claude-3-7-sonnet",
      reasoning: "high"
    }
  );

  assert.deepEqual(resolved, {
    agentType: "claude",
    model: "claude-3-7-sonnet",
    reasoning: "default"
  });
});

test("buildChatStartConfig includes codex model and reasoning flags", () => {
  const payload = buildChatStartConfig(
    {
      agentType: "codex",
      model: "gpt-5-mini",
      reasoning: "medium"
    },
    TEST_CAPABILITIES
  );

  assert.deepEqual(payload, {
    agentType: "codex",
    agentArgs: ["--model", "gpt-5-mini", "-c", "model_reasoning_effort=\"medium\""]
  });
});

test("buildChatStartConfig does not pass default codex model flag", () => {
  const payload = buildChatStartConfig(
    {
      agentType: "codex",
      model: "default",
      reasoning: "default"
    },
    TEST_CAPABILITIES
  );

  assert.deepEqual(payload, {
    agentType: "codex",
    agentArgs: []
  });
});

test("non-codex agents ignore reasoning mode and normalize invalid model selections", () => {
  const normalized = normalizeChatStartSettings(
    {
      agentType: "gemini",
      model: "unknown-model",
      reasoning: "high"
    },
    TEST_CAPABILITIES
  );
  assert.deepEqual(normalized, {
    agentType: "gemini",
    model: "default",
    reasoning: "default"
  });

  const payload = buildChatStartConfig(
    {
      agentType: "claude",
      model: "claude-3-7-sonnet",
      reasoning: "high"
    },
    TEST_CAPABILITIES
  );
  assert.deepEqual(payload, {
    agentType: "claude",
    agentArgs: ["--model", "claude-3-7-sonnet"]
  });
});

const FULL_CAPABILITIES = {
  agents: [
    {
      agentType: "codex",
      models: ["default", "gpt-5-codex", "gpt-5-mini"],
      reasoningModes: ["default", "low", "medium", "high"]
    },
    {
      agentType: "claude",
      models: ["default", "claude-3-7-sonnet"],
      reasoningModes: ["default", "low", "medium", "high"]
    },
    {
      agentType: "gemini",
      models: ["default", "gemini-2.0-pro"],
      reasoningModes: ["default", "low", "medium", "high"]
    }
  ]
};

test("buildChatStartConfig passes --effort flag for claude when reasoning is non-default", () => {
  const payload = buildChatStartConfig(
    {
      agentType: "claude",
      model: "claude-3-7-sonnet",
      reasoning: "high"
    },
    FULL_CAPABILITIES
  );
  assert.deepEqual(payload, {
    agentType: "claude",
    agentArgs: ["--model", "claude-3-7-sonnet", "--effort", "high"]
  });
});

test("buildChatStartConfig passes --thinking-level flag for gemini when reasoning is non-default", () => {
  const payload = buildChatStartConfig(
    {
      agentType: "gemini",
      model: "default",
      reasoning: "medium"
    },
    FULL_CAPABILITIES
  );
  assert.deepEqual(payload, {
    agentType: "gemini",
    agentArgs: ["--thinking-level", "medium"]
  });
});

test("buildChatStartConfig omits reasoning flags for all agents when set to default", () => {
  for (const agentType of ["codex", "claude", "gemini"]) {
    const payload = buildChatStartConfig(
      { agentType, model: "default", reasoning: "default" },
      FULL_CAPABILITIES
    );
    assert.deepEqual(payload.agentArgs, [], `expected no reasoning args for ${agentType} with default`);
  }
});

test("normalizeModeOptions prepends default and de-duplicates values case-insensitively", () => {
  const options = normalizeModeOptions(["Default", " GPT-5 ", "gpt-5", "gpt-5-mini"], ["default"]);
  assert.deepEqual(options, ["default", "gpt-5", "gpt-5-mini"]);
});
