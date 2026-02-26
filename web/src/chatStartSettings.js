const DEFAULT_AGENT_TYPE = "codex";

const DEFAULT_AGENT_CAPABILITIES = {
  agents: [
    {
      agentType: "codex",
      models: ["default"],
      reasoningModes: ["default"]
    },
    {
      agentType: "claude",
      models: ["default"],
      reasoningModes: ["default"]
    },
    {
      agentType: "gemini",
      models: ["default"],
      reasoningModes: ["default"]
    }
  ]
};

export function normalizeModeOptions(rawOptions, fallbackOptions) {
  const source = Array.isArray(rawOptions) ? rawOptions : fallbackOptions;
  const values = source.map((item) => String(item || "").trim().toLowerCase()).filter(Boolean);
  const unique = [];
  const seen = new Set();
  for (const value of values) {
    if (seen.has(value)) {
      continue;
    }
    unique.push(value);
    seen.add(value);
  }
  if (!seen.has("default")) {
    return ["default", ...unique];
  }
  return ["default", ...unique.filter((value) => value !== "default")];
}

export function normalizeAgentType(value, capabilities = DEFAULT_AGENT_CAPABILITIES, fallbackAgentType = DEFAULT_AGENT_TYPE) {
  const normalized = String(value || "").trim().toLowerCase();
  if (!normalized || normalized === "default") {
    return fallbackAgentType;
  }
  const knownTypes = (capabilities?.agents || [])
    .map((agent) => String(agent?.agentType || "").trim().toLowerCase())
    .filter((agentType) => Boolean(agentType && agentType !== "default"));
  if (knownTypes.includes(normalized)) {
    return normalized;
  }
  return fallbackAgentType;
}

export function agentCapabilityForType(
  agentType,
  capabilities = DEFAULT_AGENT_CAPABILITIES,
  fallbackAgentType = DEFAULT_AGENT_TYPE
) {
  const resolvedType = normalizeAgentType(agentType, capabilities, fallbackAgentType);
  const matched = (capabilities?.agents || []).find(
    (agent) => String(agent?.agentType || "").trim().toLowerCase() === resolvedType
  );
  if (matched) {
    return matched;
  }
  return (DEFAULT_AGENT_CAPABILITIES.agents || []).find((agent) => agent.agentType === fallbackAgentType) || null;
}

export function startModelOptionsForAgent(
  agentType,
  capabilities = DEFAULT_AGENT_CAPABILITIES,
  fallbackAgentType = DEFAULT_AGENT_TYPE
) {
  const details = agentCapabilityForType(agentType, capabilities, fallbackAgentType);
  return normalizeModeOptions(details?.models, ["default"]);
}

export function reasoningModeOptionsForAgent(
  agentType,
  capabilities = DEFAULT_AGENT_CAPABILITIES,
  fallbackAgentType = DEFAULT_AGENT_TYPE
) {
  const details = agentCapabilityForType(agentType, capabilities, fallbackAgentType);
  return normalizeModeOptions(details?.reasoningModes, ["default"]);
}

export function normalizeStartModel(
  agentType,
  value,
  capabilities = DEFAULT_AGENT_CAPABILITIES,
  fallbackAgentType = DEFAULT_AGENT_TYPE
) {
  const options = startModelOptionsForAgent(agentType, capabilities, fallbackAgentType);
  const normalized = String(value || "").trim().toLowerCase();
  if (options.includes(normalized)) {
    return normalized;
  }
  return "default";
}

export function normalizeReasoningMode(
  agentType,
  value,
  capabilities = DEFAULT_AGENT_CAPABILITIES,
  fallbackAgentType = DEFAULT_AGENT_TYPE
) {
  const options = reasoningModeOptionsForAgent(agentType, capabilities, fallbackAgentType);
  const normalized = String(value || "").trim().toLowerCase();
  if (options.includes(normalized)) {
    return normalized;
  }
  return "default";
}

export function normalizeChatStartSettings(
  value,
  capabilities = DEFAULT_AGENT_CAPABILITIES,
  fallbackAgentType = DEFAULT_AGENT_TYPE
) {
  const resolvedAgentType = normalizeAgentType(
    value?.agentType || value?.agent_type,
    capabilities,
    fallbackAgentType
  );
  return {
    agentType: resolvedAgentType,
    model: normalizeStartModel(resolvedAgentType, value?.model, capabilities, fallbackAgentType),
    reasoning: normalizeReasoningMode(resolvedAgentType, value?.reasoning, capabilities, fallbackAgentType)
  };
}

export function resolveProjectChatStartSettings(
  projectId,
  startSettingsByProject,
  defaultAgentType = DEFAULT_AGENT_TYPE,
  capabilities = DEFAULT_AGENT_CAPABILITIES,
  fallbackStartSettings = null
) {
  const sourceFromProject = startSettingsByProject && typeof startSettingsByProject === "object"
    ? startSettingsByProject[projectId]
    : null;
  const source = sourceFromProject || fallbackStartSettings || { agentType: defaultAgentType };
  return normalizeChatStartSettings(source, capabilities, defaultAgentType);
}

export function buildChatStartConfig(
  value,
  capabilities = DEFAULT_AGENT_CAPABILITIES,
  fallbackAgentType = DEFAULT_AGENT_TYPE
) {
  const normalized = normalizeChatStartSettings(value, capabilities, fallbackAgentType);
  const args = [];
  if (normalized.model !== "default") {
    args.push("--model", normalized.model);
  }
  if (normalized.reasoning !== "default") {
    if (normalized.agentType === "codex") {
      args.push("-c", `model_reasoning_effort="${normalized.reasoning}"`);
    } else if (normalized.agentType === "claude") {
      args.push("--effort", normalized.reasoning);
    } else if (normalized.agentType === "gemini") {
      args.push("--thinking-level", normalized.reasoning);
    }
  }
  return { agentType: normalized.agentType, agentArgs: args };
}
