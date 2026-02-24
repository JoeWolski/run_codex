import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { FitAddon } from "@xterm/addon-fit";
import { WebLinksAddon } from "@xterm/addon-web-links";
import { Terminal } from "@xterm/xterm";
import { Layout, Model } from "flexlayout-react";
import {
  findMatchingServerChatForPendingSession,
  isChatStarting,
  reconcilePendingChatStarts,
  reconcilePendingSessions
} from "./chatPendingState";
import {
  chatLayoutEngineOptions,
  CHAT_LAYOUT_ENGINE_CLASSIC,
  CHAT_LAYOUT_ENGINE_FLEXLAYOUT,
  DEFAULT_CHAT_LAYOUT_ENGINE,
  normalizeChatLayoutEngine
} from "./chatLayoutEngines";
import {
  layoutJsonEquals,
  reconcileOuterFlexLayoutJson,
  reconcileProjectChatsFlexLayoutJson
} from "./flexLayoutState";
import {
  isAutoCreateProjectConfigMode,
  normalizeCreateProjectConfigMode,
  shouldShowManualProjectConfigInputs
} from "./createProjectConfigMode";
import {
  buildChatStartConfig,
  normalizeAgentType,
  normalizeChatStartSettings,
  normalizeModeOptions,
  reasoningModeOptionsForAgent,
  resolveProjectChatStartSettings,
  startModelOptionsForAgent
} from "./chatStartSettings";
import { createFirstSeenOrderState, stableOrderItemsByFirstSeen } from "./stableListOrder";
import { buildProjectChatFlexModels } from "./projectChatLayoutModels";
import { chatTerminalSocketStore } from "./chatTerminalSocketStore";
import { terminalThemeForAppTheme } from "./theme";
import {
  markPendingAutoConfigProjectFailed,
  projectRowFromPendingAutoConfig,
  removePendingAutoConfigProject
} from "./autoConfigProjects";
import {
  MdArchive,
  MdAudiotrack,
  MdCode,
  MdDescription,
  MdImage,
  MdInsertDriveFile,
  MdPictureAsPdf,
  MdSlideshow,
  MdTableChart,
  MdTextSnippet,
  MdVideocam
} from "react-icons/md";

const THEME_STORAGE_KEY = "agent_hub_theme";
const CREATE_PROJECT_CONFIG_MODE_STORAGE_KEY = "agent_hub_project_config_mode";
const CHAT_FLEX_OUTER_LAYOUT_STORAGE_KEY = "agent_hub_chat_flexlayout_outer_layout_v1";
const CHAT_FLEX_PROJECT_LAYOUT_STORAGE_KEY = "agent_hub_chat_flexlayout_project_layout_v1";
const CHAT_TERMINAL_COLLAPSE_STORAGE_KEY = "agent_hub_chat_terminal_collapse_v1";
const DEFAULT_BASE_IMAGE_TAG = "ubuntu:24.04";
const DEFAULT_AGENT_TYPE = "codex";
const DEFAULT_HUB_SETTINGS = {
  defaultAgentType: DEFAULT_AGENT_TYPE,
  chatLayoutEngine: DEFAULT_CHAT_LAYOUT_ENGINE
};
const BRAND_LOGO_ASSET_BY_THEME = Object.freeze({
  light: "/branding/agent-hub-mark-light.svg",
  dark: "/branding/agent-hub-mark-dark.svg"
});
const DEFAULT_AGENT_CAPABILITIES = {
  version: 1,
  updatedAt: "",
  discoveryInProgress: false,
  discoveryStartedAt: "",
  discoveryFinishedAt: "",
  agents: [
    {
      agentType: "codex",
      label: "Codex",
      models: ["default"],
      reasoningModes: ["default"],
      updatedAt: "",
      lastError: ""
    },
    {
      agentType: "claude",
      label: "Claude",
      models: ["default"],
      reasoningModes: ["default"],
      updatedAt: "",
      lastError: ""
    },
    {
      agentType: "gemini",
      label: "Gemini CLI",
      models: ["default"],
      reasoningModes: ["default"],
      updatedAt: "",
      lastError: ""
    }
  ]
};

function normalizeHubStatePayload(rawPayload) {
  return {
    projects: Array.isArray(rawPayload?.projects) ? rawPayload.projects : [],
    chats: Array.isArray(rawPayload?.chats) ? rawPayload.chats : [],
    settings: rawPayload?.settings && typeof rawPayload.settings === "object"
      ? rawPayload.settings
      : {
        default_agent_type: DEFAULT_HUB_SETTINGS.defaultAgentType,
        chat_layout_engine: DEFAULT_HUB_SETTINGS.chatLayoutEngine
      }
  };
}

function normalizeHubSettings(rawSettings, capabilities = DEFAULT_AGENT_CAPABILITIES) {
  return {
    defaultAgentType: normalizeAgentType(rawSettings?.defaultAgentType || rawSettings?.default_agent_type, capabilities),
    chatLayoutEngine: normalizeChatLayoutEngine(rawSettings?.chatLayoutEngine || rawSettings?.chat_layout_engine)
  };
}

function normalizeAgentCapabilities(rawPayload) {
  const fallbackByType = new Map(
    (DEFAULT_AGENT_CAPABILITIES.agents || []).map((agent) => [agent.agentType, agent])
  );
  const rawAgents = Array.isArray(rawPayload?.agents) ? rawPayload.agents : [];
  const rawByType = new Map();
  for (const rawAgent of rawAgents) {
    const agentType = String(rawAgent?.agentType || rawAgent?.agent_type || "").trim().toLowerCase();
    if (!agentType) {
      continue;
    }
    rawByType.set(agentType, rawAgent);
  }
  const agents = [];
  for (const fallbackAgent of DEFAULT_AGENT_CAPABILITIES.agents) {
    const rawAgent = rawByType.get(fallbackAgent.agentType) || {};
    agents.push({
      agentType: fallbackAgent.agentType,
      label: String(rawAgent?.label || fallbackAgent.label),
      models: normalizeModeOptions(rawAgent?.models, fallbackAgent.models),
      reasoningModes: normalizeModeOptions(rawAgent?.reasoningModes || rawAgent?.reasoning_modes, fallbackAgent.reasoningModes),
      updatedAt: String(rawAgent?.updatedAt || rawAgent?.updated_at || ""),
      lastError: String(rawAgent?.lastError || rawAgent?.last_error || "")
    });
  }
  for (const [agentType, rawAgent] of rawByType.entries()) {
    if (fallbackByType.has(agentType)) {
      continue;
    }
    agents.push({
      agentType,
      label: String(rawAgent?.label || agentType),
      models: normalizeModeOptions(rawAgent?.models, ["default"]),
      reasoningModes: normalizeModeOptions(rawAgent?.reasoningModes || rawAgent?.reasoning_modes, ["default"]),
      updatedAt: String(rawAgent?.updatedAt || rawAgent?.updated_at || ""),
      lastError: String(rawAgent?.lastError || rawAgent?.last_error || "")
    });
  }
  return {
    version: Number(rawPayload?.version || DEFAULT_AGENT_CAPABILITIES.version),
    updatedAt: String(rawPayload?.updatedAt || rawPayload?.updated_at || ""),
    discoveryInProgress: Boolean(rawPayload?.discoveryInProgress ?? rawPayload?.discovery_in_progress),
    discoveryStartedAt: String(rawPayload?.discoveryStartedAt || rawPayload?.discovery_started_at || ""),
    discoveryFinishedAt: String(rawPayload?.discoveryFinishedAt || rawPayload?.discovery_finished_at || ""),
    agents
  };
}

function agentTypeOptions(capabilities = DEFAULT_AGENT_CAPABILITIES) {
  return (capabilities?.agents || []).map((agent) => ({
    value: String(agent?.agentType || "").trim().toLowerCase(),
    label: String(agent?.label || agent?.agentType || "")
  })).filter((agent) => Boolean(agent.value && agent.label && agent.value !== "default"));
}

function agentTypeLabel(agentType, capabilities = DEFAULT_AGENT_CAPABILITIES) {
  const normalized = normalizeAgentType(agentType, capabilities);
  const matched = agentTypeOptions(capabilities).find((option) => option.value === normalized);
  return matched ? matched.label : "Codex";
}

function normalizeThemePreference(value) {
  const normalized = String(value || "").toLowerCase();
  if (normalized === "light" || normalized === "dark" || normalized === "system") {
    return normalized;
  }
  return "system";
}

function resolveEffectiveTheme(preference, systemPrefersDark = false) {
  const normalized = normalizeThemePreference(preference);
  if (normalized === "light" || normalized === "dark") {
    return normalized;
  }
  return systemPrefersDark ? "dark" : "light";
}

function detectSystemPrefersDark() {
  if (typeof window === "undefined" || typeof window.matchMedia !== "function") {
    return false;
  }
  return window.matchMedia("(prefers-color-scheme: dark)").matches;
}

function loadThemePreference() {
  if (typeof window === "undefined") {
    return "system";
  }
  try {
    return normalizeThemePreference(window.localStorage.getItem(THEME_STORAGE_KEY));
  } catch {
    return "system";
  }
}

function loadCreateProjectConfigMode() {
  if (typeof window === "undefined") {
    return "auto";
  }
  try {
    return normalizeCreateProjectConfigMode(window.localStorage.getItem(CREATE_PROJECT_CONFIG_MODE_STORAGE_KEY));
  } catch {
    return "auto";
  }
}

function readLocalStorageJson(storageKey, fallbackValue) {
  if (typeof window === "undefined") {
    return fallbackValue;
  }
  try {
    const raw = window.localStorage.getItem(storageKey);
    if (!raw) {
      return fallbackValue;
    }
    return JSON.parse(raw);
  } catch {
    return fallbackValue;
  }
}

function writeLocalStorageJson(storageKey, value) {
  if (typeof window === "undefined") {
    return;
  }
  try {
    window.localStorage.setItem(storageKey, JSON.stringify(value));
  } catch {
    // Ignore storage write failures and continue with in-memory state.
  }
}

function layoutJsonMapEquals(left, right) {
  const leftEntries = Object.entries(left || {});
  const rightEntries = Object.entries(right || {});
  if (leftEntries.length !== rightEntries.length) {
    return false;
  }
  for (const [key, leftLayoutJson] of leftEntries) {
    if (!Object.prototype.hasOwnProperty.call(right || {}, key)) {
      return false;
    }
    const rightLayoutJson = right[key];
    if (!layoutJsonEquals(leftLayoutJson || null, rightLayoutJson || null)) {
      return false;
    }
  }
  return true;
}

function applyThemePreference(preference) {
  if (typeof document === "undefined") {
    return;
  }
  const normalized = normalizeThemePreference(preference);
  const root = document.documentElement;
  if (normalized === "system") {
    root.removeAttribute("data-theme");
    return;
  }
  root.setAttribute("data-theme", normalized);
}

function logoAssetForTheme(theme) {
  const normalized = String(theme || "").toLowerCase();
  return normalized === "dark" ? BRAND_LOGO_ASSET_BY_THEME.dark : BRAND_LOGO_ASSET_BY_THEME.light;
}

function applyFaviconForTheme(theme) {
  if (typeof document === "undefined") {
    return;
  }
  const faviconHref = logoAssetForTheme(theme);
  let faviconLink = document.querySelector('link[data-agent-hub-favicon="app"]');
  if (!faviconLink) {
    faviconLink = document.createElement("link");
    faviconLink.setAttribute("rel", "icon");
    faviconLink.setAttribute("type", "image/svg+xml");
    faviconLink.setAttribute("data-agent-hub-favicon", "app");
    document.head.appendChild(faviconLink);
  }
  if (faviconLink.getAttribute("href") !== faviconHref) {
    faviconLink.setAttribute("href", faviconHref);
  }
}

function emptyVolume() {
  return { host: "", container: "", mode: "ro" };
}

function emptyEnvVar() {
  return { key: "", value: "" };
}

function emptyCreateForm() {
  return {
    repoUrl: "",
    name: "",
    defaultBranch: "",
    baseImageMode: "tag",
    baseImageValue: "",
    setupScript: "",
    defaultVolumes: [],
    defaultEnvVars: []
  };
}

function extractProjectNameFromRepoUrl(repoUrl) {
  const raw = String(repoUrl || "").trim();
  if (!raw) {
    return "project";
  }
  const trimmed = raw.replace(/[\\/]+$/, "");
  const lastSlash = Math.max(trimmed.lastIndexOf("/"), trimmed.lastIndexOf(":"));
  const tail = lastSlash >= 0 ? trimmed.slice(lastSlash + 1) : trimmed;
  const withoutGit = tail.replace(/\.git$/i, "");
  return withoutGit || "project";
}

function normalizeBaseMode(mode) {
  return mode === "repo_path" ? "repo_path" : "tag";
}

function baseModeLabel(mode) {
  return mode === "repo_path" ? "Repo path" : "Docker tag";
}

function baseInputPlaceholder(mode) {
  if (mode === "repo_path") {
    return "Path in repo to Dockerfile or dir (e.g. docker/base or docker/base/Dockerfile)";
  }
  return DEFAULT_BASE_IMAGE_TAG;
}

function parseMountEntry(spec, mode) {
  if (typeof spec !== "string") {
    return null;
  }
  const idx = spec.indexOf(":");
  if (idx <= 0 || idx === spec.length - 1) {
    return null;
  }
  return {
    host: spec.slice(0, idx),
    container: spec.slice(idx + 1),
    mode: mode === "ro" ? "ro" : "rw"
  };
}

function mountRowsFromArrays(roMounts, rwMounts) {
  const rows = [];
  for (const spec of roMounts || []) {
    const parsed = parseMountEntry(spec, "ro");
    if (parsed) {
      rows.push(parsed);
    }
  }
  for (const spec of rwMounts || []) {
    const parsed = parseMountEntry(spec, "rw");
    if (parsed) {
      rows.push(parsed);
    }
  }
  return rows;
}

function envRowsFromArray(entries) {
  const rows = [];
  for (const entry of entries || []) {
    const idx = entry.indexOf("=");
    if (idx < 0) {
      rows.push({ key: entry, value: "" });
      continue;
    }
    rows.push({ key: entry.slice(0, idx), value: entry.slice(idx + 1) });
  }
  return rows;
}

function buildMountPayload(rows) {
  const roMounts = [];
  const rwMounts = [];
  for (const row of rows || []) {
    const host = String(row.host || "").trim();
    const container = String(row.container || "").trim();
    const mode = row.mode === "ro" ? "ro" : "rw";
    if (!host && !container) {
      continue;
    }
    if (!host || !container) {
      throw new Error("Each volume needs both local path and container path.");
    }
    const entry = `${host}:${container}`;
    if (mode === "ro") {
      roMounts.push(entry);
    } else {
      rwMounts.push(entry);
    }
  }
  return { roMounts, rwMounts };
}

function buildEnvPayload(rows) {
  const envVars = [];
  for (const row of rows || []) {
    const key = String(row.key || "").trim();
    const value = String(row.value || "");
    if (!key && !value) {
      continue;
    }
    if (!key) {
      throw new Error("Environment variable key is required.");
    }
    envVars.push(`${key}=${value}`);
  }
  return envVars;
}

function normalizeStringArray(rawValue, fallback = []) {
  if (!Array.isArray(rawValue)) {
    return [...fallback];
  }
  return rawValue.map((item) => String(item || "").trim()).filter(Boolean);
}

function setupCommandCount(text) {
  return String(text || "")
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean).length;
}

function formatBytes(value) {
  const size = Number(value);
  if (!Number.isFinite(size) || size < 0) {
    return "0 B";
  }
  if (size < 1024) {
    return `${size} B`;
  }
  const units = ["KB", "MB", "GB", "TB"];
  let normalized = size / 1024;
  let unitIndex = 0;
  while (normalized >= 1024 && unitIndex < units.length - 1) {
    normalized /= 1024;
    unitIndex += 1;
  }
  return `${normalized.toFixed(normalized >= 10 ? 0 : 1)} ${units[unitIndex]}`;
}

const FALLBACK_LIBRARY_ICON_EXTENSIONS = {
  code: new Set([
    "bash",
    "c",
    "cc",
    "cfg",
    "clj",
    "conf",
    "cpp",
    "cs",
    "css",
    "cxx",
    "dockerfile",
    "go",
    "h",
    "hpp",
    "html",
    "ini",
    "ipynb",
    "java",
    "js",
    "json",
    "jsx",
    "kt",
    "kts",
    "less",
    "lua",
    "m",
    "mdx",
    "php",
    "ps1",
    "py",
    "r",
    "rb",
    "rs",
    "sass",
    "scala",
    "scss",
    "sh",
    "sql",
    "swift",
    "toml",
    "ts",
    "tsx",
    "xml",
    "yaml",
    "yml",
    "zsh"
  ]),
  image: new Set(["avif", "bmp", "gif", "heic", "ico", "jpeg", "jpg", "png", "svg", "tif", "tiff", "webp"]),
  pdf: new Set(["pdf"]),
  archive: new Set([
    "7z",
    "bz2",
    "gz",
    "iso",
    "jar",
    "rar",
    "tar",
    "tar.bz2",
    "tar.gz",
    "tar.xz",
    "tgz",
    "tbz2",
    "txz",
    "war",
    "xz",
    "zip",
    "zst"
  ]),
  audio: new Set(["aac", "flac", "m4a", "mp3", "ogg", "opus", "wav", "wma"]),
  video: new Set(["avi", "flv", "m4v", "mkv", "mov", "mp4", "webm", "wmv"]),
  table: new Set(["csv", "ods", "tsv", "xls", "xlsx"]),
  slide: new Set(["key", "odp", "ppt", "pptx"]),
  document: new Set(["doc", "docx", "odt", "rtf"]),
  text: new Set(["log", "md", "rst", "txt"]),
  binary: new Set(["bin", "dat", "exe", "dll", "so", "o", "a"])
};
const FALLBACK_LIBRARY_ICON_COMPONENTS = {
  code: MdCode,
  image: MdImage,
  pdf: MdPictureAsPdf,
  archive: MdArchive,
  audio: MdAudiotrack,
  video: MdVideocam,
  table: MdTableChart,
  slide: MdSlideshow,
  document: MdDescription,
  text: MdTextSnippet,
  binary: MdInsertDriveFile
};
const FALLBACK_LIBRARY_ICON_COLORS = {
  code: { background: "#2563eb", foreground: "#f8fafc", border: "#1d4ed8" },
  image: { background: "#16a34a", foreground: "#f0fdf4", border: "#15803d" },
  pdf: { background: "#dc2626", foreground: "#fef2f2", border: "#b91c1c" },
  archive: { background: "#f59e0b", foreground: "#451a03", border: "#d97706" },
  audio: { background: "#7c3aed", foreground: "#faf5ff", border: "#6d28d9" },
  video: { background: "#db2777", foreground: "#fdf2f8", border: "#be185d" },
  table: { background: "#0ea5e9", foreground: "#f0f9ff", border: "#0284c7" },
  slide: { background: "#f97316", foreground: "#fff7ed", border: "#ea580c" },
  document: { background: "#14b8a6", foreground: "#f0fdfa", border: "#0f766e" },
  text: { background: "#0f766e", foreground: "#ecfeff", border: "#0e7490" },
  binary: { background: "#c2410c", foreground: "#fff7ed", border: "#9a3412" }
};
const FALLBACK_LIBRARY_DEFAULT_COLORS = { background: "#0e7490", foreground: "#f0f9ff", border: "#155e75" };

function artifactIconCandidates(artifact) {
  const uniqueCandidates = new Set();

  function addCandidate(value) {
    const candidate = String(value || "").trim().toLowerCase().replace(/^\./, "");
    if (candidate) {
      uniqueCandidates.add(candidate);
    }
  }

  function collectFromPath(rawPath) {
    const normalized = String(rawPath || "").trim();
    if (!normalized) {
      return;
    }
    const basename = normalized.split(/[\\/]/).pop() || "";
    const lowered = basename.toLowerCase();
    if (!lowered) {
      return;
    }
    addCandidate(lowered);
    const parts = lowered.split(".").filter(Boolean);
    if (parts.length <= 1) {
      return;
    }
    for (let index = 1; index < parts.length; index += 1) {
      addCandidate(parts.slice(index).join("."));
    }
    addCandidate(parts[parts.length - 1]);
  }

  collectFromPath(artifact?.name);
  collectFromPath(artifact?.relative_path);
  return uniqueCandidates;
}

function fallbackLibraryIconVariant(candidates) {
  for (const candidate of candidates) {
    const extension = candidate.includes(".") ? candidate.split(".").pop() : candidate;
    if (!extension) {
      continue;
    }
    for (const [variant, knownExtensions] of Object.entries(FALLBACK_LIBRARY_ICON_EXTENSIONS)) {
      if (knownExtensions.has(candidate) || knownExtensions.has(extension)) {
        return variant;
      }
    }
  }
  return null;
}

function resolveArtifactIcon(artifact) {
  const candidates = artifactIconCandidates(artifact);
  const fallbackVariant = fallbackLibraryIconVariant(candidates);
  if (fallbackVariant) {
    return { variant: fallbackVariant };
  }
  return { variant: "binary" };
}

function renderArtifactIcon(iconDescriptor) {
  const IconComponent = FALLBACK_LIBRARY_ICON_COMPONENTS[iconDescriptor.variant] || MdInsertDriveFile;
  const colors = FALLBACK_LIBRARY_ICON_COLORS[iconDescriptor.variant] || FALLBACK_LIBRARY_DEFAULT_COLORS;
  return (
    <span
      className="chat-artifact-icon-fallback"
      style={{
        backgroundColor: colors.background,
        color: colors.foreground,
        borderColor: colors.border
      }}
      aria-hidden="true"
    >
      <IconComponent />
    </span>
  );
}

function projectStatusInfo(buildStatus) {
  if (buildStatus === "ready") {
    return { key: "ready", label: "Ready" };
  }
  if (buildStatus === "building") {
    return { key: "building", label: "Building image" };
  }
  if (buildStatus === "failed") {
    return { key: "failed", label: "Build failed" };
  }
  return { key: "pending", label: "Needs build" };
}

function SpinnerLabel({ text }) {
  return (
    <>
      <span className="inline-spinner" aria-hidden="true" />
      <span>{text}</span>
    </>
  );
}

async function fetchJson(url, options = {}) {
  const response = await fetch(url, {
    headers: { "Content-Type": "application/json" },
    ...options
  });
  if (!response.ok) {
    const text = await response.text();
    throw new Error(text || `Request failed with status ${response.status}`);
  }
  if (response.status === 204) {
    return null;
  }
  return response.json();
}

async function fetchText(url) {
  const response = await fetch(url);
  if (!response.ok) {
    const text = await response.text();
    throw new Error(text || `Request failed with status ${response.status}`);
  }
  return response.text();
}

function hubEventsSocketUrl() {
  const protocol = window.location.protocol === "https:" ? "wss" : "ws";
  return `${protocol}://${window.location.host}/api/events`;
}

function openTerminalUrlInNewTab(event, rawUri) {
  event?.preventDefault?.();
  event?.stopPropagation?.();
  const uri = String(rawUri || "").trim();
  if (!uri) {
    return;
  }
  let parsedUri;
  try {
    parsedUri = new URL(uri);
  } catch {
    return;
  }
  const protocol = parsedUri.protocol.toLowerCase();
  if (protocol !== "http:" && protocol !== "https:") {
    return;
  }
  const openedWindow = window.open(parsedUri.toString(), "_blank", "noopener,noreferrer");
  if (openedWindow) {
    try {
      openedWindow.opener = null;
    } catch {
      // Ignore environments that reject opener mutation.
    }
  }
}

function ExpandIcon() {
  return (
    <svg viewBox="0 0 20 20" aria-hidden="true" focusable="false">
      <path
        d="M7.5 3.75H3.75V7.5M12.5 3.75h3.75V7.5M7.5 16.25H3.75V12.5M12.5 16.25h3.75V12.5M7.5 3.75 3.75 7.5M12.5 3.75 16.25 7.5M7.5 16.25 3.75 12.5M12.5 16.25 16.25 12.5"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function MinimizeIcon() {
  return (
    <svg viewBox="0 0 20 20" aria-hidden="true" focusable="false">
      <path
        d="M7.5 3.75H3.75V7.5M12.5 3.75h3.75V7.5M7.5 16.25H3.75V12.5M12.5 16.25h3.75V12.5M3.75 3.75 8 8M16.25 3.75 12 8M3.75 16.25 8 12M16.25 16.25 12 12"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function EllipsisIcon() {
  return (
    <svg viewBox="0 0 20 20" aria-hidden="true" focusable="false">
      <circle cx="5.5" cy="10" r="1.25" fill="currentColor" />
      <circle cx="10" cy="10" r="1.25" fill="currentColor" />
      <circle cx="14.5" cy="10" r="1.25" fill="currentColor" />
    </svg>
  );
}

function CloseIcon() {
  return (
    <svg viewBox="0 0 20 20" aria-hidden="true" focusable="false">
      <path
        d="M5.5 5.5 14.5 14.5M14.5 5.5 5.5 14.5"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.75"
        strokeLinecap="round"
      />
    </svg>
  );
}

function InfoIcon() {
  return (
    <svg viewBox="0 0 20 20" aria-hidden="true" focusable="false">
      <circle cx="10" cy="10" r="6.25" fill="none" stroke="currentColor" strokeWidth="1.5" />
      <circle cx="10" cy="7.15" r="1.05" fill="currentColor" />
      <path
        d="M10 9.9v4.35"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinecap="round"
      />
    </svg>
  );
}

function RefreshWarningIcon() {
  return (
    <svg viewBox="0 0 20 20" aria-hidden="true" focusable="false">
      <path
        d="M14.7 5.7A5.5 5.5 0 0 0 5.3 7.4M5.3 14.3A5.5 5.5 0 0 0 14.7 12.6"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.4"
        strokeLinecap="round"
      />
      <path
        d="M14.7 5.7v2.3h-2.3M5.3 14.3V12h2.3"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.4"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <path
        d="M10 7.25v3.55"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.6"
        strokeLinecap="round"
      />
      <circle cx="10" cy="12.95" r="0.85" fill="currentColor" />
    </svg>
  );
}

function DownloadArrowIcon() {
  return (
    <svg viewBox="0 0 20 20" aria-hidden="true" focusable="false">
      <path
        d="M10 3.75v8.5m0 0 3-3m-3 3-3-3M4.5 14.25h11"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.75"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function ChatTerminal({
  chatId,
  running,
  title = "",
  titleStateLabel = "",
  titleStateClassName = "",
  statusOverride = "",
  toolbarActions = null,
  showToolbar = true,
  overlay = null,
  collapsed = false,
  tabs = [],
  activeTabId = "",
  onTabSelect = null,
  effectiveTheme = "light"
}) {
  const shellRef = useRef(null);
  const hostRef = useRef(null);
  const terminalRef = useRef(null);
  const [status, setStatus] = useState(running ? "connecting" : "offline");
  const terminalTheme = useMemo(() => terminalThemeForAppTheme(effectiveTheme), [effectiveTheme]);
  const statusText = String(status || "unknown").toLowerCase();
  const displayStatus = String(statusOverride || statusText || "unknown").toLowerCase();
  const hasTabs = Array.isArray(tabs) && tabs.length > 0;
  const renderToolbar = Boolean(showToolbar);

  useEffect(() => {
    chatTerminalSocketStore.setRunning(chatId, Boolean(running));
    if (!running) {
      setStatus("offline");
      return undefined;
    }
    if (!hostRef.current) {
      return undefined;
    }

    const terminal = new Terminal({
      convertEol: true,
      cursorBlink: true,
      fontFamily: "ui-monospace, SFMono-Regular, Menlo, Monaco, monospace",
      fontSize: 13,
      scrollback: 5000,
      theme: terminalTheme
    });
    const fitAddon = new FitAddon();
    terminal.loadAddon(fitAddon);
    terminal.loadAddon(new WebLinksAddon(openTerminalUrlInNewTab));
    terminal.open(hostRef.current);
    terminalRef.current = terminal;
    fitAddon.fit();
    terminal.focus();
    let resizeFrame = null;

    const sendInput = (text) => {
      const payload = String(text || "");
      if (!payload) {
        return false;
      }
      return chatTerminalSocketStore.sendInput(chatId, payload);
    };

    const sendPasteText = (text) => {
      const normalized = String(text || "").replace(/\r\n/g, "\n");
      if (!normalized) {
        return false;
      }
      return sendInput(normalized);
    };

    const sendResize = (cols = terminal.cols, rows = terminal.rows, force = false) => {
      const nextCols = Math.max(1, Number(cols) || 1);
      const nextRows = Math.max(1, Number(rows) || 1);
      chatTerminalSocketStore.sendResize(chatId, nextCols, nextRows, { force });
    };

    const fitAndResize = (force = false) => {
      fitAddon.fit();
      sendResize(terminal.cols, terminal.rows, force);
    };

    const scheduleFitAndResize = (force = false) => {
      if (resizeFrame != null) {
        cancelAnimationFrame(resizeFrame);
      }
      resizeFrame = requestAnimationFrame(() => {
        resizeFrame = null;
        fitAndResize(force);
      });
    };

    const inputDisposable = terminal.onData((data) => {
      sendInput(data);
    });
    const keyDisposable = terminal.onKey(({ domEvent }) => {
      if (
        domEvent.key === "Enter" &&
        !domEvent.shiftKey &&
        !domEvent.altKey &&
        !domEvent.ctrlKey &&
        !domEvent.metaKey
      ) {
        chatTerminalSocketStore.sendSubmit(chatId);
      }
    });
    const unsubscribe = chatTerminalSocketStore.subscribe(chatId, {
      onStatus: (nextStatus) => {
        setStatus(nextStatus);
        if (nextStatus === "connected") {
          fitAndResize(true);
          terminal.focus();
        }
      },
      onBacklog: (backlog) => {
        if (backlog) {
          terminal.write(backlog);
        }
      },
      onData: (chunk) => {
        if (chunk) {
          terminal.write(chunk);
        }
      }
    });
    const onTerminalPointerDown = () => {
      terminal.focus();
    };
    const onShellPaste = (event) => {
      const clipboardText = event.clipboardData?.getData("text");
      if (!clipboardText) {
        return;
      }
      if (sendPasteText(clipboardText)) {
        event.preventDefault();
      }
    };

    const shellElement = shellRef.current;
    const terminalViewElement = hostRef.current;
    if (terminalViewElement) {
      terminalViewElement.addEventListener("pointerdown", onTerminalPointerDown);
    }
    if (shellElement) {
      shellElement.addEventListener("paste", onShellPaste);
    }

    let resizeObserver;
    let onWindowResize = null;
    if (typeof ResizeObserver !== "undefined" && shellElement) {
      resizeObserver = new ResizeObserver(() => {
        scheduleFitAndResize();
      });
      resizeObserver.observe(shellElement);
    } else {
      onWindowResize = () => {
        scheduleFitAndResize();
      };
      window.addEventListener("resize", onWindowResize);
    }

    return () => {
      unsubscribe();
      if (resizeObserver) {
        resizeObserver.disconnect();
      }
      if (onWindowResize) {
        window.removeEventListener("resize", onWindowResize);
      }
      if (terminalViewElement) {
        terminalViewElement.removeEventListener("pointerdown", onTerminalPointerDown);
      }
      if (shellElement) {
        shellElement.removeEventListener("paste", onShellPaste);
      }
      if (resizeFrame != null) {
        cancelAnimationFrame(resizeFrame);
      }
      inputDisposable.dispose();
      keyDisposable.dispose();
      terminalRef.current = null;
      terminal.dispose();
    };
  }, [chatId, running]);

  useEffect(() => {
    if (!terminalRef.current) {
      return;
    }
    terminalRef.current.options.theme = terminalTheme;
  }, [terminalTheme]);

  const shellClasses = [
    "terminal-shell",
    "chat-terminal-shell",
    renderToolbar ? "" : "terminal-shell-no-toolbar",
    collapsed ? "terminal-shell-collapsed" : ""
  ]
    .filter(Boolean)
    .join(" ");
  const viewClasses = [
    "terminal-view",
    renderToolbar ? "" : "terminal-view-no-toolbar",
    collapsed ? "terminal-view-collapsed" : ""
  ]
    .filter(Boolean)
    .join(" ");

  return (
    <div className={shellClasses} ref={shellRef}>
      {renderToolbar ? (
        <div className="terminal-toolbar">
          <div className="terminal-toolbar-main">
            <span
              className={`terminal-health-dot ${displayStatus}`}
              role="img"
              aria-label={`Terminal health: ${displayStatus}`}
              title={displayStatus}
            />
            {hasTabs ? (
              <div className="terminal-toolbar-tabs" role="tablist" aria-label="Chat tabs">
                {tabs.map((tab) => {
                  const tabId = String(tab?.id || "");
                  const tabLabel = String(tab?.label || tabId || "Chat");
                  const isSelected = tabId && tabId === String(activeTabId || "");
                  return (
                    <button
                      key={`terminal-tab-${tabId || tabLabel}`}
                      type="button"
                      role="tab"
                      className={`terminal-toolbar-tab ${isSelected ? "selected" : ""}`.trim()}
                      aria-selected={isSelected}
                      aria-label={tabLabel}
                      onClick={() => {
                        if (typeof onTabSelect === "function" && tabId) {
                          onTabSelect(tabId);
                        }
                      }}
                    >
                      {tabLabel}
                    </button>
                  );
                })}
              </div>
            ) : title ? (
              <span className="terminal-title" title={title}>
                {title}
              </span>
            ) : null}
            {titleStateLabel ? (
              <span className={`chat-title-state ${titleStateClassName}`.trim()}>
                {titleStateLabel}
              </span>
            ) : null}
          </div>
          {toolbarActions ? (
            <div className="terminal-toolbar-actions">
              {toolbarActions}
            </div>
          ) : null}
        </div>
      ) : null}
      <div className={viewClasses} ref={hostRef} />
      {!collapsed && overlay ? <div className="terminal-overlay">{overlay}</div> : null}
    </div>
  );
}

function ProjectBuildTerminal({
  text,
  title = "Image build output",
  shellClassName = "",
  viewClassName = "",
  effectiveTheme = "light"
}) {
  const hostRef = useRef(null);
  const terminalRef = useRef(null);
  const fitRef = useRef(null);
  const terminalTheme = useMemo(() => terminalThemeForAppTheme(effectiveTheme), [effectiveTheme]);
  const shellClasses = ["terminal-shell", "project-build-shell", shellClassName].filter(Boolean).join(" ");
  const viewClasses = ["terminal-view", "project-build-view", viewClassName].filter(Boolean).join(" ");

  useEffect(() => {
    if (!hostRef.current) {
      return undefined;
    }

    const terminal = new Terminal({
      convertEol: true,
      cursorBlink: false,
      disableStdin: true,
      fontFamily: "ui-monospace, SFMono-Regular, Menlo, Monaco, monospace",
      fontSize: 12,
      scrollback: 10000,
      theme: terminalTheme
    });
    const fitAddon = new FitAddon();
    terminal.loadAddon(fitAddon);
    terminal.open(hostRef.current);
    fitAddon.fit();
    terminalRef.current = terminal;
    fitRef.current = fitAddon;

    const onResize = () => fitAddon.fit();
    window.addEventListener("resize", onResize);
    return () => {
      window.removeEventListener("resize", onResize);
      terminalRef.current = null;
      fitRef.current = null;
      terminal.dispose();
    };
  }, []);

  useEffect(() => {
    const terminal = terminalRef.current;
    if (!terminal) {
      return;
    }
    terminal.reset();
    terminal.write(text || "Preparing project image...\r\n");
    terminal.scrollToBottom();
    fitRef.current?.fit();
  }, [text]);

  useEffect(() => {
    if (!terminalRef.current) {
      return;
    }
    terminalRef.current.options.theme = terminalTheme;
  }, [terminalTheme]);

  return (
    <div className={shellClasses}>
      <div className="terminal-toolbar">
        <span className="terminal-title">{title}</span>
      </div>
      <div className={viewClasses} ref={hostRef} />
    </div>
  );
}

function VolumeEditor({ rows, onChange }) {
  function updateRow(index, patch) {
    const next = [...rows];
    next[index] = { ...next[index], ...patch };
    onChange(next);
  }

  function removeRow(index) {
    onChange(rows.filter((_, i) => i !== index));
  }

  function addRow() {
    onChange([...rows, emptyVolume()]);
  }

  return (
    <div className="widget-block">
      {rows.map((row, index) => (
        <div className="widget-row volume" key={`volume-${index}`}>
          <div className="widget-row-fields">
            <input
              value={row.host}
              onChange={(event) => updateRow(index, { host: event.target.value })}
              placeholder="Local path (e.g. /data/datasets)"
            />
            <input
              value={row.container}
              onChange={(event) => updateRow(index, { container: event.target.value })}
              placeholder="Container path (e.g. /workspace/data)"
            />
            <div className="volume-mode-control">
              <select
                className="volume-mode-select"
                value={row.mode}
                onChange={(event) => updateRow(index, { mode: event.target.value })}
                aria-label="Volume mode"
              >
                <option value="ro">Read-only</option>
                <option value="rw">Read-write</option>
              </select>
            </div>
          </div>
          <button
            type="button"
            className="icon-button widget-row-remove"
            onClick={() => removeRow(index)}
            aria-label="Remove volume"
          >
            <CloseIcon />
          </button>
        </div>
      ))}
      <button type="button" className="btn-secondary btn-small" onClick={addRow}>
        Add volume
      </button>
    </div>
  );
}

function EnvVarEditor({ rows, onChange }) {
  function updateRow(index, patch) {
    const next = [...rows];
    next[index] = { ...next[index], ...patch };
    onChange(next);
  }

  function removeRow(index) {
    onChange(rows.filter((_, i) => i !== index));
  }

  function addRow() {
    onChange([...rows, emptyEnvVar()]);
  }

  return (
    <div className="widget-block">
      {rows.map((row, index) => (
        <div className="widget-row env" key={`env-${index}`}>
          <div className="widget-row-fields">
            <input
              value={row.key}
              onChange={(event) => updateRow(index, { key: event.target.value })}
              placeholder="KEY"
            />
            <input
              value={row.value}
              onChange={(event) => updateRow(index, { value: event.target.value })}
              placeholder="VALUE"
            />
          </div>
          <button
            type="button"
            className="icon-button widget-row-remove"
            onClick={() => removeRow(index)}
            aria-label="Remove environment variable"
          >
            <CloseIcon />
          </button>
        </div>
      ))}
      <button type="button" className="btn-secondary btn-small" onClick={addRow}>
        Add environment variable
      </button>
    </div>
  );
}

function projectDraftFromProject(project) {
  return {
    baseImageMode: normalizeBaseMode(project.base_image_mode),
    baseImageValue: String(project.base_image_value || ""),
    setupScript: String(project.setup_script || ""),
    defaultVolumes: mountRowsFromArrays(project.default_ro_mounts || [], project.default_rw_mounts || []),
    defaultEnvVars: envRowsFromArray(project.default_env_vars || [])
  };
}

function normalizeOpenAiProviderStatus(rawProvider) {
  return {
    provider: "openai",
    connected: Boolean(rawProvider?.connected),
    keyHint: String(rawProvider?.key_hint || ""),
    updatedAt: String(rawProvider?.updated_at || ""),
    accountConnected: Boolean(rawProvider?.account_connected),
    accountAuthMode: String(rawProvider?.account_auth_mode || ""),
    accountUpdatedAt: String(rawProvider?.account_updated_at || "")
  };
}

function normalizeGithubProviderStatus(rawProvider) {
  const rawTokens = Array.isArray(rawProvider?.personal_access_tokens)
    ? rawProvider.personal_access_tokens
    : [];
  const personalAccessTokens = rawTokens
    .map((item) => {
      const ownerScopesRaw = Array.isArray(item?.owner_scopes) ? item.owner_scopes : [];
      const ownerScopes = ownerScopesRaw.map((value) => String(value || "").trim()).filter(Boolean);
      return {
        tokenId: String(item?.token_id || ""),
        tokenHint: String(item?.token_hint || ""),
        host: String(item?.host || ""),
        accountLogin: String(item?.account_login || ""),
        accountName: String(item?.account_name || ""),
        accountEmail: String(item?.account_email || ""),
        accountId: String(item?.account_id || ""),
        gitUserName: String(item?.git_user_name || ""),
        gitUserEmail: String(item?.git_user_email || ""),
        tokenScopes: String(item?.token_scopes || ""),
        verifiedAt: String(item?.verified_at || ""),
        connectedAt: String(item?.connected_at || ""),
        ownerScopes
      };
    })
    .filter((item) => item.tokenId && item.host && item.accountLogin);

  return {
    provider: "github",
    connected: Boolean(rawProvider?.connected),
    connectionMode: String(rawProvider?.connection_mode || ""),
    connectionHost: String(rawProvider?.connection_host || ""),
    appConfigured: Boolean(rawProvider?.app_configured),
    appSlug: String(rawProvider?.app_slug || ""),
    installUrl: String(rawProvider?.install_url || ""),
    installationId: Number(rawProvider?.installation_id || 0) || 0,
    installationAccountLogin: String(rawProvider?.installation_account_login || ""),
    installationAccountType: String(rawProvider?.installation_account_type || ""),
    repositorySelection: String(rawProvider?.repository_selection || ""),
    personalAccessTokenHint: String(rawProvider?.personal_access_token_hint || ""),
    personalAccessTokenHost: String(rawProvider?.personal_access_token_host || ""),
    personalAccessTokenUserLogin: String(rawProvider?.personal_access_token_user_login || ""),
    personalAccessTokenUserName: String(rawProvider?.personal_access_token_user_name || ""),
    personalAccessTokenUserEmail: String(rawProvider?.personal_access_token_user_email || ""),
    personalAccessTokenScopes: String(rawProvider?.personal_access_token_scopes || ""),
    personalAccessTokenVerifiedAt: String(rawProvider?.personal_access_token_verified_at || ""),
    personalAccessTokenGitUserName: String(rawProvider?.personal_access_token_git_user_name || ""),
    personalAccessTokenGitUserEmail: String(rawProvider?.personal_access_token_git_user_email || ""),
    personalAccessTokenOwnerScopes: Array.isArray(rawProvider?.personal_access_token_owner_scopes)
      ? rawProvider.personal_access_token_owner_scopes.map((value) => String(value || "").trim()).filter(Boolean)
      : [],
    personalAccessTokenCount: Number(rawProvider?.personal_access_token_count || personalAccessTokens.length) || 0,
    personalAccessTokens,
    updatedAt: String(rawProvider?.updated_at || ""),
    error: String(rawProvider?.error || "")
  };
}

function normalizeGithubInstallation(rawInstallation) {
  return {
    id: Number(rawInstallation?.id || 0) || 0,
    accountLogin: String(rawInstallation?.account_login || ""),
    accountType: String(rawInstallation?.account_type || ""),
    repositorySelection: String(rawInstallation?.repository_selection || ""),
    updatedAt: String(rawInstallation?.updated_at || ""),
    suspendedAt: String(rawInstallation?.suspended_at || "")
  };
}

function normalizeGithubAppSetupSession(rawSession) {
  if (!rawSession || typeof rawSession !== "object") {
    return {
      active: false,
      id: "",
      status: "idle",
      formAction: "",
      manifest: null,
      startedAt: "",
      expiresAt: "",
      completedAt: "",
      error: "",
      appId: "",
      appSlug: "",
      callbackUrl: ""
    };
  }
  return {
    active: Boolean(rawSession.active),
    id: String(rawSession.id || ""),
    status: String(rawSession.status || (rawSession.active ? "awaiting_user" : "idle")),
    formAction: String(rawSession.form_action || ""),
    manifest: rawSession.manifest && typeof rawSession.manifest === "object" ? rawSession.manifest : null,
    startedAt: String(rawSession.started_at || ""),
    expiresAt: String(rawSession.expires_at || ""),
    completedAt: String(rawSession.completed_at || ""),
    error: String(rawSession.error || ""),
    appId: String(rawSession.app_id || ""),
    appSlug: String(rawSession.app_slug || ""),
    callbackUrl: String(rawSession.callback_url || "")
  };
}

function normalizeOpenAiAccountSession(rawSession) {
  if (!rawSession || typeof rawSession !== "object") {
    return null;
  }
  return {
    id: String(rawSession.id || ""),
    method: String(rawSession.method || "browser_callback"),
    status: String(rawSession.status || ""),
    startedAt: String(rawSession.started_at || ""),
    completedAt: String(rawSession.completed_at || ""),
    exitCode: rawSession.exit_code == null ? null : Number(rawSession.exit_code),
    error: String(rawSession.error || ""),
    running: Boolean(rawSession.running),
    loginUrl: String(rawSession.login_url || ""),
    deviceCode: String(rawSession.device_code || ""),
    localCallbackUrl: String(rawSession.local_callback_url || ""),
    callbackPort: Number(rawSession.callback_port || 0) || 0,
    callbackPath: String(rawSession.callback_path || "/auth/callback"),
    logTail: String(rawSession.log_tail || "")
  };
}

function normalizeOpenAiTitleTestResult(rawResult) {
  if (!rawResult || typeof rawResult !== "object") {
    return null;
  }
  const rawConnectivity = rawResult.connectivity && typeof rawResult.connectivity === "object"
    ? rawResult.connectivity
    : {};
  const rawIssues = Array.isArray(rawResult.issues) ? rawResult.issues : [];
  return {
    ok: Boolean(rawResult.ok),
    title: String(rawResult.title || ""),
    model: String(rawResult.model || ""),
    prompt: String(rawResult.prompt || ""),
    error: String(rawResult.error || ""),
    issues: rawIssues.map((item) => String(item || "")).filter(Boolean),
    connectivity: {
      apiKeyConnected: Boolean(rawConnectivity.api_key_connected),
      apiKeyHint: String(rawConnectivity.api_key_hint || ""),
      apiKeyUpdatedAt: String(rawConnectivity.api_key_updated_at || ""),
      accountConnected: Boolean(rawConnectivity.account_connected),
      accountAuthMode: String(rawConnectivity.account_auth_mode || ""),
      accountUpdatedAt: String(rawConnectivity.account_updated_at || ""),
      titleGenerationAuthMode: String(rawConnectivity.title_generation_auth_mode || "none")
    }
  };
}

function resolveTitleGenerationAuthMode(providerStatus) {
  if (providerStatus?.accountConnected) {
    return "chatgpt_account";
  }
  if (providerStatus?.connected) {
    return "api_key";
  }
  return "none";
}

function extractCallbackQuery(value) {
  const raw = String(value || "").trim();
  if (!raw) {
    return "";
  }
  if (raw.startsWith("?")) {
    return raw.slice(1);
  }
  if (raw.startsWith("http://") || raw.startsWith("https://")) {
    try {
      const parsed = new URL(raw);
      return parsed.search ? parsed.search.slice(1) : "";
    } catch {
      return "";
    }
  }
  if (raw.includes("?")) {
    return raw.split("?", 2)[1] || "";
  }
  return raw;
}

function formatTimestamp(isoText) {
  const normalized = String(isoText || "").trim();
  if (!normalized) {
    return "Never";
  }
  const parsed = new Date(normalized);
  if (Number.isNaN(parsed.getTime())) {
    return normalized;
  }
  return parsed.toLocaleString();
}

function OpenAiAuthCallbackPage() {
  const [status, setStatus] = useState("forwarding");
  const [message, setMessage] = useState("Forwarding callback to the OpenAI login container...");

  useEffect(() => {
    let cancelled = false;
    async function forwardCallback() {
      const search = window.location.search || "";
      if (!search || search === "?") {
        if (!cancelled) {
          setStatus("error");
          setMessage("Missing callback query parameters.");
        }
        return;
      }

      try {
        const payload = await fetchJson(`/api/settings/auth/openai/account/callback${search}`);
        if (cancelled) {
          return;
        }
        const forwarded = payload?.callback;
        if (forwarded?.forwarded) {
          setStatus("complete");
          setMessage(
            forwarded?.response_summary
              ? `Callback forwarded. ${forwarded.response_summary}`
              : "Callback forwarded. Return to Agent Hub and wait for login status to become connected."
          );
        } else {
          setStatus("error");
          setMessage("Callback forwarding did not complete.");
        }
      } catch (err) {
        if (!cancelled) {
          setStatus("error");
          setMessage(err.message || String(err));
        }
      }
    }

    forwardCallback();
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <main className="callback-page">
      <section className="panel callback-panel">
        <h2>OpenAI Account Login</h2>
        <p className={`meta callback-status ${status}`}>{message}</p>
        <div className="actions">
          <button type="button" className="btn-primary" onClick={() => window.location.assign("/")}>
            Return to Agent Hub
          </button>
        </div>
      </section>
    </main>
  );
}

function HubApp() {
  const [hubState, setHubState] = useState(() => normalizeHubStatePayload(null));
  const [agentCapabilities, setAgentCapabilities] = useState(() =>
    normalizeAgentCapabilities(DEFAULT_AGENT_CAPABILITIES)
  );
  const [error, setError] = useState("");
  const [createForm, setCreateForm] = useState(() => emptyCreateForm());
  const [createProjectConfigMode, setCreateProjectConfigMode] = useState(() => loadCreateProjectConfigMode());
  const [pendingAutoConfigProjects, setPendingAutoConfigProjects] = useState([]);
  const [projectDrafts, setProjectDrafts] = useState({});
  const [editingProjects, setEditingProjects] = useState({});
  const [projectBuildLogs, setProjectBuildLogs] = useState({});
  const [projectStaticLogs, setProjectStaticLogs] = useState({});
  const [openBuildLogs, setOpenBuildLogs] = useState({});
  const [activeTab, setActiveTab] = useState("projects");
  const [openChats, setOpenChats] = useState({});
  const [openChatDetails, setOpenChatDetails] = useState({});
  const [showArtifactThumbnailsByChat, setShowArtifactThumbnailsByChat] = useState({});
  const [collapsedTerminalsByChat, setCollapsedTerminalsByChat] = useState(() => {
    const loaded = readLocalStorageJson(CHAT_TERMINAL_COLLAPSE_STORAGE_KEY, {});
    if (!loaded || typeof loaded !== "object" || Array.isArray(loaded)) {
      return {};
    }
    return loaded;
  });
  const [collapsedProjectChats, setCollapsedProjectChats] = useState({});
  const [chatStartSettingsByProject, setChatStartSettingsByProject] = useState({});
  const [fullscreenChatId, setFullscreenChatId] = useState("");
  const [artifactPreview, setArtifactPreview] = useState(null);
  const [pendingSessions, setPendingSessions] = useState([]);
  const [pendingProjectBuilds, setPendingProjectBuilds] = useState({});
  const [pendingChatStarts, setPendingChatStarts] = useState({});
  const [pendingContainerRefreshes, setPendingContainerRefreshes] = useState({});
  const [pendingProjectChatCreates, setPendingProjectChatCreates] = useState({});
  const [themePreference, setThemePreference] = useState(() => loadThemePreference());
  const [systemThemeIsDark, setSystemThemeIsDark] = useState(() => detectSystemPrefersDark());
  const [hubStateHydrated, setHubStateHydrated] = useState(false);
  const [defaultAgentSettingSaving, setDefaultAgentSettingSaving] = useState(false);
  const [chatLayoutEngineSettingSaving, setChatLayoutEngineSettingSaving] = useState(false);
  const [chatFlexOuterLayoutJson, setChatFlexOuterLayoutJson] = useState(() => {
    const loaded = readLocalStorageJson(CHAT_FLEX_OUTER_LAYOUT_STORAGE_KEY, null);
    return loaded && typeof loaded === "object" ? loaded : null;
  });
  const [chatFlexProjectLayoutsByProjectId, setChatFlexProjectLayoutsByProjectId] = useState(() => {
    const loaded = readLocalStorageJson(CHAT_FLEX_PROJECT_LAYOUT_STORAGE_KEY, {});
    if (!loaded || typeof loaded !== "object" || Array.isArray(loaded)) {
      return {};
    }
    return loaded;
  });
  const [openAiProviderStatus, setOpenAiProviderStatus] = useState(() =>
    normalizeOpenAiProviderStatus(null)
  );
  const [openAiAuthLoaded, setOpenAiAuthLoaded] = useState(false);
  const [openAiCardExpanded, setOpenAiCardExpanded] = useState(false);
  const [openAiCardExpansionInitialized, setOpenAiCardExpansionInitialized] = useState(false);
  const [openAiDraftKey, setOpenAiDraftKey] = useState("");
  const [verifyOpenAiOnSave, setVerifyOpenAiOnSave] = useState(true);
  const [showOpenAiDraftKey, setShowOpenAiDraftKey] = useState(false);
  const [openAiSaving, setOpenAiSaving] = useState(false);
  const [openAiDisconnecting, setOpenAiDisconnecting] = useState(false);
  const [openAiAccountSession, setOpenAiAccountSession] = useState(null);
  const [openAiAccountStarting, setOpenAiAccountStarting] = useState(false);
  const [openAiAccountCancelling, setOpenAiAccountCancelling] = useState(false);
  const [openAiAccountDisconnecting, setOpenAiAccountDisconnecting] = useState(false);
  const [openAiAccountCallbackInput, setOpenAiAccountCallbackInput] = useState("");
  const [openAiTitleTestPrompt, setOpenAiTitleTestPrompt] = useState("");
  const [openAiTitleTestRunning, setOpenAiTitleTestRunning] = useState(false);
  const [openAiTitleTestResult, setOpenAiTitleTestResult] = useState(null);
  const [githubProviderStatus, setGithubProviderStatus] = useState(() =>
    normalizeGithubProviderStatus(null)
  );
  const [githubCardExpanded, setGithubCardExpanded] = useState(true);
  const [githubInstallations, setGithubInstallations] = useState([]);
  const [githubSelectedInstallationId, setGithubSelectedInstallationId] = useState("");
  const [githubAppSetupSession, setGithubAppSetupSession] = useState(() =>
    normalizeGithubAppSetupSession(null)
  );
  const [githubAppSetupStarting, setGithubAppSetupStarting] = useState(false);
  const [githubInstallationsLoading, setGithubInstallationsLoading] = useState(false);
  const [githubSaving, setGithubSaving] = useState(false);
  const [githubDisconnecting, setGithubDisconnecting] = useState(false);
  const [githubPatRemovingTokenId, setGithubPatRemovingTokenId] = useState("");
  const [githubPersonalAccessTokenDraft, setGithubPersonalAccessTokenDraft] = useState("");
  const [githubPersonalAccessHostDraft, setGithubPersonalAccessHostDraft] = useState("github.com");
  const [githubPersonalAccessOwnerScopesDraft, setGithubPersonalAccessOwnerScopesDraft] = useState("");
  const [showGithubPersonalAccessTokenDraft, setShowGithubPersonalAccessTokenDraft] = useState(false);
  const stateRefreshInFlightRef = useRef(false);
  const stateRefreshQueuedRef = useRef(false);
  const authRefreshInFlightRef = useRef(false);
  const authRefreshQueuedRef = useRef(false);
  const capabilitiesRefreshInFlightRef = useRef(false);
  const capabilitiesRefreshQueuedRef = useRef(false);
  const githubSetupResolutionRef = useRef("");
  const projectFirstSeenOrderRef = useRef(createFirstSeenOrderState());
  const chatFirstSeenOrderRef = useRef(createFirstSeenOrderState());
  const autoConfigProjectOrderAliasByProjectIdRef = useRef(new Map());
  const chatOrderAliasByServerIdRef = useRef(new Map());
  const projectChatCreateLocksRef = useRef(new Set());
  const chatFlexOuterModelCacheRef = useRef({ layoutJson: null, model: null });
  const chatFlexProjectModelCacheRef = useRef({
    layoutsByProjectId: {},
    modelsByProjectId: {}
  });
  const hubSettings = useMemo(
    () => normalizeHubSettings(hubState.settings, agentCapabilities),
    [hubState.settings, agentCapabilities]
  );
  const hubSettingsRef = useRef(hubSettings);
  const agentCapabilitiesRef = useRef(agentCapabilities);
  const chatStartSettingsByProjectRef = useRef(chatStartSettingsByProject);
  hubSettingsRef.current = hubSettings;
  agentCapabilitiesRef.current = agentCapabilities;
  chatStartSettingsByProjectRef.current = chatStartSettingsByProject;

  const applyStatePayload = useCallback((payload) => {
    const normalizedPayload = normalizeHubStatePayload(payload);
    setHubState(normalizedPayload);
    setHubStateHydrated(true);
    const serverChatMap = new Map((normalizedPayload.chats || []).map((chat) => [chat.id, chat]));
    setPendingSessions((prev) => reconcilePendingSessions(prev, serverChatMap));
    setPendingChatStarts((prev) => reconcilePendingChatStarts(prev, serverChatMap));
    setPendingProjectBuilds((prev) => {
      const next = {};
      for (const project of normalizedPayload.projects || []) {
        if (prev[project.id] && String(project.build_status || "") === "building") {
          next[project.id] = true;
        }
      }
      return next;
    });
  }, []);

  const refreshState = useCallback(async () => {
    const payload = await fetchJson("/api/state");
    applyStatePayload(payload);
  }, [applyStatePayload]);

  const refreshAuthSettings = useCallback(async () => {
    const [authPayload, sessionPayload] = await Promise.all([
      fetchJson("/api/settings/auth"),
      fetchJson("/api/settings/auth/openai/account/session")
    ]);
    const openAiProvider = authPayload?.providers?.openai;
    const githubProvider = authPayload?.providers?.github;
    setOpenAiProviderStatus(normalizeOpenAiProviderStatus(openAiProvider));
    const normalizedGithubProvider = normalizeGithubProviderStatus(githubProvider);
    setGithubProviderStatus(normalizedGithubProvider);
    setGithubPersonalAccessHostDraft((prev) => {
      const connectedPatHost = String(normalizedGithubProvider.personalAccessTokenHost || "");
      if (normalizedGithubProvider.connectionMode === "personal_access_token" && connectedPatHost) {
        return connectedPatHost;
      }
      const existing = String(prev || "").trim();
      if (existing) {
        return prev;
      }
      if (connectedPatHost) {
        return connectedPatHost;
      }
      if (normalizedGithubProvider.connectionHost) {
        return normalizedGithubProvider.connectionHost;
      }
      return "github.com";
    });
    setGithubSelectedInstallationId((prev) => {
      const connectedId = Number(normalizedGithubProvider.installationId || 0) || 0;
      if (connectedId > 0) {
        return String(connectedId);
      }
      return prev;
    });
    setOpenAiAccountSession(normalizeOpenAiAccountSession(sessionPayload?.session));
    setOpenAiAuthLoaded(true);
  }, []);

  const applyAgentCapabilitiesPayload = useCallback((payload) => {
    setAgentCapabilities(normalizeAgentCapabilities(payload));
  }, []);

  const refreshAgentCapabilities = useCallback(async () => {
    const payload = await fetchJson("/api/agent-capabilities");
    applyAgentCapabilitiesPayload(payload);
  }, [applyAgentCapabilitiesPayload]);

  const startAgentCapabilitiesDiscovery = useCallback(async () => {
    try {
      const payload = await fetchJson("/api/agent-capabilities/discover", { method: "POST" });
      applyAgentCapabilitiesPayload(payload);
    } catch (err) {
      // Background refresh should not block primary UI.
      console.warn("Agent capability discovery start failed", err);
    }
  }, [applyAgentCapabilitiesPayload]);

  const refreshGithubInstallations = useCallback(async () => {
    setGithubInstallationsLoading(true);
    try {
      const payload = await fetchJson("/api/settings/auth/github/installations");
      const installations = Array.isArray(payload?.installations)
        ? payload.installations
          .map((item) => normalizeGithubInstallation(item))
          .filter((item) => item.id > 0)
        : [];
      setGithubInstallations(installations);
      const connectedInstallationId = Number(payload?.connected_installation_id || 0) || 0;
      setGithubSelectedInstallationId((prev) => {
        if (connectedInstallationId > 0) {
          return String(connectedInstallationId);
        }
        const previousId = Number(prev || 0) || 0;
        if (previousId > 0 && installations.some((item) => item.id === previousId)) {
          return String(previousId);
        }
        return installations.length > 0 ? String(installations[0].id) : "";
      });
    } finally {
      setGithubInstallationsLoading(false);
    }
  }, []);

  const refreshGithubAppSetupSession = useCallback(async () => {
    const payload = await fetchJson("/api/settings/auth/github/app/setup/session");
    const normalized = normalizeGithubAppSetupSession(payload);
    setGithubAppSetupSession(normalized);
    return normalized;
  }, []);

  const submitGithubAppSetupForm = useCallback((session) => {
    const formAction = String(session?.formAction || "").trim();
    const manifest = session?.manifest;
    if (!formAction || !manifest || typeof manifest !== "object") {
      throw new Error("GitHub app setup session did not include a manifest form payload.");
    }

    const popupName = `github-app-setup-${session.id || Date.now()}`;
    const popup = window.open("", popupName, "popup=yes,width=980,height=860");
    const target = popup ? popupName : "_self";

    const form = document.createElement("form");
    form.method = "POST";
    form.action = formAction;
    form.target = target;
    form.style.display = "none";

    const manifestInput = document.createElement("input");
    manifestInput.type = "hidden";
    manifestInput.name = "manifest";
    manifestInput.value = JSON.stringify(manifest);
    form.appendChild(manifestInput);

    document.body.appendChild(form);
    form.submit();
    form.remove();
  }, []);

  const queueStateRefresh = useCallback(() => {
    if (stateRefreshInFlightRef.current) {
      stateRefreshQueuedRef.current = true;
      return;
    }
    stateRefreshInFlightRef.current = true;
    refreshState()
      .then(() => {
        setError("");
      })
      .catch((err) => {
        setError(err.message || String(err));
      })
      .finally(() => {
        stateRefreshInFlightRef.current = false;
        if (stateRefreshQueuedRef.current) {
          stateRefreshQueuedRef.current = false;
          queueStateRefresh();
        }
      });
  }, [refreshState]);

  const queueAuthRefresh = useCallback(() => {
    if (authRefreshInFlightRef.current) {
      authRefreshQueuedRef.current = true;
      return;
    }
    authRefreshInFlightRef.current = true;
    refreshAuthSettings()
      .then(() => {
        setError("");
      })
      .catch((err) => {
        setError(err.message || String(err));
      })
      .finally(() => {
        authRefreshInFlightRef.current = false;
        if (authRefreshQueuedRef.current) {
          authRefreshQueuedRef.current = false;
          queueAuthRefresh();
        }
      });
  }, [refreshAuthSettings]);

  const queueAgentCapabilitiesRefresh = useCallback(() => {
    if (capabilitiesRefreshInFlightRef.current) {
      capabilitiesRefreshQueuedRef.current = true;
      return;
    }
    capabilitiesRefreshInFlightRef.current = true;
    refreshAgentCapabilities()
      .catch((err) => {
        console.warn("Agent capability refresh failed", err);
      })
      .finally(() => {
        capabilitiesRefreshInFlightRef.current = false;
        if (capabilitiesRefreshQueuedRef.current) {
          capabilitiesRefreshQueuedRef.current = false;
          queueAgentCapabilitiesRefresh();
        }
      });
  }, [refreshAgentCapabilities]);

  const visibleChats = useMemo(() => {
    const serverChats = hubState.chats || [];
    const serverChatById = new Map(serverChats.map((chat) => [chat.id, chat]));
    const mappedServerIds = new Set();
    const merged = [];

    for (const session of pendingSessions) {
      const serverId = String(session.server_chat_id || "");
      if (serverId && serverChatById.has(serverId)) {
        mappedServerIds.add(serverId);
        const serverChat = serverChatById.get(serverId);
        merged.push({ ...serverChat, id: session.ui_id, server_chat_id: serverId });
        continue;
      }
      const matchedServerChat = findMatchingServerChatForPendingSession(session, serverChats, mappedServerIds);
      if (matchedServerChat) {
        mappedServerIds.add(matchedServerChat.id);
        const matchedStatus = String(matchedServerChat.status || "").toLowerCase();
        merged.push({
          ...matchedServerChat,
          id: session.ui_id,
          server_chat_id: matchedServerChat.id,
          is_pending_start: matchedStatus === "starting"
        });
        continue;
      }
      merged.push({
        id: session.ui_id,
        server_chat_id: serverId,
        name: "new-chat",
        display_name: "New Chat",
        display_subtitle: "Creating workspace and starting worker…",
        status: "starting",
        is_running: false,
        is_pending_start: true,
        project_id: session.project_id,
        project_name: session.project_name || "Unknown",
        agent_type: normalizeAgentType(session.agent_type, agentCapabilities),
        workspace: "",
        container_workspace: "",
        ro_mounts: [],
        rw_mounts: [],
        env_vars: []
      });
    }

    for (const chat of serverChats) {
      if (!mappedServerIds.has(chat.id)) {
        merged.push(chat);
      }
    }

    return merged;
  }, [hubState.chats, pendingSessions, agentCapabilities]);

  useEffect(() => {
    for (const session of pendingSessions) {
      const serverChatId = String(session?.server_chat_id || "").trim();
      const uiId = String(session?.ui_id || "").trim();
      if (serverChatId && uiId && !chatOrderAliasByServerIdRef.current.has(serverChatId)) {
        chatOrderAliasByServerIdRef.current.set(serverChatId, uiId);
      }
    }
  }, [pendingSessions]);

  const orderedVisibleChats = useMemo(
    () =>
      stableOrderItemsByFirstSeen(
        visibleChats,
        (chat, index) => {
          const serverChatId = String(chat?.server_chat_id || "").trim();
          if (serverChatId) {
            const aliased = chatOrderAliasByServerIdRef.current.get(serverChatId);
            if (aliased) {
              return aliased;
            }
          }
          const chatId = String(chat?.id || "").trim();
          if (chatId) {
            return chatId;
          }
          return serverChatId || `chat-${index}`;
        },
        chatFirstSeenOrderRef.current
      ),
    [visibleChats]
  );

  useEffect(() => {
    const runningStates = new Map();
    for (const chat of orderedVisibleChats) {
      const resolvedChatId = String(chat?.server_chat_id || chat?.id || "").trim();
      if (!resolvedChatId) {
        continue;
      }
      runningStates.set(resolvedChatId, Boolean(chat?.is_running));
    }
    chatTerminalSocketStore.syncRunningStates(runningStates);
  }, [orderedVisibleChats]);

  useEffect(() => () => {
    chatTerminalSocketStore.disposeAll();
  }, []);

  useEffect(() => {
    let cancelled = false;
    startAgentCapabilitiesDiscovery().catch(() => {});
    Promise.all([refreshState(), refreshAuthSettings(), refreshAgentCapabilities()])
      .then(() => {
        if (!cancelled) {
          setError("");
        }
      })
      .catch((err) => {
        if (!cancelled) {
          setError(err.message || String(err));
        }
      });
    return () => {
      cancelled = true;
    };
  }, [refreshState, refreshAuthSettings, refreshAgentCapabilities, startAgentCapabilitiesDiscovery]);

  useEffect(() => {
    if (typeof window === "undefined") {
      return;
    }
    try {
      window.localStorage.setItem(
        CREATE_PROJECT_CONFIG_MODE_STORAGE_KEY,
        normalizeCreateProjectConfigMode(createProjectConfigMode)
      );
    } catch {
      // Local storage persistence failure should not block project creation.
    }
  }, [createProjectConfigMode]);

  useEffect(() => {
    let stopped = false;
    let reconnectTimer = null;
    let ws = null;

    const applyAuthPayload = (authPayload) => {
      const openAiProvider = authPayload?.providers?.openai;
      const githubProvider = authPayload?.providers?.github;
      setOpenAiProviderStatus(normalizeOpenAiProviderStatus(openAiProvider));
      const normalizedGithubProvider = normalizeGithubProviderStatus(githubProvider);
      setGithubProviderStatus(normalizedGithubProvider);
      setGithubPersonalAccessHostDraft((prev) => {
        const connectedPatHost = String(normalizedGithubProvider.personalAccessTokenHost || "");
        if (normalizedGithubProvider.connectionMode === "personal_access_token" && connectedPatHost) {
          return connectedPatHost;
        }
        const existing = String(prev || "").trim();
        if (existing) {
          return prev;
        }
        if (connectedPatHost) {
          return connectedPatHost;
        }
        if (normalizedGithubProvider.connectionHost) {
          return normalizedGithubProvider.connectionHost;
        }
        return "github.com";
      });
      setGithubSelectedInstallationId((prev) => {
        const connectedId = Number(normalizedGithubProvider.installationId || 0) || 0;
        if (connectedId > 0) {
          return String(connectedId);
        }
        return prev;
      });
      setOpenAiAuthLoaded(true);
    };

    const applyOpenAiSessionPayload = (sessionPayload) => {
      setOpenAiAccountSession(normalizeOpenAiAccountSession(sessionPayload?.session));
      if (sessionPayload?.account_connected) {
        setOpenAiProviderStatus((prev) => ({
          ...prev,
          accountConnected: true,
          accountAuthMode: String(sessionPayload?.account_auth_mode || prev.accountAuthMode || "chatgpt"),
          accountUpdatedAt: String(sessionPayload?.account_updated_at || prev.accountUpdatedAt || "")
        }));
      }
    };

    const connect = () => {
      if (stopped) {
        return;
      }
      ws = new WebSocket(hubEventsSocketUrl());
      ws.addEventListener("message", (event) => {
        if (typeof event.data !== "string") {
          return;
        }
        let parsed = null;
        try {
          parsed = JSON.parse(event.data);
        } catch {
          return;
        }
        const eventType = String(parsed?.type || "");
        const payload = parsed?.payload || {};
        if (eventType === "pong") {
          return;
        }
        if (eventType === "snapshot") {
          if (payload.state) {
            applyStatePayload(payload.state);
          }
          if (payload.auth) {
            applyAuthPayload(payload.auth);
          }
          if (payload.agent_capabilities) {
            applyAgentCapabilitiesPayload(payload.agent_capabilities);
          }
          if (payload.openai_account_session) {
            applyOpenAiSessionPayload(payload.openai_account_session);
          }
          if (payload.project_build_logs && typeof payload.project_build_logs === "object") {
            setProjectBuildLogs((prev) => ({ ...prev, ...payload.project_build_logs }));
          }
          return;
        }
        if (eventType === "state_changed") {
          queueStateRefresh();
          return;
        }
        if (eventType === "auth_changed") {
          queueAuthRefresh();
          return;
        }
        if (eventType === "agent_capabilities_changed") {
          queueAgentCapabilitiesRefresh();
          return;
        }
        if (eventType === "openai_account_session") {
          applyOpenAiSessionPayload(payload);
          return;
        }
        if (eventType === "project_build_log") {
          const projectId = String(payload.project_id || "");
          if (!projectId) {
            return;
          }
          const text = String(payload.text || "");
          const replace = Boolean(payload.replace);
          setProjectBuildLogs((prev) => ({
            ...prev,
            [projectId]: replace ? text : `${prev[projectId] || ""}${text}`
          }));
          return;
        }
        if (eventType === "auto_config_log") {
          const requestId = String(payload.request_id || "");
          if (!requestId) {
            return;
          }
          const text = String(payload.text || "");
          const replace = Boolean(payload.replace);
          setPendingAutoConfigProjects((prev) =>
            prev.map((project) =>
              project.id === requestId
                ? {
                  ...project,
                  auto_config_log: replace ? text : `${project.auto_config_log || ""}${text}`
                }
                : project
            )
          );
        }
      });
      ws.addEventListener("close", () => {
        if (stopped) {
          return;
        }
        reconnectTimer = window.setTimeout(connect, 800);
      });
      ws.addEventListener("error", () => {
        if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) {
          ws.close();
        }
      });
    };

    connect();
    return () => {
      stopped = true;
      if (reconnectTimer) {
        window.clearTimeout(reconnectTimer);
      }
      if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) {
        ws.close();
      }
    };
  }, [
    applyAgentCapabilitiesPayload,
    applyStatePayload,
    queueAgentCapabilitiesRefresh,
    queueAuthRefresh,
    queueStateRefresh
  ]);

  useEffect(() => {
    setProjectDrafts((prev) => {
      const next = {};
      for (const project of hubState.projects) {
        next[project.id] = prev[project.id] || projectDraftFromProject(project);
      }
      return next;
    });
  }, [hubState.projects]);

  useEffect(() => {
    setEditingProjects((prev) => {
      const next = {};
      for (const project of hubState.projects) {
        next[project.id] = Boolean(prev[project.id]);
      }
      return next;
    });
  }, [hubState.projects]);

  useEffect(() => {
    setOpenChats((prev) => {
      const next = {};
      for (const chat of orderedVisibleChats) {
        next[chat.id] = prev[chat.id] ?? true;
      }
      return next;
    });
  }, [orderedVisibleChats]);

  useEffect(() => {
    setOpenChatDetails((prev) => {
      const next = {};
      for (const chat of orderedVisibleChats) {
        next[chat.id] = prev[chat.id] ?? false;
      }
      return next;
    });
  }, [orderedVisibleChats]);

  useEffect(() => {
    setCollapsedProjectChats((prev) => {
      const next = {};
      for (const project of hubState.projects) {
        next[project.id] = prev[project.id] ?? false;
      }
      next.__orphan__ = prev.__orphan__ ?? false;
      return next;
    });
  }, [hubState.projects]);

  useEffect(() => {
    setChatStartSettingsByProject((prev) => {
      const next = {};
      for (const project of hubState.projects) {
        const current = prev[project.id] || { agentType: hubSettings.defaultAgentType };
        next[project.id] = normalizeChatStartSettings(current, agentCapabilities);
      }
      return next;
    });
  }, [hubState.projects, agentCapabilities, hubSettings.defaultAgentType]);

  useEffect(() => {
    const activeProjectIds = new Set(
      hubState.projects
        .filter((project) => String(project.build_status || "") === "building")
        .map((project) => project.id)
    );
    setProjectBuildLogs((prev) => {
      if (activeProjectIds.size === 0) {
        return {};
      }
      const next = {};
      for (const projectId of activeProjectIds) {
        next[projectId] = prev[projectId] || "";
      }
      return next;
    });
  }, [hubState.projects]);

  const projectsById = useMemo(() => {
    const map = new Map();
    for (const project of hubState.projects) {
      map.set(project.id, project);
    }
    return map;
  }, [hubState.projects]);

  const projectsForList = useMemo(() => {
    const pendingRows = pendingAutoConfigProjects.map((project) => projectRowFromPendingAutoConfig(project));
    const combinedRows = [...pendingRows, ...(hubState.projects || [])];
    return stableOrderItemsByFirstSeen(
      combinedRows,
      (project, index) => {
        const explicitStableKey = String(project?.stable_order_key || "").trim();
        if (explicitStableKey) {
          return explicitStableKey;
        }
        const projectId = String(project?.id || "").trim();
        if (!projectId) {
          return `project-${index}`;
        }
        return autoConfigProjectOrderAliasByProjectIdRef.current.get(projectId) || projectId;
      },
      projectFirstSeenOrderRef.current
    );
  }, [hubState.projects, pendingAutoConfigProjects]);
  const orderedProjects = useMemo(
    () => projectsForList.filter((project) => !project.is_auto_config_pending),
    [projectsForList]
  );
  const settingsAgentOptions = useMemo(() => {
    const options = agentTypeOptions(agentCapabilities);
    if (options.length > 0) {
      return options;
    }
    return [{ value: DEFAULT_HUB_SETTINGS.defaultAgentType, label: "Codex" }];
  }, [agentCapabilities]);
  const settingsChatLayoutEngineOptions = useMemo(() => chatLayoutEngineOptions(), []);
  const createProjectManualMode = shouldShowManualProjectConfigInputs(createProjectConfigMode);

  function updateCreateForm(patch) {
    setCreateForm((prev) => ({ ...prev, ...patch }));
  }

  function updateProjectDraft(projectId, patch) {
    setProjectDrafts((prev) => ({
      ...prev,
      [projectId]: { ...prev[projectId], ...patch }
    }));
  }

  function updateProjectChatStartSettings(projectId, patch) {
    const capabilities = agentCapabilitiesRef.current;
    const defaultAgentType = hubSettingsRef.current.defaultAgentType;
    setChatStartSettingsByProject((prev) => {
      const current = normalizeChatStartSettings(
        prev[projectId] || { agentType: defaultAgentType },
        capabilities
      );
      const candidate = {
        ...current,
        ...patch
      };
      const normalized = normalizeChatStartSettings(candidate, capabilities);
      return {
        ...prev,
        [projectId]: normalized
      };
    });
  }

  async function persistHubSettingsPatch(serverPatch, optimisticSettingsPatch) {
    const payload = await fetchJson("/api/settings", {
      method: "PATCH",
      body: JSON.stringify(serverPatch)
    });
    setHubState((prev) => ({
      ...prev,
      settings: payload?.settings && typeof payload.settings === "object"
        ? payload.settings
        : { ...(prev.settings || {}), ...optimisticSettingsPatch }
    }));
  }

  async function handleUpdateDefaultAgentSetting(rawAgentType) {
    const previousDefaultAgentType = hubSettings.defaultAgentType;
    const nextDefaultAgentType = normalizeAgentType(rawAgentType, agentCapabilities);
    if (nextDefaultAgentType === previousDefaultAgentType) {
      return;
    }
    setHubState((prev) => ({
      ...prev,
      settings: { ...(prev.settings || {}), default_agent_type: nextDefaultAgentType }
    }));
    setChatStartSettingsByProject((prev) => {
      const next = {};
      for (const project of hubState.projects) {
        const current = normalizeChatStartSettings(
          prev[project.id] || { agentType: previousDefaultAgentType },
          agentCapabilities
        );
        if (current.agentType !== previousDefaultAgentType) {
          next[project.id] = current;
          continue;
        }
        next[project.id] = normalizeChatStartSettings(
          {
            ...current,
            agentType: nextDefaultAgentType,
            model: "default",
            reasoning: "default"
          },
          agentCapabilities
        );
      }
      return next;
    });
    setDefaultAgentSettingSaving(true);
    try {
      await persistHubSettingsPatch(
        { default_agent_type: nextDefaultAgentType },
        { default_agent_type: nextDefaultAgentType }
      );
      setError("");
    } catch (err) {
      setError(err.message || String(err));
      refreshState().catch(() => {});
    } finally {
      setDefaultAgentSettingSaving(false);
    }
  }

  async function handleUpdateChatLayoutEngineSetting(rawLayoutEngine) {
    const nextLayoutEngine = normalizeChatLayoutEngine(rawLayoutEngine);
    if (nextLayoutEngine === hubSettings.chatLayoutEngine) {
      return;
    }
    setHubState((prev) => ({
      ...prev,
      settings: { ...(prev.settings || {}), chat_layout_engine: nextLayoutEngine }
    }));
    setChatLayoutEngineSettingSaving(true);
    try {
      await persistHubSettingsPatch(
        { chat_layout_engine: nextLayoutEngine },
        { chat_layout_engine: nextLayoutEngine }
      );
      setError("");
    } catch (err) {
      setError(err.message || String(err));
      refreshState().catch(() => {});
    } finally {
      setChatLayoutEngineSettingSaving(false);
    }
  }

  function markProjectBuilding(projectId) {
    setHubState((prev) => ({
      ...prev,
      projects: (prev.projects || []).map((project) =>
        project.id === projectId
          ? { ...project, build_status: "building", build_error: "" }
          : project
      )
    }));
    setPendingProjectBuilds((prev) => ({ ...prev, [projectId]: true }));
    setOpenBuildLogs((prev) => ({ ...prev, [projectId]: false }));
    setProjectBuildLogs((prev) => ({
      ...prev,
      [projectId]: prev[projectId] || "Preparing project image...\r\n"
    }));
  }

  const removeOptimisticChatRow = useCallback((uiId) => {
    setPendingSessions((prev) => prev.filter((session) => session.ui_id !== uiId));
    setOpenChats((prev) => {
      const next = { ...prev };
      delete next[uiId];
      return next;
    });
    setOpenChatDetails((prev) => {
      const next = { ...prev };
      delete next[uiId];
      return next;
    });
  }, []);

  async function handleAutoConfigureCreateForm(formSnapshot) {
    const repoUrl = String(formSnapshot.repoUrl || "").trim();
    if (!repoUrl) {
      setError("Repo URL is required before auto configure.");
      return;
    }
    let fallbackMounts;
    let fallbackEnvVars;
    try {
      fallbackMounts = buildMountPayload(formSnapshot.defaultVolumes);
      fallbackEnvVars = buildEnvPayload(formSnapshot.defaultEnvVars);
    } catch (err) {
      setError(err.message || String(err));
      return;
    }

    const pendingProjectId = `pending-auto-${Date.now()}-${Math.random().toString(16).slice(2, 8)}`;
    const pendingProjectName = String(formSnapshot.name || "").trim() || extractProjectNameFromRepoUrl(repoUrl);
    const pendingProjectBranch = formSnapshot.defaultBranch || "auto-detect";
    setPendingAutoConfigProjects((prev) => [
      ...prev,
      {
        id: pendingProjectId,
        stable_order_key: pendingProjectId,
        name: pendingProjectName,
        repo_url: repoUrl,
        default_branch: pendingProjectBranch,
        auto_config_status: "running",
        auto_config_error: "",
        auto_config_log: "Preparing repository checkout for temporary analysis chat...\r\n"
      }
    ]);
    setError("");

    try {
      const payload = await fetchJson("/api/projects/auto-configure", {
        method: "POST",
        body: JSON.stringify({
          repo_url: repoUrl,
          default_branch: formSnapshot.defaultBranch,
          request_id: pendingProjectId
        })
      });
      const recommendation = payload?.recommendation || {};
      const autoConfigProjectPayload = {
        repo_url: repoUrl,
        name: String(formSnapshot.name || "").trim(),
        default_branch: String(recommendation.default_branch || formSnapshot.defaultBranch || "").trim(),
        base_image_mode: normalizeBaseMode(recommendation.base_image_mode || formSnapshot.baseImageMode),
        base_image_value: String(recommendation.base_image_value || formSnapshot.baseImageValue || ""),
        setup_script: String(recommendation.setup_script || formSnapshot.setupScript || ""),
        default_ro_mounts: normalizeStringArray(
          recommendation.default_ro_mounts,
          fallbackMounts.roMounts
        ),
        default_rw_mounts: normalizeStringArray(
          recommendation.default_rw_mounts,
          fallbackMounts.rwMounts
        ),
        default_env_vars: normalizeStringArray(
          recommendation.default_env_vars,
          fallbackEnvVars
        )
      };
      const createProjectResponse = await fetchJson("/api/projects", {
        method: "POST",
        body: JSON.stringify(autoConfigProjectPayload)
      });
      const createdProjectId = String(createProjectResponse?.project?.id || "").trim();
      if (createdProjectId) {
        autoConfigProjectOrderAliasByProjectIdRef.current.set(createdProjectId, pendingProjectId);
      }
      setPendingAutoConfigProjects((prev) => removePendingAutoConfigProject(prev, pendingProjectId));
      setCreateForm(emptyCreateForm());
      setError("");
      refreshState().catch(() => {});
    } catch (err) {
      const message = err.message || String(err);
      setPendingAutoConfigProjects((prev) => markPendingAutoConfigProjectFailed(prev, pendingProjectId, message));
      refreshState().catch(() => {});
    }
  }

  function handleDeletePendingAutoConfigProject(projectId) {
    setPendingAutoConfigProjects((prev) => removePendingAutoConfigProject(prev, projectId));
  }

  async function handleCreateProject(event) {
    event.preventDefault();
    const repoUrl = String(createForm.repoUrl || "").trim();
    if (!repoUrl) {
      setError("Repo URL is required.");
      return;
    }
    const formSnapshot = {
      repoUrl,
      name: String(createForm.name || ""),
      defaultBranch: String(createForm.defaultBranch || ""),
      baseImageMode: normalizeBaseMode(createForm.baseImageMode),
      baseImageValue: String(createForm.baseImageValue || ""),
      setupScript: String(createForm.setupScript || ""),
      defaultVolumes: (createForm.defaultVolumes || []).map((entry) => ({ ...entry })),
      defaultEnvVars: (createForm.defaultEnvVars || []).map((entry) => ({ ...entry }))
    };
    setCreateForm((prev) => ({ ...prev, repoUrl: "" }));
    if (isAutoCreateProjectConfigMode(createProjectConfigMode)) {
      await handleAutoConfigureCreateForm(formSnapshot);
      return;
    }
    try {
      const mounts = buildMountPayload(formSnapshot.defaultVolumes);
      const envVars = buildEnvPayload(formSnapshot.defaultEnvVars);
      const payload = {
        repo_url: formSnapshot.repoUrl,
        name: formSnapshot.name,
        default_branch: formSnapshot.defaultBranch,
        base_image_mode: formSnapshot.baseImageMode,
        base_image_value: formSnapshot.baseImageValue,
        setup_script: formSnapshot.setupScript,
        default_ro_mounts: mounts.roMounts,
        default_rw_mounts: mounts.rwMounts,
        default_env_vars: envVars
      };
      await fetchJson("/api/projects", {
        method: "POST",
        body: JSON.stringify(payload)
      });
      setCreateForm(emptyCreateForm());
      setError("");
      await refreshState();
    } catch (err) {
      setError(err.message || String(err));
    }
  }

  async function persistProjectSettings(projectId) {
    const draft = projectDrafts[projectId];
    if (!draft) {
      throw new Error("Project draft is missing.");
    }
    const mounts = buildMountPayload(draft.defaultVolumes);
    const envVars = buildEnvPayload(draft.defaultEnvVars);
    const payload = {
      base_image_mode: normalizeBaseMode(draft.baseImageMode),
      base_image_value: draft.baseImageValue,
      setup_script: draft.setupScript,
      default_ro_mounts: mounts.roMounts,
      default_rw_mounts: mounts.rwMounts,
      default_env_vars: envVars
    };
    await fetchJson(`/api/projects/${projectId}`, {
      method: "PATCH",
      body: JSON.stringify(payload)
    });
  }

  function handleEditProject(project) {
    setProjectDrafts((prev) => ({
      ...prev,
      [project.id]: projectDraftFromProject(project)
    }));
    setEditingProjects((prev) => ({ ...prev, [project.id]: true }));
  }

  function handleCancelProjectEdit(project) {
    setProjectDrafts((prev) => ({
      ...prev,
      [project.id]: projectDraftFromProject(project)
    }));
    setEditingProjects((prev) => ({ ...prev, [project.id]: false }));
  }

  async function handleCreateChat(projectId, startSettings = null) {
    const normalizedProjectId = String(projectId || "").trim();
    if (!normalizedProjectId) {
      setError("Project id is required to create a chat.");
      return;
    }
    if (projectChatCreateLocksRef.current.has(normalizedProjectId)) {
      return;
    }
    projectChatCreateLocksRef.current.add(normalizedProjectId);
    setPendingProjectChatCreates((prev) => ({ ...prev, [normalizedProjectId]: Date.now() }));
    let uiId = "";
    try {
      const pendingCreatedAtMs = Date.now();
      uiId = `pending-${Date.now()}-${Math.random().toString(16).slice(2, 8)}`;
      const requestId = `chat-create-${uiId}`;
      const project = projectsById.get(normalizedProjectId);
      const selectedStartSettings = resolveProjectChatStartSettings(
        normalizedProjectId,
        chatStartSettingsByProjectRef.current,
        hubSettingsRef.current.defaultAgentType,
        agentCapabilitiesRef.current,
        startSettings
      );
      const { agentType, agentArgs } = buildChatStartConfig(selectedStartSettings, agentCapabilitiesRef.current);
      const knownServerChatIds = (hubState.chats || [])
        .filter((chat) => String(chat.project_id || "") === normalizedProjectId)
        .map((chat) => chat.id);
      setPendingSessions((prev) => [...prev, {
        ui_id: uiId,
        project_id: normalizedProjectId,
        project_name: project?.name || "Unknown",
        agent_type: agentType,
        server_chat_id: "",
        created_at_ms: pendingCreatedAtMs,
        server_chat_id_set_at_ms: 0,
        known_server_chat_ids: knownServerChatIds,
        seen_on_server: false
      }]);
      setActiveTab("chats");
      setOpenChats((prev) => ({ ...prev, [uiId]: true }));
      setOpenChatDetails((prev) => ({ ...prev, [uiId]: false }));
      setCollapsedProjectChats((prev) => ({ ...prev, [normalizedProjectId]: false }));

      const response = await fetchJson(`/api/projects/${normalizedProjectId}/chats/start`, {
        method: "POST",
        body: JSON.stringify({ agent_type: agentType, agent_args: agentArgs, request_id: requestId })
      });
      const chatId = response?.chat?.id;
      if (!chatId) {
        removeOptimisticChatRow(uiId);
        setError("Chat start request succeeded but did not include a chat id.");
        refreshState().catch(() => {});
        return;
      }
      chatOrderAliasByServerIdRef.current.set(chatId, uiId);
      setPendingSessions((prev) =>
        prev.map((session) =>
          session.ui_id === uiId
            ? {
              ...session,
              server_chat_id: chatId,
              server_chat_id_set_at_ms: Date.now()
            }
            : session
        )
      );
      setPendingChatStarts((prev) => ({ ...prev, [chatId]: Date.now() }));
      setError("");
      refreshState().catch(() => {});
    } catch (err) {
      if (uiId) {
        removeOptimisticChatRow(uiId);
      }
      setError(err.message || String(err));
      refreshState().catch(() => {});
    } finally {
      projectChatCreateLocksRef.current.delete(normalizedProjectId);
      setPendingProjectChatCreates((prev) => {
        if (!prev[normalizedProjectId]) {
          return prev;
        }
        const next = { ...prev };
        delete next[normalizedProjectId];
        return next;
      });
    }
  }

  async function handleDeleteProject(projectId) {
    const project = projectsById.get(projectId);
    const label = project ? project.name : projectId;
    if (!window.confirm(`Delete project '${label}' and all chats?`)) {
      return;
    }
    setHubState((prev) => ({
      ...prev,
      projects: (prev.projects || []).filter((projectItem) => projectItem.id !== projectId),
      chats: (prev.chats || []).filter((chat) => chat.project_id !== projectId)
    }));
    setPendingSessions((prev) => prev.filter((session) => session.project_id !== projectId));
    try {
      await fetchJson(`/api/projects/${projectId}`, { method: "DELETE" });
      setError("");
      refreshState().catch(() => {});
    } catch (err) {
      setError(err.message || String(err));
      refreshState().catch(() => {});
    }
  }

  async function handleStartChat(chatId) {
    setPendingChatStarts((prev) => ({ ...prev, [chatId]: Date.now() }));
    setOpenChats((prev) => ({ ...prev, [chatId]: true }));
    try {
      await fetchJson(`/api/chats/${chatId}/start`, { method: "POST" });
      setError("");
      refreshState().catch(() => {});
    } catch (err) {
      setPendingChatStarts((prev) => {
        const next = { ...prev };
        delete next[chatId];
        return next;
      });
      setError(err.message || String(err));
      refreshState().catch(() => {});
    }
  }

  async function handleRefreshChatContainer(chatId) {
    setPendingContainerRefreshes((prev) => ({ ...prev, [chatId]: Date.now() }));
    try {
      await fetchJson(`/api/chats/${chatId}/refresh-container`, { method: "POST" });
      setError("");
      refreshState().catch(() => {});
    } catch (err) {
      setError(err.message || String(err));
      refreshState().catch(() => {});
    } finally {
      setPendingContainerRefreshes((prev) => {
        const next = { ...prev };
        delete next[chatId];
        return next;
      });
    }
  }

  async function handleDeleteChat(chatId, uiId = chatId) {
    setHubState((prev) => ({
      ...prev,
      chats: (prev.chats || []).filter((chat) => chat.id !== chatId)
    }));
    setPendingSessions((prev) =>
      prev.filter((session) => session.ui_id !== uiId && session.server_chat_id !== chatId)
    );
    setPendingChatStarts((prev) => {
      const next = { ...prev };
      delete next[chatId];
      return next;
    });
    setPendingContainerRefreshes((prev) => {
      const next = { ...prev };
      delete next[chatId];
      return next;
    });
    setOpenChats((prev) => {
      const next = { ...prev };
      delete next[uiId];
      delete next[chatId];
      return next;
    });
    setOpenChatDetails((prev) => {
      const next = { ...prev };
      delete next[uiId];
      delete next[chatId];
      return next;
    });
    setFullscreenChatId((current) => (current === uiId || current === chatId ? "" : current));
    try {
      await fetchJson(`/api/chats/${chatId}`, { method: "DELETE" });
      setError("");
      refreshState().catch(() => {});
    } catch (err) {
      setError(err.message || String(err));
      refreshState().catch(() => {});
    }
  }

  async function handleBuildProject(projectId) {
    markProjectBuilding(projectId);
    try {
      await persistProjectSettings(projectId);
      setEditingProjects((prev) => ({ ...prev, [projectId]: false }));
      setPendingProjectBuilds((prev) => {
        const next = { ...prev };
        delete next[projectId];
        return next;
      });
      setError("");
      refreshState().catch(() => {});
    } catch (err) {
      setPendingProjectBuilds((prev) => {
        const next = { ...prev };
        delete next[projectId];
        return next;
      });
      setError(err.message || String(err));
      refreshState().catch(() => {});
    }
  }

  async function handleToggleStoredBuildLog(projectId) {
    const currentlyOpen = Boolean(openBuildLogs[projectId]);
    if (currentlyOpen) {
      setOpenBuildLogs((prev) => ({ ...prev, [projectId]: false }));
      return;
    }
    try {
      if (projectStaticLogs[projectId] === undefined) {
        const text = await fetchText(`/api/projects/${projectId}/build-logs`);
        setProjectStaticLogs((prev) => ({ ...prev, [projectId]: text }));
      }
      setOpenBuildLogs((prev) => ({ ...prev, [projectId]: true }));
      setError("");
    } catch (err) {
      setError(err.message || String(err));
    }
  }

  async function handleConnectOpenAi(event) {
    event.preventDefault();
    const apiKey = openAiDraftKey.trim();
    if (!apiKey) {
      setError("OpenAI API key is required.");
      return;
    }

    setOpenAiSaving(true);
    try {
      const payload = await fetchJson("/api/settings/auth/openai/connect", {
        method: "POST",
        body: JSON.stringify({ api_key: apiKey, verify: verifyOpenAiOnSave })
      });
      setOpenAiProviderStatus(normalizeOpenAiProviderStatus(payload?.provider));
      setOpenAiDraftKey("");
      setShowOpenAiDraftKey(false);
      setError("");
    } catch (err) {
      setError(err.message || String(err));
    } finally {
      setOpenAiSaving(false);
    }
  }

  async function handleDisconnectOpenAi() {
    setOpenAiDisconnecting(true);
    try {
      const payload = await fetchJson("/api/settings/auth/openai/disconnect", {
        method: "POST"
      });
      setOpenAiProviderStatus(normalizeOpenAiProviderStatus(payload?.provider));
      setOpenAiDraftKey("");
      setShowOpenAiDraftKey(false);
      setError("");
    } catch (err) {
      setError(err.message || String(err));
    } finally {
      setOpenAiDisconnecting(false);
    }
  }

  async function handleConnectGithubApp(event) {
    event.preventDefault();
    const installationId = Number(githubSelectedInstallationId || 0) || 0;
    if (installationId <= 0) {
      setError("Choose a GitHub App installation first.");
      return;
    }

    setGithubSaving(true);
    try {
      const payload = await fetchJson("/api/settings/auth/github/connect", {
        method: "POST",
        body: JSON.stringify({
          installation_id: installationId
        })
      });
      setGithubProviderStatus(normalizeGithubProviderStatus(payload?.provider));
      setGithubSelectedInstallationId(String(installationId));
      refreshGithubInstallations().catch(() => {});
      setError("");
    } catch (err) {
      setError(err.message || String(err));
    } finally {
      setGithubSaving(false);
    }
  }

  async function handleConnectGithubPersonalAccessToken(event) {
    event.preventDefault();
    const personalAccessToken = githubPersonalAccessTokenDraft.trim();
    if (!personalAccessToken) {
      setError("GitHub personal access token is required.");
      return;
    }
    const host = String(githubPersonalAccessHostDraft || "").trim() || "github.com";
    const ownerScopes = String(githubPersonalAccessOwnerScopesDraft || "")
      .split(/[\s,]+/)
      .map((value) => value.trim())
      .filter(Boolean);

    setGithubSaving(true);
    try {
      const payload = await fetchJson("/api/settings/auth/github/connect", {
        method: "POST",
        body: JSON.stringify({
          connection_mode: "personal_access_token",
          personal_access_token: personalAccessToken,
          host,
          owner_scopes: ownerScopes
        })
      });
      setGithubProviderStatus(normalizeGithubProviderStatus(payload?.provider));
      setGithubPersonalAccessTokenDraft("");
      setShowGithubPersonalAccessTokenDraft(false);
      setGithubPersonalAccessOwnerScopesDraft("");
      setGithubPersonalAccessHostDraft(host);
      refreshGithubInstallations().catch(() => {});
      setError("");
    } catch (err) {
      setError(err.message || String(err));
    } finally {
      setGithubSaving(false);
    }
  }

  async function handleDisconnectGithubPersonalAccessToken(tokenId) {
    const normalizedTokenId = String(tokenId || "").trim();
    if (!normalizedTokenId) {
      return;
    }
    setGithubPatRemovingTokenId(normalizedTokenId);
    try {
      const payload = await fetchJson(`/api/settings/auth/github/personal-access-tokens/${encodeURIComponent(normalizedTokenId)}`, {
        method: "DELETE"
      });
      setGithubProviderStatus(normalizeGithubProviderStatus(payload?.provider));
      setError("");
    } catch (err) {
      setError(err.message || String(err));
    } finally {
      setGithubPatRemovingTokenId("");
    }
  }

  async function handleStartGithubAppSetup() {
    setGithubAppSetupStarting(true);
    try {
      const payload = await fetchJson("/api/settings/auth/github/app/setup/start", {
        method: "POST",
        body: JSON.stringify({ origin: window.location.origin })
      });
      const session = normalizeGithubAppSetupSession(payload);
      setGithubAppSetupSession(session);
      submitGithubAppSetupForm(session);
      setGithubCardExpanded(true);
      setError("");
    } catch (err) {
      setError(err.message || String(err));
    } finally {
      setGithubAppSetupStarting(false);
    }
  }

  async function handleDisconnectGithubApp() {
    setGithubDisconnecting(true);
    try {
      const payload = await fetchJson("/api/settings/auth/github/disconnect", {
        method: "POST"
      });
      setGithubProviderStatus(normalizeGithubProviderStatus(payload?.provider));
      setGithubPersonalAccessTokenDraft("");
      setShowGithubPersonalAccessTokenDraft(false);
      setGithubPersonalAccessOwnerScopesDraft("");
      refreshGithubInstallations().catch(() => {});
      setGithubCardExpanded(true);
      setError("");
    } catch (err) {
      setError(err.message || String(err));
    } finally {
      setGithubDisconnecting(false);
    }
  }

  async function handleStartOpenAiAccountLogin(method) {
    setOpenAiAccountStarting(true);
    try {
      const payload = await fetchJson("/api/settings/auth/openai/account/start", {
        method: "POST",
        body: JSON.stringify({ method })
      });
      setOpenAiAccountSession(normalizeOpenAiAccountSession(payload?.session));
      setError("");
      refreshAuthSettings().catch(() => {});
    } catch (err) {
      setError(err.message || String(err));
    } finally {
      setOpenAiAccountStarting(false);
    }
  }

  async function handleCancelOpenAiAccountLogin() {
    setOpenAiAccountCancelling(true);
    try {
      const payload = await fetchJson("/api/settings/auth/openai/account/cancel", {
        method: "POST"
      });
      setOpenAiAccountSession(normalizeOpenAiAccountSession(payload?.session));
      setError("");
      refreshAuthSettings().catch(() => {});
    } catch (err) {
      setError(err.message || String(err));
    } finally {
      setOpenAiAccountCancelling(false);
    }
  }

  async function handleDisconnectOpenAiAccount() {
    setOpenAiAccountDisconnecting(true);
    try {
      const payload = await fetchJson("/api/settings/auth/openai/account/disconnect", {
        method: "POST"
      });
      setOpenAiProviderStatus(normalizeOpenAiProviderStatus(payload?.provider));
      setError("");
      refreshAuthSettings().catch(() => {});
    } catch (err) {
      setError(err.message || String(err));
    } finally {
      setOpenAiAccountDisconnecting(false);
    }
  }

  async function handleForwardOpenAiAccountCallback(event) {
    event.preventDefault();
    const query = extractCallbackQuery(openAiAccountCallbackInput);
    if (!query) {
      setError("Paste the full callback URL (or query string) from the localhost error page.");
      return;
    }
    try {
      const payload = await fetchJson(`/api/settings/auth/openai/account/callback?${query}`);
      setOpenAiAccountSession(normalizeOpenAiAccountSession(payload?.session));
      setOpenAiAccountCallbackInput("");
      setError("");
      refreshAuthSettings().catch(() => {});
    } catch (err) {
      setError(err.message || String(err));
    }
  }

  async function handleTestOpenAiTitleGeneration(event) {
    event.preventDefault();
    const prompt = String(openAiTitleTestPrompt || "").trim();
    const titleAuthMode = resolveTitleGenerationAuthMode(openAiProviderStatus);
    if (!prompt) {
      setOpenAiTitleTestResult({
        ok: false,
        title: "",
        model: "",
        prompt: "",
        error: "Enter a prompt before running the title-generation test.",
        issues: ["Enter a prompt before running the title-generation test."],
        connectivity: {
          apiKeyConnected: openAiProviderStatus.connected,
          apiKeyHint: openAiProviderStatus.keyHint,
          apiKeyUpdatedAt: openAiProviderStatus.updatedAt,
          accountConnected: openAiProviderStatus.accountConnected,
          accountAuthMode: openAiProviderStatus.accountAuthMode,
          accountUpdatedAt: openAiProviderStatus.accountUpdatedAt,
          titleGenerationAuthMode: titleAuthMode
        }
      });
      return;
    }

    setOpenAiTitleTestRunning(true);
    try {
      const payload = await fetchJson("/api/settings/auth/openai/title-test", {
        method: "POST",
        body: JSON.stringify({ prompt })
      });
      setOpenAiTitleTestResult(normalizeOpenAiTitleTestResult(payload));
    } catch (err) {
      const message = err.message || String(err);
      setOpenAiTitleTestResult({
        ok: false,
        title: "",
        model: "",
        prompt,
        error: message,
        issues: [message],
        connectivity: {
          apiKeyConnected: openAiProviderStatus.connected,
          apiKeyHint: openAiProviderStatus.keyHint,
          apiKeyUpdatedAt: openAiProviderStatus.updatedAt,
          accountConnected: openAiProviderStatus.accountConnected,
          accountAuthMode: openAiProviderStatus.accountAuthMode,
          accountUpdatedAt: openAiProviderStatus.accountUpdatedAt,
          titleGenerationAuthMode: titleAuthMode
        }
      });
    } finally {
      setOpenAiTitleTestRunning(false);
    }
  }

  const chatsByProject = useMemo(() => {
    const byProject = new Map();
    for (const project of orderedProjects) {
      byProject.set(project.id, []);
    }
    const orphanChats = [];
    for (const chat of orderedVisibleChats) {
      if (!byProject.has(chat.project_id)) {
        orphanChats.push(chat);
        continue;
      }
      byProject.get(chat.project_id).push(chat);
    }
    return { byProject, orphanChats };
  }, [orderedProjects, orderedVisibleChats]);
  const chatFlexOuterLayoutReconciledJson = useMemo(
    () => reconcileOuterFlexLayoutJson(chatFlexOuterLayoutJson, orderedProjects, chatsByProject.orphanChats.length > 0),
    [chatFlexOuterLayoutJson, orderedProjects, chatsByProject.orphanChats.length]
  );
  const chatFlexProjectLayoutsReconciledByProjectId = useMemo(() => {
    const nextLayoutsByProjectId = {};
    for (const project of orderedProjects) {
      const projectId = String(project.id || "");
      if (!projectId) {
        continue;
      }
      const projectChats = chatsByProject.byProject.get(projectId) || [];
      const reconciled = reconcileProjectChatsFlexLayoutJson(
        chatFlexProjectLayoutsByProjectId[projectId],
        projectChats,
        projectId
      );
      if (reconciled) {
        nextLayoutsByProjectId[projectId] = reconciled;
      }
    }
    return nextLayoutsByProjectId;
  }, [chatFlexProjectLayoutsByProjectId, orderedProjects, chatsByProject]);
  const chatFlexOuterModel = useMemo(() => {
    if (
      !chatFlexOuterLayoutReconciledJson ||
      typeof chatFlexOuterLayoutReconciledJson !== "object" ||
      !chatFlexOuterLayoutReconciledJson.layout ||
      typeof chatFlexOuterLayoutReconciledJson.layout !== "object"
    ) {
      chatFlexOuterModelCacheRef.current = { layoutJson: null, model: null };
      return null;
    }
    const cachedOuterModel = chatFlexOuterModelCacheRef.current;
    if (
      cachedOuterModel.model &&
      layoutJsonEquals(cachedOuterModel.layoutJson || null, chatFlexOuterLayoutReconciledJson || null)
    ) {
      return cachedOuterModel.model;
    }
    try {
      const parsedModel = Model.fromJson(chatFlexOuterLayoutReconciledJson);
      chatFlexOuterModelCacheRef.current = {
        layoutJson: chatFlexOuterLayoutReconciledJson,
        model: parsedModel
      };
      return parsedModel;
    } catch (err) {
      console.error("Failed to parse outer flex layout model.", err);
      chatFlexOuterModelCacheRef.current = { layoutJson: null, model: null };
      return null;
    }
  }, [chatFlexOuterLayoutReconciledJson]);
  const chatFlexProjectModelsByProjectId = useMemo(() => {
    const cachedProjectModels = chatFlexProjectModelCacheRef.current;
    const nextProjectModels = buildProjectChatFlexModels(
      chatFlexProjectLayoutsReconciledByProjectId,
      (projectLayoutJson) => Model.fromJson(projectLayoutJson),
      (projectId, err) => {
        console.error(`Failed to parse project chat flex layout model for ${projectId}.`, err);
      },
      {
        previousLayoutsByProjectId: cachedProjectModels.layoutsByProjectId,
        previousModelsByProjectId: cachedProjectModels.modelsByProjectId,
        areLayoutsEqual: layoutJsonEquals
      }
    );
    chatFlexProjectModelCacheRef.current = {
      layoutsByProjectId: chatFlexProjectLayoutsReconciledByProjectId,
      modelsByProjectId: nextProjectModels
    };
    return nextProjectModels;
  }, [chatFlexProjectLayoutsReconciledByProjectId]);
  const effectiveTheme = useMemo(
    () => resolveEffectiveTheme(themePreference, systemThemeIsDark),
    [themePreference, systemThemeIsDark]
  );
  const chatFlexLayoutThemeClass = useMemo(
    () => `flexlayout__theme_${effectiveTheme}`,
    [effectiveTheme]
  );

  const openAiAccountLoginUrl = String(openAiAccountSession?.loginUrl || "").trim();
  const openAiAccountSessionMethod = String(openAiAccountSession?.method || "");
  const openAiAccountLoginInFlight = Boolean(
    openAiAccountSession &&
      ["starting", "running", "waiting_for_browser", "waiting_for_device_code", "callback_received"].includes(
        String(openAiAccountSession.status || "")
      )
  );
  const openAiBrowserCallbackInFlight = openAiAccountLoginInFlight && openAiAccountSessionMethod === "browser_callback";
  const openAiDeviceAuthInFlight = openAiAccountLoginInFlight && openAiAccountSessionMethod === "device_auth";
  const openAiAccountConnected = Boolean(openAiProviderStatus.accountConnected);
  const openAiCardCanExpand = !openAiAccountConnected;
  const openAiOverallConnected = openAiProviderStatus.accountConnected || openAiProviderStatus.connected;
  const openAiConnectionSummary = openAiProviderStatus.accountConnected && openAiProviderStatus.connected
    ? "Connected with OpenAI account and API key."
    : openAiProviderStatus.accountConnected
      ? "Connected with OpenAI account."
      : openAiProviderStatus.connected
        ? "Connected with API key."
        : "Not connected yet. Expand this section and choose one login method.";
  const githubAppConfigured = githubProviderStatus.appConfigured;
  const githubConnected = githubProviderStatus.connected;
  const githubConnectionMode = String(githubProviderStatus.connectionMode || "");
  const githubConnectedWithPat = githubConnected && githubConnectionMode === "personal_access_token";
  const githubConnectedWithApp = githubConnected && githubConnectionMode === "github_app";
  const githubPersonalAccessTokens = Array.isArray(githubProviderStatus.personalAccessTokens)
    ? githubProviderStatus.personalAccessTokens
    : [];
  const githubPrimaryPat = githubPersonalAccessTokens.length > 0 ? githubPersonalAccessTokens[0] : null;
  const githubAppSetupStatus = githubAppSetupSession.status;
  const githubAppSetupInFlight = githubAppSetupSession.active &&
    ["awaiting_user", "converting"].includes(githubAppSetupStatus);
  const githubAppSetupDone = githubAppSetupStatus === "completed";
  const githubAppSetupError = githubAppSetupSession.error;
  const githubAppSetupStatusLabel = githubAppSetupStatus === "awaiting_user"
    ? "waiting for GitHub approval"
    : githubAppSetupStatus === "converting"
      ? "finishing setup"
      : githubAppSetupStatus === "completed"
        ? "completed"
        : githubAppSetupStatus === "expired"
          ? "expired"
          : githubAppSetupStatus === "failed"
            ? "failed"
            : "idle";
  const githubConnectionSummary = githubConnectedWithPat
    ? githubPersonalAccessTokens.length > 1
      ? `Connected with ${githubPersonalAccessTokens.length} personal access tokens${githubPrimaryPat?.host ? ` on ${githubPrimaryPat.host}` : ""}.`
      : `Connected as ${githubProviderStatus.personalAccessTokenUserLogin || "unknown user"} using a personal access token${githubProviderStatus.personalAccessTokenHost ? ` on ${githubProviderStatus.personalAccessTokenHost}` : ""}.`
    : githubConnectedWithApp
      ? githubProviderStatus.installationAccountLogin
        ? `Connected to installation #${githubProviderStatus.installationId} (${githubProviderStatus.installationAccountLogin}).`
        : `Connected to installation #${githubProviderStatus.installationId}.`
      : !githubAppConfigured
        ? githubAppSetupInFlight
          ? "GitHub App setup in progress. Complete the GitHub authorization window to continue."
          : githubAppSetupDone
            ? "GitHub App setup completed. Select an installation below to connect."
            : githubProviderStatus.error || "GitHub App is not configured on this server."
        : "Not connected yet. Connect a personal access token (acts as you) or a GitHub App installation (acts as the app).";
  const selectedGithubInstallation = useMemo(() => {
    const selectedId = Number(githubSelectedInstallationId || 0) || 0;
    if (selectedId <= 0) {
      return null;
    }
    return githubInstallations.find((item) => item.id === selectedId) || null;
  }, [githubInstallations, githubSelectedInstallationId]);

  useEffect(() => {
    if (!openAiAuthLoaded || openAiCardExpansionInitialized) {
      return;
    }
    setOpenAiCardExpanded(!openAiOverallConnected);
    setOpenAiCardExpansionInitialized(true);
  }, [openAiAuthLoaded, openAiCardExpansionInitialized, openAiOverallConnected]);

  useEffect(() => {
    if (openAiCardCanExpand && openAiAccountLoginInFlight) {
      setOpenAiCardExpanded(true);
    }
  }, [openAiCardCanExpand, openAiAccountLoginInFlight]);

  useEffect(() => {
    if (openAiAccountConnected && openAiCardExpanded) {
      setOpenAiCardExpanded(false);
    }
  }, [openAiAccountConnected, openAiCardExpanded]);

  useEffect(() => {
    if (!openAiAuthLoaded || !githubCardExpanded) {
      return;
    }
    Promise.all([
      refreshGithubInstallations(),
      refreshGithubAppSetupSession()
    ]).catch((err) => {
      setError(err.message || String(err));
    });
  }, [openAiAuthLoaded, githubCardExpanded, refreshGithubInstallations, refreshGithubAppSetupSession]);

  useEffect(() => {
    if (!githubAppSetupInFlight) {
      return undefined;
    }
    let cancelled = false;

    const poll = async () => {
      try {
        const session = await refreshGithubAppSetupSession();
        if (cancelled) {
          return;
        }
        if (!session.active) {
          return;
        }
      } catch (err) {
        if (!cancelled) {
          setError(err.message || String(err));
        }
      }
    };

    poll();
    const timer = window.setInterval(poll, 2000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [githubAppSetupInFlight, refreshGithubAppSetupSession]);

  useEffect(() => {
    const resolutionKey = `${githubAppSetupSession.id}:${githubAppSetupStatus}:${githubAppSetupSession.completedAt}`;
    if (!githubAppSetupSession.id || githubSetupResolutionRef.current === resolutionKey) {
      return;
    }
    if (!["completed", "failed", "expired"].includes(githubAppSetupStatus)) {
      return;
    }
    githubSetupResolutionRef.current = resolutionKey;

    if (githubAppSetupStatus === "completed") {
      refreshAuthSettings().catch(() => {});
      refreshGithubInstallations().catch(() => {});
      setError("");
      return;
    }

    if (githubAppSetupError) {
      setError(githubAppSetupError);
    }
  }, [
    githubAppSetupSession.id,
    githubAppSetupSession.completedAt,
    githubAppSetupError,
    githubAppSetupStatus,
    refreshAuthSettings,
    refreshGithubInstallations
  ]);

  useEffect(() => {
    if (typeof window === "undefined" || typeof window.matchMedia !== "function") {
      return undefined;
    }
    const mediaQuery = window.matchMedia("(prefers-color-scheme: dark)");
    const updateFromMediaQuery = (eventOrList) => {
      setSystemThemeIsDark(Boolean(eventOrList?.matches));
    };
    updateFromMediaQuery(mediaQuery);
    if (typeof mediaQuery.addEventListener === "function") {
      mediaQuery.addEventListener("change", updateFromMediaQuery);
      return () => {
        mediaQuery.removeEventListener("change", updateFromMediaQuery);
      };
    }
    if (typeof mediaQuery.addListener === "function") {
      mediaQuery.addListener(updateFromMediaQuery);
      return () => {
        mediaQuery.removeListener(updateFromMediaQuery);
      };
    }
    return undefined;
  }, []);

  useEffect(() => {
    const normalized = normalizeThemePreference(themePreference);
    applyThemePreference(normalized);
    try {
      window.localStorage.setItem(THEME_STORAGE_KEY, normalized);
    } catch {
      // Ignore storage failures and keep in-memory preference.
    }
  }, [themePreference]);

  useEffect(() => {
    applyFaviconForTheme(effectiveTheme);
  }, [effectiveTheme]);

  useEffect(() => {
    if (!hubStateHydrated) {
      return;
    }
    const keepProjectIds = new Set(orderedProjects.map((project) => String(project.id || "")));
    setChatFlexProjectLayoutsByProjectId((prev) => {
      let changed = false;
      const next = {};
      for (const [projectId, layoutJson] of Object.entries(prev || {})) {
        if (!keepProjectIds.has(projectId)) {
          changed = true;
          continue;
        }
        next[projectId] = layoutJson;
      }
      return changed ? next : prev;
    });
  }, [hubStateHydrated, orderedProjects]);

  useEffect(() => {
    if (!hubStateHydrated) {
      return;
    }
    const keepChatIds = new Set(orderedVisibleChats.map((chat) => String(chat.id || "")));
    setCollapsedTerminalsByChat((prev) => {
      let changed = false;
      const next = {};
      for (const [chatId, collapsed] of Object.entries(prev || {})) {
        if (!keepChatIds.has(chatId)) {
          changed = true;
          continue;
        }
        next[chatId] = collapsed;
      }
      return changed ? next : prev;
    });
  }, [hubStateHydrated, orderedVisibleChats]);

  useEffect(() => {
    if (!hubStateHydrated) {
      return;
    }
    if (layoutJsonEquals(chatFlexOuterLayoutJson || null, chatFlexOuterLayoutReconciledJson || null)) {
      return;
    }
    setChatFlexOuterLayoutJson(chatFlexOuterLayoutReconciledJson || null);
  }, [chatFlexOuterLayoutJson, chatFlexOuterLayoutReconciledJson, hubStateHydrated]);

  useEffect(() => {
    if (!hubStateHydrated) {
      return;
    }
    if (layoutJsonMapEquals(chatFlexProjectLayoutsByProjectId, chatFlexProjectLayoutsReconciledByProjectId)) {
      return;
    }
    setChatFlexProjectLayoutsByProjectId(chatFlexProjectLayoutsReconciledByProjectId || {});
  }, [chatFlexProjectLayoutsByProjectId, chatFlexProjectLayoutsReconciledByProjectId, hubStateHydrated]);

  useEffect(() => {
    writeLocalStorageJson(CHAT_FLEX_OUTER_LAYOUT_STORAGE_KEY, chatFlexOuterLayoutJson || null);
  }, [chatFlexOuterLayoutJson]);

  useEffect(() => {
    writeLocalStorageJson(CHAT_FLEX_PROJECT_LAYOUT_STORAGE_KEY, chatFlexProjectLayoutsByProjectId || {});
  }, [chatFlexProjectLayoutsByProjectId]);

  useEffect(() => {
    writeLocalStorageJson(CHAT_TERMINAL_COLLAPSE_STORAGE_KEY, collapsedTerminalsByChat || {});
  }, [collapsedTerminalsByChat]);

  useEffect(() => {
    if (!fullscreenChatId) {
      return;
    }
    const exists = orderedVisibleChats.some((chat) => chat.id === fullscreenChatId);
    if (!exists) {
      setFullscreenChatId("");
    }
  }, [orderedVisibleChats, fullscreenChatId]);

  useEffect(() => {
    if (activeTab !== "chats") {
      if (fullscreenChatId) {
        setFullscreenChatId("");
      }
      if (artifactPreview) {
        setArtifactPreview(null);
      }
    }
  }, [activeTab, fullscreenChatId, artifactPreview]);

  useEffect(() => {
    if (!fullscreenChatId && !artifactPreview) {
      return undefined;
    }
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = previousOverflow;
    };
  }, [fullscreenChatId, artifactPreview]);

  useEffect(() => {
    if (!fullscreenChatId || artifactPreview) {
      return undefined;
    }
    const onKeyDown = (event) => {
      if (event.key === "Escape") {
        setFullscreenChatId("");
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => {
      window.removeEventListener("keydown", onKeyDown);
    };
  }, [fullscreenChatId, artifactPreview]);

  useEffect(() => {
    if (!artifactPreview) {
      return undefined;
    }
    const onKeyDown = (event) => {
      if (event.key === "Escape") {
        setArtifactPreview(null);
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => {
      window.removeEventListener("keydown", onKeyDown);
    };
  }, [artifactPreview]);

  function resolveServerChatId(chat) {
    return String(chat?.server_chat_id || chat?.id || "");
  }

  function hasServerChat(chat) {
    return Boolean(chat?.server_chat_id || !String(chat?.id || "").startsWith("pending-"));
  }

  function toggleProjectChatRows(groupKey, chats, currentlyCollapsed) {
    const nextCollapsed = !currentlyCollapsed;
    setCollapsedProjectChats((prev) => ({ ...prev, [groupKey]: nextCollapsed }));
    setOpenChats((prev) => {
      const next = { ...prev };
      for (const chat of chats) {
        next[chat.id] = !nextCollapsed;
      }
      return next;
    });
    if (nextCollapsed) {
      setOpenChatDetails((prev) => {
        const next = { ...prev };
        for (const chat of chats) {
          next[chat.id] = false;
        }
        return next;
      });
      setFullscreenChatId((current) => (chats.some((chat) => chat.id === current) ? "" : current));
    }
  }

  function toggleChatRow(chat) {
    const chatId = String(chat?.id || "");
    const currentOpen = openChats[chatId] ?? true;
    const nextOpen = !currentOpen;
    setOpenChats((prev) => ({ ...prev, [chatId]: nextOpen }));
    if (!nextOpen) {
      setOpenChatDetails((detailsPrev) => ({ ...detailsPrev, [chatId]: false }));
      setFullscreenChatId((current) => (current === chatId ? "" : current));
    }
  }

  function openOpenAiLoginHelper() {
    setOpenAiCardExpansionInitialized(true);
    if (openAiCardCanExpand) {
      setOpenAiCardExpanded(true);
    }
    setActiveTab("settings");
  }

  function renderChatTerminalControlGroup(chat, { includeDelete = true } = {}) {
    const resolvedChatId = resolveServerChatId(chat);
    const chatHasServer = hasServerChat(chat);
    const titleText = chat.display_name || chat.name;
    const terminalCollapsed = Boolean(collapsedTerminalsByChat[chat.id]);
    const detailsOpen = openChatDetails[chat.id] ?? false;
    const isFullscreenChat = fullscreenChatId === chat.id;
    const isCodexChat = normalizeAgentType(chat.agent_type, agentCapabilities) === "codex";
    const showOpenAiHelperButton = isCodexChat && !openAiAccountConnected;
    const isContainerOutdated = Boolean(chat.container_outdated);
    const containerOutdatedReason = String(chat.container_outdated_reason || "");
    const isContainerRefreshInFlight = Boolean(pendingContainerRefreshes[resolvedChatId]);
    const showContainerRefreshButton = chatHasServer && isContainerOutdated;
    const containerRefreshTooltip = isContainerRefreshInFlight
      ? "Refreshing container with latest snapshot..."
      : containerOutdatedReason || "Running on an out-of-date container. Refresh to restart on latest snapshot.";

    return (
      <>
        {showOpenAiHelperButton ? (
          <button
            type="button"
            className="chat-header-helper-button"
            title="Open OpenAI account login helper"
            aria-label={`Open OpenAI account login helper for ${chat.display_name || chat.name}`}
            onClick={openOpenAiLoginHelper}
          >
            <InfoIcon />
            <span>Connect account</span>
          </button>
        ) : null}
        {showContainerRefreshButton ? (
          <button
            type="button"
            className={`icon-button chat-header-icon chat-header-refresh${isContainerRefreshInFlight ? " is-refreshing" : ""}`}
            title={containerRefreshTooltip}
            aria-label={
              isContainerRefreshInFlight
                ? `Refreshing container for ${chat.display_name || chat.name}`
                : `Refresh out-of-date container for ${chat.display_name || chat.name}`
            }
            disabled={isContainerRefreshInFlight}
            onClick={() => handleRefreshChatContainer(resolvedChatId)}
          >
            <RefreshWarningIcon />
          </button>
        ) : null}
        <button
          type="button"
          className="icon-button chat-header-icon chat-header-collapse"
          title={terminalCollapsed ? "Expand terminal" : "Collapse terminal"}
          aria-label={terminalCollapsed ? `Expand terminal for ${titleText}` : `Collapse terminal for ${titleText}`}
          aria-pressed={terminalCollapsed}
          onClick={() => {
            setCollapsedTerminalsByChat((prev) => ({
              ...(prev || {}),
              [chat.id]: !(prev?.[chat.id] ?? false)
            }));
          }}
        >
          <span aria-hidden="true">{terminalCollapsed ? "+" : "−"}</span>
        </button>
        <button
          type="button"
          className="icon-button chat-header-icon chat-header-popout"
          title={isFullscreenChat ? "Minimize" : "Pop out"}
          aria-label={isFullscreenChat ? `Minimize ${chat.display_name || chat.name}` : `Pop out ${chat.display_name || chat.name}`}
          onClick={() => {
            if (isFullscreenChat) {
              setFullscreenChatId("");
              return;
            }
            setOpenChats((prev) => ({ ...prev, [chat.id]: true }));
            setFullscreenChatId(chat.id);
          }}
        >
          {isFullscreenChat ? <MinimizeIcon /> : <ExpandIcon />}
        </button>
        <button
          type="button"
          className="icon-button chat-header-icon"
          title={detailsOpen ? "Hide details" : "Show details"}
          aria-label={detailsOpen ? `Hide details for ${chat.display_name || chat.name}` : `Show details for ${chat.display_name || chat.name}`}
          onClick={() => {
            setOpenChatDetails((prev) => ({ ...prev, [chat.id]: !(prev[chat.id] ?? false) }));
          }}
        >
          <EllipsisIcon />
        </button>
        {includeDelete ? (
          <button
            type="button"
            className="icon-button chat-header-icon chat-header-delete"
            title={`Delete ${titleText}`}
            aria-label={`Delete ${titleText}`}
            onClick={() => {
              if (!chatHasServer) {
                setPendingSessions((prev) => prev.filter((session) => session.ui_id !== chat.id));
                setOpenChats((prev) => {
                  const next = { ...prev };
                  delete next[chat.id];
                  return next;
                });
                setOpenChatDetails((prev) => {
                  const next = { ...prev };
                  delete next[chat.id];
                  return next;
                });
                setFullscreenChatId((current) => (current === chat.id ? "" : current));
                return;
              }
              handleDeleteChat(resolvedChatId, chat.id);
            }}
          >
            <CloseIcon />
          </button>
        ) : null}
      </>
    );
  }

  function renderChatCard(chat, options = {}) {
    const resolvedChatId = resolveServerChatId(chat);
    const chatHasServer = hasServerChat(chat);
    const normalizedStatus = String(chat.status || "").toLowerCase();
    const isRunning = Boolean(chat.is_running);
    const pendingStart = Boolean(pendingChatStarts[resolvedChatId] || chat.is_pending_start);
    const isStarting = isChatStarting(normalizedStatus, isRunning, pendingStart);
    const isFailed = normalizedStatus === "failed";
    const statusClassName = isRunning ? "running" : isStarting ? "starting" : isFailed ? "failed" : "stopped";
    const statusLabel = isRunning
      ? "running"
      : isStarting
        ? "starting"
        : isFailed
          ? "failed"
          : (normalizedStatus === "running" ? "stopped" : (normalizedStatus || "stopped"));
    const statusReason = String(chat.status_reason || "").trim();
    const startError = String(chat.start_error || "").trim();
    const titleStatus = String(chat.title_status || "idle").toLowerCase();
    const volumeCount = (chat.ro_mounts || []).length + (chat.rw_mounts || []).length;
    const envCount = (chat.env_vars || []).length;
    const artifacts = Array.isArray(chat.artifacts) ? chat.artifacts : [];
    const artifactById = new Map(
      artifacts
        .map((artifact) => [String(artifact?.id || ""), artifact])
        .filter(([artifactId]) => artifactId)
    );
    const hasCurrentArtifactIds = Array.isArray(chat.artifact_current_ids);
    const currentArtifactIds = hasCurrentArtifactIds
      ? chat.artifact_current_ids
        .map((artifactId) => String(artifactId || "").trim())
        .filter((artifactId, index, source) =>
          artifactId && source.indexOf(artifactId) === index && artifactById.has(artifactId)
        )
      : artifacts
        .map((artifact) => String(artifact?.id || "").trim())
        .filter(Boolean);
    const currentArtifacts = currentArtifactIds
      .map((artifactId) => artifactById.get(artifactId))
      .filter(Boolean);
    const artifactPromptHistory = Array.isArray(chat.artifact_prompt_history)
      ? chat.artifact_prompt_history
        .map((entry) => {
          const prompt = String(entry?.prompt || "").trim();
          if (!prompt) {
            return null;
          }
          const entryArtifacts = (Array.isArray(entry?.artifacts) ? entry.artifacts : [])
            .map((artifact) => {
              const artifactId = String(artifact?.id || "").trim();
              if (artifactId && artifactById.has(artifactId)) {
                return artifactById.get(artifactId);
              }
              if (!artifact || typeof artifact !== "object") {
                return null;
              }
              return artifact;
            })
            .filter(Boolean);
          if (entryArtifacts.length === 0) {
            return null;
          }
          return {
            prompt,
            archivedAt: String(entry?.archived_at || ""),
            artifacts: entryArtifacts
          };
        })
        .filter(Boolean)
      : [];
    const detailsOpen = openChatDetails[chat.id] ?? false;
    const thumbnailsVisible = showArtifactThumbnailsByChat[chat.id] ?? true;
    const isFullscreenChat = fullscreenChatId === chat.id;
    const containerClassName = ["card", isFullscreenChat ? "chat-card-popped" : ""].filter(Boolean).join(" ");
    const titleText = chat.display_name || chat.name;
    const titleStateLabel = titleStatus === "error" ? "Title error" : "";
    const terminalStatusOverride = isRunning ? "" : statusClassName;
    const terminalCollapsed = Boolean(collapsedTerminalsByChat[chat.id]);
    const terminalTabs = Array.isArray(options?.terminalTabs)
      ? options.terminalTabs
        .map((tab) => ({
          id: String(tab?.id || "").trim(),
          label: String(tab?.label || "").trim()
        }))
        .filter((tab) => Boolean(tab.id && tab.label))
      : [];
    const activeTerminalTabId = String(options?.activeTerminalTabId || "");
    const onSelectTerminalTab = typeof options?.onSelectTerminalTab === "function"
      ? options.onSelectTerminalTab
      : null;

    const buildArtifactRenderInfo = (artifact) => {
      const artifactId = String(artifact?.id || "");
      const artifactName = String(artifact?.name || artifact?.relative_path || "artifact");
      const iconDescriptor = resolveArtifactIcon(artifact);
      const previewKind =
        iconDescriptor.variant === "image" || iconDescriptor.variant === "video" ? iconDescriptor.variant : "";
      const previewUrl = String(artifact?.preview_url || artifact?.download_url || "");
      const downloadUrl = String(artifact?.download_url || "");
      const canPreview = Boolean(previewKind && previewUrl);
      const artifactMeta = [
        formatBytes(artifact?.size_bytes),
        formatTimestamp(artifact?.created_at)
      ]
        .filter(Boolean)
        .join(" • ");
      return {
        artifactId,
        artifactName,
        iconDescriptor,
        previewKind,
        previewUrl,
        downloadUrl,
        canPreview,
        artifactMeta
      };
    };

    const openArtifactPreview = (artifactInfo) =>
      setArtifactPreview({
        chatId: chat.id,
        artifactId: artifactInfo.artifactId,
        name: artifactInfo.artifactName,
        kind: artifactInfo.previewKind,
        previewUrl: artifactInfo.previewUrl,
        downloadUrl: artifactInfo.downloadUrl || artifactInfo.previewUrl
      });

    const renderArtifactBubble = (artifact, keyPrefix = "artifact", precomputedInfo = null) => {
      const artifactInfo = precomputedInfo || buildArtifactRenderInfo(artifact);
      const hoverMeta = artifactInfo.artifactMeta || (artifactInfo.downloadUrl ? "Ready to download" : "Unavailable");
      const bubbleContent = (
        <>
          <span className="chat-artifact-icon" aria-hidden="true">
            {renderArtifactIcon(artifactInfo.iconDescriptor)}
          </span>
          <span className="chat-artifact-name" title={artifactInfo.artifactName}>{artifactInfo.artifactName}</span>
          <span className="chat-artifact-meta-hover">{hoverMeta}</span>
        </>
      );
      if (artifactInfo.canPreview) {
        return (
          <button
            key={`${keyPrefix}-${artifactInfo.artifactId || artifactInfo.artifactName}`}
            type="button"
            className="chat-artifact-bubble chat-artifact-bubble-action"
            aria-label={`Preview ${artifactInfo.artifactName}`}
            title={`${artifactInfo.artifactName} (preview)`}
            onClick={() => openArtifactPreview(artifactInfo)}
          >
            {bubbleContent}
          </button>
        );
      }
      if (artifactInfo.downloadUrl) {
        return (
          <a
            key={`${keyPrefix}-${artifactInfo.artifactId || artifactInfo.artifactName}`}
            className="chat-artifact-bubble"
            href={artifactInfo.downloadUrl}
            download={artifactInfo.artifactName}
            aria-label={`Download ${artifactInfo.artifactName}`}
            title={artifactInfo.artifactName}
          >
            {bubbleContent}
          </a>
        );
      }
      return (
        <span
          key={`${keyPrefix}-${artifactInfo.artifactId || artifactInfo.artifactName}`}
          className="chat-artifact-bubble chat-artifact-bubble-unavailable"
          title={artifactInfo.artifactName}
        >
          {bubbleContent}
        </span>
      );
    };

    const renderArtifactThumbnail = (artifact, keyPrefix = "artifact-thumbnail", precomputedInfo = null) => {
      const artifactInfo = precomputedInfo || buildArtifactRenderInfo(artifact);
      if (!artifactInfo.canPreview) {
        return renderArtifactBubble(artifact, keyPrefix, artifactInfo);
      }
      return (
        <button
          key={`${keyPrefix}-${artifactInfo.artifactId || artifactInfo.artifactName}`}
          type="button"
          className="chat-artifact-thumbnail"
          aria-label={`Preview ${artifactInfo.artifactName}`}
          title={`${artifactInfo.artifactName} (preview)`}
          onClick={() => openArtifactPreview(artifactInfo)}
        >
          <span className="chat-artifact-thumbnail-media-wrap" aria-hidden="true">
            {artifactInfo.previewKind === "video" ? (
              <video
                className="chat-artifact-thumbnail-media"
                src={artifactInfo.previewUrl}
                muted
                preload="metadata"
                playsInline
              />
            ) : (
              <img
                className="chat-artifact-thumbnail-media"
                src={artifactInfo.previewUrl}
                alt=""
                loading="lazy"
                decoding="async"
              />
            )}
          </span>
          <span className="chat-artifact-thumbnail-name" title={artifactInfo.artifactName}>
            {artifactInfo.artifactName}
          </span>
          {artifactInfo.artifactMeta ? (
            <span className="chat-artifact-thumbnail-meta">{artifactInfo.artifactMeta}</span>
          ) : null}
        </button>
      );
    };

    const currentArtifactItems = currentArtifacts.map((artifact, index) => ({
      artifact,
      index,
      artifactInfo: buildArtifactRenderInfo(artifact)
    }));
    const nonPreviewableCurrentArtifacts = currentArtifactItems.filter(({ artifactInfo }) => !artifactInfo.canPreview);
    const previewableCurrentArtifacts = currentArtifactItems.filter(({ artifactInfo }) => artifactInfo.canPreview);
    const moveTerminalControlsToTabRow = Boolean(options?.moveTerminalControlsToTabRow) && !isFullscreenChat;
    const terminalToolbarActions = moveTerminalControlsToTabRow ? null : renderChatTerminalControlGroup(chat);
    const terminalOverlay = !isRunning && isStarting ? <span className="inline-spinner" aria-hidden="true" /> : null;

    return (
      <article className={containerClassName} key={chat.id}>
        <div className="stack compact chat-card-body">
          {!terminalCollapsed && detailsOpen ? (
            <section className="chat-details">
              <div className="meta">
                Status:{" "}
                <span className={`status ${statusClassName}`}>
                  {statusLabel}
                </span>
              </div>
              {statusReason ? <div className="meta">Status reason: {statusReason}</div> : null}
              {startError ? <div className="meta build-error">Start error: {startError}</div> : null}
              {chat.last_exit_code !== null && chat.last_exit_code !== undefined
                ? <div className="meta">Last exit code: {chat.last_exit_code}</div>
                : null}
              <div className="meta">Title status: {titleStatus || "idle"}</div>
              {chat.title_error ? <div className="meta build-error">Title generation error: {chat.title_error}</div> : null}
              <div className="meta">Chat ID: {resolvedChatId || "starting..."}</div>
              <div className="meta">Agent: {agentTypeLabel(chat.agent_type, agentCapabilities)}</div>
              <div className="meta">Workspace: {chat.workspace}</div>
              <div className="meta">Container folder: {chat.container_workspace || "not started yet"}</div>
              {chat.setup_snapshot_image ? (
                <div className="meta">Setup snapshot image: {chat.setup_snapshot_image}</div>
              ) : null}
              <div className="meta">Volumes: {volumeCount} | Env vars: {envCount}</div>
              {artifactPromptHistory.length > 0 ? (
                <details className="details-block chat-artifact-history-block">
                  <summary className="details-summary">Historical files ({artifactPromptHistory.length})</summary>
                  <div className="chat-artifact-history-list">
                    {artifactPromptHistory.map((historyEntry, index) => (
                      <section
                        className="chat-artifact-history-entry"
                        key={`history-${chat.id}-${index}-${historyEntry.prompt}`}
                      >
                        <div className="meta chat-artifact-history-label">
                          Prompt:{" "}
                          <span className="chat-artifact-history-prompt">{historyEntry.prompt}</span>
                          {historyEntry.archivedAt ? ` (${formatTimestamp(historyEntry.archivedAt)})` : ""}
                        </div>
                        <div className="chat-artifact-list">
                          {historyEntry.artifacts.map((artifact, artifactIndex) =>
                            renderArtifactBubble(artifact, `history-${index}-${artifactIndex}`)
                          )}
                        </div>
                      </section>
                    ))}
                  </div>
                </details>
              ) : null}
            </section>
          ) : null}

          <ChatTerminal
            chatId={resolvedChatId}
            running={isRunning}
            effectiveTheme={effectiveTheme}
            statusOverride={terminalStatusOverride}
            title={titleText}
            titleStateLabel={titleStateLabel}
            titleStateClassName={titleStatus}
            toolbarActions={terminalToolbarActions}
            showToolbar={!moveTerminalControlsToTabRow}
            overlay={terminalOverlay}
            collapsed={terminalCollapsed}
            tabs={terminalTabs}
            activeTabId={activeTerminalTabId}
            onTabSelect={onSelectTerminalTab}
          />

          {!terminalCollapsed && !isRunning && chatHasServer && !isStarting ? (
            <div className="stack compact">
              <div className="meta chat-terminal-stopped">
                {isFailed ? "Chat failed. Review the error and retry." : "Chat is stopped. Start it to reconnect the terminal."}
              </div>
              {isFailed && startError ? <div className="meta build-error">{startError}</div> : null}
              <div className="actions chat-actions">
                <button
                  type="button"
                  className="btn-primary chat-primary-action"
                  onClick={() => handleStartChat(resolvedChatId)}
                >
                  {isFailed ? "Retry chat" : "Start chat"}
                </button>
              </div>
            </div>
          ) : null}

          {!terminalCollapsed && currentArtifacts.length > 0 ? (
            <section className="chat-artifacts" aria-label={`Generated files for ${titleText}`}>
              {nonPreviewableCurrentArtifacts.length > 0 ? (
                <div className="chat-artifact-list">
                  {nonPreviewableCurrentArtifacts.map(({ artifact, index, artifactInfo }) =>
                    renderArtifactBubble(artifact, `current-${index}`, artifactInfo)
                  )}
                </div>
              ) : null}
              {previewableCurrentArtifacts.length > 0 ? (
                <>
                  <div className="chat-artifact-preview-header">
                    <button
                      type="button"
                      className="btn-secondary btn-small chat-artifact-thumbnail-toggle"
                      aria-label={`${thumbnailsVisible ? "Hide" : "Show"} thumbnails for ${titleText}`}
                      aria-pressed={thumbnailsVisible}
                      onClick={() =>
                        setShowArtifactThumbnailsByChat((prev) => ({
                          ...prev,
                          [chat.id]: !(prev[chat.id] ?? true)
                        }))
                      }
                    >
                      {thumbnailsVisible ? "Hide thumbnails" : "Show thumbnails"}
                    </button>
                  </div>
                  {thumbnailsVisible ? (
                    <div className="chat-artifact-thumbnail-row">
                      {previewableCurrentArtifacts.map(({ artifact, index, artifactInfo }) =>
                        renderArtifactThumbnail(artifact, `current-preview-${index}`, artifactInfo)
                      )}
                    </div>
                  ) : (
                    <div className="chat-artifact-list chat-artifact-bubble-row">
                      {previewableCurrentArtifacts.map(({ artifact, index, artifactInfo }) =>
                        renderArtifactBubble(artifact, `current-preview-bubble-${index}`, artifactInfo)
                      )}
                    </div>
                  )}
                </>
              ) : null}
            </section>
          ) : null}
        </div>
      </article>
    );
  }

  function renderProjectChatGroup(project, keyPrefix = "group") {
    const projectChats = chatsByProject.byProject.get(project.id) || [];
    const buildStatus = String(project.build_status || "pending");
    const canStartChat = buildStatus === "ready";
    const isBuilding = buildStatus === "building" || Boolean(pendingProjectBuilds[project.id]);
    const isCreatingChat = Boolean(pendingProjectChatCreates[project.id]);
    const projectRowsCollapsed = Boolean(collapsedProjectChats[project.id]);
    const projectStartSettings = normalizeChatStartSettings(
      chatStartSettingsByProject[project.id] || { agentType: hubSettings.defaultAgentType },
      agentCapabilities
    );
    const projectAgentOptions = agentTypeOptions(agentCapabilities);
    const projectModelOptions = startModelOptionsForAgent(projectStartSettings.agentType, agentCapabilities);
    const projectReasoningOptions = reasoningModeOptionsForAgent(projectStartSettings.agentType, agentCapabilities);

    return (
      <article className="card project-chat-group" key={`${keyPrefix}-${project.id}`}>
        <div
          className="project-head project-chat-head project-chat-row"
          role="button"
          tabIndex={0}
          onClick={() => toggleProjectChatRows(project.id, projectChats, projectRowsCollapsed)}
          onKeyDown={(event) => {
            if (event.key === "Enter" || event.key === " ") {
              event.preventDefault();
              toggleProjectChatRows(project.id, projectChats, projectRowsCollapsed);
            }
          }}
        >
          <h3>{project.name}</h3>
          <div className="actions project-head-actions">
            {canStartChat ? (
              <>
                <select
                  className="project-start-select"
                  aria-label={`Agent for ${project.name}`}
                  value={projectStartSettings.agentType}
                  onClick={(event) => event.stopPropagation()}
                  onKeyDown={(event) => event.stopPropagation()}
                  onChange={(event) => {
                    event.stopPropagation();
                    updateProjectChatStartSettings(project.id, { agentType: event.target.value });
                  }}
                >
                  {projectAgentOptions.map((option) => (
                    <option key={`${project.id}-agent-${option.value}`} value={option.value}>
                      {option.label}
                    </option>
                  ))}
                </select>
                <select
                  className="project-start-select"
                  aria-label={`Start model for ${project.name}`}
                  value={projectStartSettings.model}
                  onClick={(event) => event.stopPropagation()}
                  onKeyDown={(event) => event.stopPropagation()}
                  onChange={(event) => {
                    event.stopPropagation();
                    updateProjectChatStartSettings(project.id, { model: event.target.value });
                  }}
                >
                  {projectModelOptions.map((modelOption) => (
                    <option key={`${project.id}-model-${projectStartSettings.agentType}-${modelOption}`} value={modelOption}>
                      {modelOption}
                    </option>
                  ))}
                </select>
                <select
                  className="project-start-select"
                  aria-label={`Reasoning mode for ${project.name}`}
                  value={projectStartSettings.reasoning}
                  onClick={(event) => event.stopPropagation()}
                  onKeyDown={(event) => event.stopPropagation()}
                  onChange={(event) => {
                    event.stopPropagation();
                    updateProjectChatStartSettings(project.id, { reasoning: event.target.value });
                  }}
                >
                  {projectReasoningOptions.map((reasoningMode) => (
                    <option key={`${project.id}-reasoning-${reasoningMode}`} value={reasoningMode}>
                      {reasoningMode}
                    </option>
                  ))}
                </select>
              </>
            ) : null}
            <button
              type="button"
              className="btn-primary project-group-action"
              disabled={isCreatingChat || (!canStartChat && isBuilding)}
              onClick={(event) => {
                event.stopPropagation();
                if (canStartChat) {
                  handleCreateChat(project.id);
                } else {
                  handleBuildProject(project.id);
                }
              }}
            >
              {canStartChat
                ? (
                  isCreatingChat
                    ? <SpinnerLabel text="Starting chat..." />
                    : "New chat"
                )
                : isBuilding
                  ? <SpinnerLabel text="Building image..." />
                  : "Build image"}
            </button>
          </div>
        </div>

        {projectRowsCollapsed ? (
          <div className="meta">Chats hidden ({projectChats.length})</div>
        ) : (
          <div className="stack compact">
            {projectChats.length === 0 ? <div className="empty">No chats yet for this project.</div> : null}
            {projectChats.map((chat) => renderChatCard(chat))}
          </div>
        )}
      </article>
    );
  }

  function renderOrphanChatGroup(keyPrefix = "group-orphan") {
    return (
      <article className="card project-chat-group" key={keyPrefix}>
        <div
          className="project-head project-chat-row"
          role="button"
          tabIndex={0}
          onClick={() =>
            toggleProjectChatRows("__orphan__", chatsByProject.orphanChats, Boolean(collapsedProjectChats.__orphan__))
          }
          onKeyDown={(event) => {
            if (event.key === "Enter" || event.key === " ") {
              event.preventDefault();
              toggleProjectChatRows("__orphan__", chatsByProject.orphanChats, Boolean(collapsedProjectChats.__orphan__));
            }
          }}
        >
          <h3>Unknown project</h3>
        </div>
        {collapsedProjectChats.__orphan__ ? (
          <div className="meta">Chats hidden ({chatsByProject.orphanChats.length})</div>
        ) : (
          <div className="stack compact">
            {chatsByProject.orphanChats.map((chat) => renderChatCard(chat))}
          </div>
        )}
      </article>
    );
  }

  function renderClassicChatsLayout() {
    return (
      <div className="stack chat-groups">
        {orderedProjects.length === 0 ? <div className="empty">No projects yet.</div> : null}
        {orderedProjects.map((project) => renderProjectChatGroup(project))}
        {chatsByProject.orphanChats.length > 0 ? renderOrphanChatGroup() : null}
      </div>
    );
  }

  function renderFlexLayoutProjectHeaderControls(tabSetNode, renderValues) {
    const selectedNode = tabSetNode?.getSelectedNode?.();
    if (!selectedNode) {
      return;
    }
    const selectedComponent = String(selectedNode.getComponent() || "");
    if (selectedComponent !== "project-chat-group") {
      return;
    }
    const projectId = String(selectedNode.getConfig()?.project_id || "");
    const project = projectsById.get(projectId);
    if (!project) {
      return;
    }
    const buildStatus = String(project.build_status || "pending");
    const canStartChat = buildStatus === "ready";
    const isBuilding = buildStatus === "building" || Boolean(pendingProjectBuilds[project.id]);
    const isCreatingChat = Boolean(pendingProjectChatCreates[project.id]);
    const projectStartSettings = normalizeChatStartSettings(
      chatStartSettingsByProject[project.id] || { agentType: hubSettings.defaultAgentType },
      agentCapabilities
    );
    const projectAgentOptions = agentTypeOptions(agentCapabilities);
    const projectModelOptions = startModelOptionsForAgent(projectStartSettings.agentType, agentCapabilities);
    const projectReasoningOptions = reasoningModeOptionsForAgent(projectStartSettings.agentType, agentCapabilities);

    renderValues.stickyButtons.push(
      <div
        key={`outer-project-controls-${tabSetNode.getId()}`}
        className="chat-layout-project-controls"
        onPointerDown={(event) => event.stopPropagation()}
        onClick={(event) => event.stopPropagation()}
      >
        {canStartChat ? (
          <>
            <select
              className="project-start-select"
              aria-label={`Agent for ${project.name}`}
              value={projectStartSettings.agentType}
              onChange={(event) => {
                updateProjectChatStartSettings(project.id, { agentType: event.target.value });
              }}
            >
              {projectAgentOptions.map((option) => (
                <option key={`${project.id}-outer-agent-${option.value}`} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
            <select
              className="project-start-select"
              aria-label={`Start model for ${project.name}`}
              value={projectStartSettings.model}
              onChange={(event) => {
                updateProjectChatStartSettings(project.id, { model: event.target.value });
              }}
            >
              {projectModelOptions.map((modelOption) => (
                <option key={`${project.id}-outer-model-${projectStartSettings.agentType}-${modelOption}`} value={modelOption}>
                  {modelOption}
                </option>
              ))}
            </select>
            <select
              className="project-start-select"
              aria-label={`Reasoning mode for ${project.name}`}
              value={projectStartSettings.reasoning}
              onChange={(event) => {
                updateProjectChatStartSettings(project.id, { reasoning: event.target.value });
              }}
            >
              {projectReasoningOptions.map((reasoningMode) => (
                <option key={`${project.id}-outer-reasoning-${reasoningMode}`} value={reasoningMode}>
                  {reasoningMode}
                </option>
              ))}
            </select>
          </>
        ) : null}
        <button
          type="button"
          className="btn-primary project-group-action chat-layout-project-control-button"
          disabled={isCreatingChat || (!canStartChat && isBuilding)}
          onClick={() => {
            if (canStartChat) {
              handleCreateChat(project.id);
            } else {
              handleBuildProject(project.id);
            }
          }}
        >
          {canStartChat
            ? (
              isCreatingChat
                ? <SpinnerLabel text="Starting chat..." />
                : "New chat"
            )
            : isBuilding
              ? <SpinnerLabel text="Building image..." />
              : "Build image"}
        </button>
      </div>
    );
  }

  function renderProjectChatsFlexLayout(project) {
    const projectChats = chatsByProject.byProject.get(project.id) || [];
    if (projectChats.length === 0) {
      return <div className="empty">No chats yet for this project.</div>;
    }
    const projectChatLayoutJson = chatFlexProjectLayoutsReconciledByProjectId[project.id] || null;
    if (!projectChatLayoutJson || !projectChatLayoutJson.layout || typeof projectChatLayoutJson.layout !== "object") {
      return <div className="empty">No chats yet for this project.</div>;
    }
    const projectChatModel = chatFlexProjectModelsByProjectId[project.id];
    if (!projectChatModel) {
      return <div className="empty">Unable to load saved chat layout for this project.</div>;
    }
    const chatsById = new Map(projectChats.map((chat) => [String(chat.id || ""), chat]));
    const renderProjectChatPaneTab = (tabNode) => {
      const component = String(tabNode.getComponent() || "");
      if (component !== "project-chat-pane") {
        return <div className="empty">Unsupported project chat tab.</div>;
      }
      const chatId = String(tabNode.getConfig()?.chat_id || "");
      const chat = chatsById.get(chatId);
      if (!chat) {
        return <div className="empty">Chat no longer exists.</div>;
      }
      return <div className="chat-layout-project-chat-pane">{renderChatCard(chat, { moveTerminalControlsToTabRow: true })}</div>;
    };
    const renderProjectChatTabSetControls = (tabSetNode, renderValues) => {
      const selectedNode = tabSetNode?.getSelectedNode?.();
      if (!selectedNode) {
        return;
      }
      const component = String(selectedNode.getComponent() || "");
      if (component !== "project-chat-pane") {
        return;
      }
      const selectedChatId = String(selectedNode.getConfig()?.chat_id || "");
      const selectedChat = chatsById.get(selectedChatId);
      if (!selectedChat) {
        return;
      }
      renderValues.stickyButtons.push(
        <div
          key={`project-chat-controls-${tabSetNode.getId()}-${selectedChatId}`}
          className="terminal-toolbar-actions chat-layout-project-terminal-controls"
          onPointerDown={(event) => event.stopPropagation()}
          onClick={(event) => event.stopPropagation()}
        >
          {renderChatTerminalControlGroup(selectedChat, { includeDelete: false })}
        </div>
      );
    };
    const renderProjectChatTab = (tabNode, renderValues) => {
      const component = String(tabNode.getComponent() || "");
      if (component !== "project-chat-pane") {
        return;
      }
      const chatId = String(tabNode.getConfig()?.chat_id || "");
      const chat = chatsById.get(chatId);
      if (!chat) {
        return;
      }
      const resolvedChatId = resolveServerChatId(chat);
      const chatHasServer = hasServerChat(chat);
      const normalizedStatus = String(chat.status || "").toLowerCase();
      const isRunning = Boolean(chat.is_running);
      const pendingStart = Boolean(pendingChatStarts[resolvedChatId] || chat.is_pending_start);
      const isStarting = isChatStarting(normalizedStatus, isRunning, pendingStart);
      const isFailed = normalizedStatus === "failed";
      const statusClassName = isRunning ? "running" : isStarting ? "starting" : isFailed ? "failed" : "stopped";
      const isContainerOutdated = Boolean(chat.container_outdated);
      const isContainerRefreshInFlight = Boolean(pendingContainerRefreshes[resolvedChatId]);
      const showContainerRefreshButton = chatHasServer && isContainerOutdated;
      const containerOutdatedReason = String(chat.container_outdated_reason || "");
      const containerRefreshTooltip = isContainerRefreshInFlight
        ? "Refreshing container with latest snapshot..."
        : containerOutdatedReason || "Running on an out-of-date container. Refresh to restart on latest snapshot.";
      const chatTitle = chat.display_name || chat.name;
      if (showContainerRefreshButton) {
        renderValues.leading = (
          <button
            type="button"
            className={`icon-button chat-flex-tab-status-btn chat-header-refresh${isContainerRefreshInFlight ? " is-refreshing" : ""}`}
            title={containerRefreshTooltip}
            aria-label={isContainerRefreshInFlight ? `Refreshing container for ${chatTitle}` : `Refresh out-of-date container for ${chatTitle}`}
            disabled={isContainerRefreshInFlight}
            onPointerDown={(e) => e.stopPropagation()}
            onClick={(e) => { e.stopPropagation(); handleRefreshChatContainer(resolvedChatId); }}
          >
            <RefreshWarningIcon />
          </button>
        );
      } else {
        renderValues.leading = (
          <span
            className={`terminal-health-dot chat-flex-tab-status-dot ${statusClassName}`}
            role="img"
            aria-label={`Status: ${statusClassName}`}
            title={statusClassName}
          />
        );
      }
      renderValues.buttons.push(
        <button
          key={`flex-tab-close-${chatId}`}
          type="button"
          className="icon-button chat-flex-tab-close"
          title={`Delete ${chatTitle}`}
          aria-label={`Delete ${chatTitle}`}
          onPointerDown={(e) => e.stopPropagation()}
          onClick={(e) => {
            e.stopPropagation();
            if (!chatHasServer) {
              setPendingSessions((prev) => prev.filter((session) => session.ui_id !== chat.id));
              setOpenChats((prev) => { const next = { ...prev }; delete next[chat.id]; return next; });
              setOpenChatDetails((prev) => { const next = { ...prev }; delete next[chat.id]; return next; });
              setFullscreenChatId((current) => (current === chat.id ? "" : current));
              return;
            }
            handleDeleteChat(resolvedChatId, chat.id);
          }}
        >
          <CloseIcon />
        </button>
      );
    };
    return (
      <div className={`chat-layout-project-shell ${chatFlexLayoutThemeClass}`.trim()}>
        <Layout
          model={projectChatModel}
          factory={renderProjectChatPaneTab}
          onRenderTabSet={renderProjectChatTabSetControls}
          onRenderTab={renderProjectChatTab}
          onModelChange={(model) => {
            const nextProjectLayoutJson = model.toJson();
            setChatFlexProjectLayoutsByProjectId((prev) => {
              const currentLayoutJson = prev?.[project.id];
              if (layoutJsonEquals(currentLayoutJson || null, nextProjectLayoutJson || null)) {
                return prev;
              }
              return {
                ...(prev || {}),
                [project.id]: nextProjectLayoutJson
              };
            });
          }}
        />
      </div>
    );
  }

  function renderChatFlexLayoutTab(node) {
    const component = String(node.getComponent() || "");
    if (component === "project-chat-group") {
      const projectId = String(node.getConfig()?.project_id || "");
      const project = projectsById.get(projectId);
      if (!project) {
        return <div className="empty">Project no longer exists.</div>;
      }
      return <div className="chat-layout-flex-tab">{renderProjectChatsFlexLayout(project)}</div>;
    }
    if (component === "orphan-chat-group") {
      return <div className="chat-layout-flex-tab">{renderOrphanChatGroup("flex-group-orphan")}</div>;
    }
    return <div className="empty">Unsupported chat layout tab.</div>;
  }

  function renderFlexLayoutChatsLayout() {
    if (!chatFlexOuterModel || !chatFlexOuterLayoutReconciledJson) {
      return <div className="empty">No projects yet.</div>;
    }
    return (
      <div className={`chat-layout-flex-shell ${chatFlexLayoutThemeClass}`.trim()}>
        <Layout
          model={chatFlexOuterModel}
          factory={renderChatFlexLayoutTab}
          onRenderTabSet={renderFlexLayoutProjectHeaderControls}
          onModelChange={(model) => {
            const nextOuterLayoutJson = model.toJson();
            setChatFlexOuterLayoutJson((prev) => (
              layoutJsonEquals(prev || null, nextOuterLayoutJson) ? prev : nextOuterLayoutJson
            ));
          }}
        />
      </div>
    );
  }

  function renderChatsLayoutEngine() {
    const renderByEngine = {
      [CHAT_LAYOUT_ENGINE_CLASSIC]: renderClassicChatsLayout,
      [CHAT_LAYOUT_ENGINE_FLEXLAYOUT]: renderFlexLayoutChatsLayout
    };
    const renderer = renderByEngine[hubSettings.chatLayoutEngine];
    if (!renderer) {
      throw new Error(`Unsupported chat layout engine: ${hubSettings.chatLayoutEngine}`);
    }
    return renderer();
  }

  return (
    <div className="app-root">
      <header className="app-header">
        <div className="header-row">
          <div className="brand-block">
            <h1 className="brand-title">
              <img className="brand-logo" src={logoAssetForTheme(effectiveTheme)} alt="" aria-hidden="true" />
              <span>Agent Hub</span>
            </h1>
          </div>
          <nav className="tab-row" aria-label="Primary sections">
            <button
              type="button"
              className={`tab-button ${activeTab === "projects" ? "active" : ""}`}
              onClick={() => setActiveTab("projects")}
            >
              Projects
            </button>
            <button
              type="button"
              className={`tab-button ${activeTab === "chats" ? "active" : ""}`}
              onClick={() => setActiveTab("chats")}
            >
              Chats
            </button>
            <button
              type="button"
              className={`tab-button ${activeTab === "settings" ? "active" : ""}`}
              onClick={() => setActiveTab("settings")}
            >
              Settings
            </button>
          </nav>
        </div>
      </header>

      <div className={`content-shell ${activeTab === "chats" ? "content-shell-chats" : ""}`.trim()}>
        {error ? <div className="error-banner">{error}</div> : null}

        <main className="layout">
        {activeTab === "projects" ? (
          <section className="panel projects-panel">
            <div className="projects-split">
              <section className="projects-create">
                <h3 className="section-title">Add project</h3>
                <form className="stack" onSubmit={handleCreateProject}>
                  <input
                    required
                    value={createForm.repoUrl}
                    onChange={(event) => updateCreateForm({ repoUrl: event.target.value })}
                    placeholder="git@github.com:org/repo.git or https://..."
                  />
                  <div className="create-project-config-mode">
                    <div className="create-project-config-mode-inline">
                      <span className="create-project-config-mode-title" id="create-project-config-mode-label">
                        Config mode
                      </span>
                      <div className="create-project-config-mode-control">
                        <span
                          className={`create-project-config-mode-option ${!createProjectManualMode ? "active" : ""}`}
                          aria-hidden="true"
                        >
                          Auto
                        </span>
                        <button
                          type="button"
                          className={`create-project-config-switch ${createProjectManualMode ? "manual" : "auto"}`}
                          role="switch"
                          aria-checked={createProjectManualMode}
                          aria-labelledby="create-project-config-mode-label"
                          onClick={() => setCreateProjectConfigMode(createProjectManualMode ? "auto" : "manual")}
                        >
                          <span className="create-project-config-switch-track" aria-hidden="true" />
                          <span className="create-project-config-switch-thumb" aria-hidden="true" />
                        </button>
                        <span
                          className={`create-project-config-mode-option ${createProjectManualMode ? "active" : ""}`}
                          aria-hidden="true"
                        >
                          Manual
                        </span>
                      </div>
                    </div>
                  </div>
                  {createProjectManualMode ? (
                    <>
                      <div className="row two">
                        <input
                          value={createForm.name}
                          onChange={(event) => updateCreateForm({ name: event.target.value })}
                          placeholder="Optional project name"
                        />
                        <input
                          value={createForm.defaultBranch}
                          onChange={(event) => updateCreateForm({ defaultBranch: event.target.value })}
                          placeholder="Default branch (optional)"
                        />
                      </div>
                      <div className="row two">
                        <select
                          value={createForm.baseImageMode}
                          onChange={(event) => updateCreateForm({ baseImageMode: event.target.value })}
                        >
                          <option value="tag">Docker image tag</option>
                          <option value="repo_path">Repo Dockerfile/path</option>
                        </select>
                        <input
                          value={createForm.baseImageValue}
                          onChange={(event) => updateCreateForm({ baseImageValue: event.target.value })}
                          placeholder={baseInputPlaceholder(createForm.baseImageMode)}
                        />
                      </div>
                      <textarea
                        className="script-input"
                        value={createForm.setupScript}
                        onChange={(event) => updateCreateForm({ setupScript: event.target.value })}
                        placeholder={
                          "Setup script (one command per line; runs in container with checked-out project as working directory)\n" +
                          "example:\nuv sync\nuv run python -m pip install -e ."
                        }
                      />

                      <div className="label">Default volumes for new chats</div>
                      <VolumeEditor rows={createForm.defaultVolumes} onChange={(rows) => updateCreateForm({ defaultVolumes: rows })} />

                      <div className="label">Default environment variables for new chats</div>
                      <EnvVarEditor rows={createForm.defaultEnvVars} onChange={(rows) => updateCreateForm({ defaultEnvVars: rows })} />
                    </>
                  ) : null}

                  <button type="submit" className="btn-primary create-project-submit-button">
                    Add project
                  </button>
                </form>
              </section>

              <section className="projects-list">
                <h3 className="section-title">Existing projects</h3>
                <div className="stack">
              {projectsForList.length === 0 ? <div className="empty">No projects yet.</div> : null}
              {projectsForList.map((project) => {
                if (project.is_auto_config_pending) {
                  const isFailed = String(project.build_status || "") === "failed";
                  const statusLabel = isFailed ? "Auto configure failed" : "Auto configuring";
                  return (
                    <article className="card project-card" key={project.id}>
                      <div className="project-head">
                        <h3>{project.name}</h3>
                      </div>
                      <div className="meta">ID: {project.id}</div>
                      <div className="meta">Repo: {project.repo_url}</div>
                      <div className="meta">Branch: {project.default_branch || "auto-detect"}</div>
                      <div className="meta">
                        Status:{" "}
                        <span className={`project-build-state ${isFailed ? "failed" : "building"}`}>
                          {statusLabel}
                        </span>
                      </div>
                      <div className="meta auto-config-meta">
                        Running temporary analysis chat, then creating a configured project entry.
                      </div>
                      <ProjectBuildTerminal
                        title="Temporary analysis chat"
                        shellClassName="auto-config-terminal-shell"
                        viewClassName="auto-config-terminal-view"
                        effectiveTheme={effectiveTheme}
                        text={project.auto_config_log || "Waiting for temporary analysis chat output...\r\n"}
                      />
                      {isFailed ? (
                        <>
                          <div className="meta build-error">
                            {project.build_error || "Auto configure failed before project creation completed."}
                          </div>
                          <div className="actions project-collapsed-actions">
                            <button
                              type="button"
                              className="btn-danger project-collapsed-delete"
                              onClick={() => handleDeletePendingAutoConfigProject(project.id)}
                            >
                              Delete project
                            </button>
                          </div>
                        </>
                      ) : null}
                    </article>
                  );
                }
                const draft = projectDrafts[project.id] || projectDraftFromProject(project);
                const setupCommands = String(project.setup_script || "")
                  .split("\n")
                  .map((line) => line.trim())
                  .filter(Boolean);
                const defaultRoMounts = project.default_ro_mounts || [];
                const defaultRwMounts = project.default_rw_mounts || [];
                const defaultEnvVars = project.default_env_vars || [];
                const defaultVolumeCount =
                  defaultRoMounts.length + defaultRwMounts.length;
                const defaultEnvCount = defaultEnvVars.length;
                const buildStatus = String(project.build_status || "pending");
                const statusInfo = projectStatusInfo(buildStatus);
                const isBuilding = buildStatus === "building" || Boolean(pendingProjectBuilds[project.id]);
                const canStartChat = buildStatus === "ready";
                const isCreatingChat = Boolean(pendingProjectChatCreates[project.id]);
                const isEditing = Boolean(editingProjects[project.id]);
                const isStoredLogOpen = Boolean(openBuildLogs[project.id]);
                const storedLogText = projectStaticLogs[project.id];
                const hasStoredLogText = Boolean(String(storedLogText || "").trim());
                const hasLiveLogText = Boolean(String(projectBuildLogs[project.id] || "").trim());
                const hasBuildLog = Boolean(project.has_build_log) || hasStoredLogText || hasLiveLogText;

                return (
                  <article className="card project-card" key={project.id}>
                    <div className="project-head">
                      <h3>{project.name}</h3>
                      <button
                        type="button"
                        className="icon-button"
                        title={isEditing ? "Collapse project settings" : "Edit project settings"}
                        aria-label={isEditing ? `Collapse settings for ${project.name}` : `Edit ${project.name}`}
                        onClick={() => {
                          if (isEditing) {
                            handleCancelProjectEdit(project);
                            return;
                          }
                          handleEditProject(project);
                        }}
                      >
                        {isEditing ? "✕" : "✎"}
                      </button>
                    </div>
                    <div className="meta">ID: {project.id}</div>
                    <div className="meta">Repo: {project.repo_url}</div>
                    <div className="meta">Branch: {project.default_branch || "master"}</div>
                    <div className="meta project-status-meta">
                      <span>Status:</span>
                      <span className={`project-build-state ${statusInfo.key}`}>{statusInfo.label}</span>
                      {hasBuildLog ? (
                        <button
                          type="button"
                          className={`icon-button build-log-icon-button ${isStoredLogOpen ? "is-open" : ""}`}
                          title="image build log"
                          aria-label={`image build log for ${project.name}`}
                          onClick={() => handleToggleStoredBuildLog(project.id)}
                        >
                          <MdDescription aria-hidden="true" />
                        </button>
                      ) : null}
                    </div>
                    <div className="meta">
                      Base image source:{" "}
                      {project.base_image_value
                        ? `${baseModeLabel(normalizeBaseMode(project.base_image_mode))}: ${project.base_image_value}`
                        : "Default agent_cli base image"}
                    </div>
                    {project.setup_snapshot_image ? (
                      <div className="meta">Setup snapshot image: {project.setup_snapshot_image}</div>
                    ) : null}
                    {project.build_error ? <div className="meta build-error">{project.build_error}</div> : null}
                    {setupCommands.length > 0 ? (
                      <details className="details-block">
                        <summary className="details-summary">Setup commands ({setupCommands.length})</summary>
                        <pre className="log-box details-log">{setupCommands.join("\n")}</pre>
                      </details>
                    ) : null}
                    {defaultVolumeCount > 0 ? (
                      <details className="details-block">
                        <summary className="details-summary">Default volumes ({defaultVolumeCount})</summary>
                        <div className="details-list">
                          {defaultRoMounts.map((mount, idx) => (
                            <div className="meta" key={`ro-${project.id}-${idx}`}>read-only: {mount}</div>
                          ))}
                          {defaultRwMounts.map((mount, idx) => (
                            <div className="meta" key={`rw-${project.id}-${idx}`}>read-write: {mount}</div>
                          ))}
                        </div>
                      </details>
                    ) : null}
                    {defaultEnvCount > 0 ? (
                      <details className="details-block">
                        <summary className="details-summary">Default environment variables ({defaultEnvCount})</summary>
                        <div className="details-list">
                          {defaultEnvVars.map((entry, idx) => (
                            <div className="meta" key={`env-${project.id}-${idx}`}>{entry}</div>
                          ))}
                        </div>
                      </details>
                    ) : null}

                    <div className="stack compact">
                      {!isEditing ? (
                        <div className="actions project-collapsed-actions">
                          <button
                            type="button"
                            className="btn-primary project-collapsed-primary"
                            disabled={isCreatingChat || (!canStartChat && isBuilding)}
                            onClick={() => (canStartChat ? handleCreateChat(project.id) : handleBuildProject(project.id))}
                          >
                            {canStartChat
                              ? (
                                isCreatingChat
                                  ? <SpinnerLabel text="Starting chat..." />
                                  : "New chat"
                              )
                              : isBuilding
                                ? <SpinnerLabel text="Building image..." />
                                : "Build"}
                          </button>
                          <button
                            type="button"
                            className="btn-danger project-collapsed-delete"
                            onClick={() => handleDeleteProject(project.id)}
                          >
                            Delete project
                          </button>
                        </div>
                      ) : null}
                      {isBuilding ? (
                        <ProjectBuildTerminal
                          effectiveTheme={effectiveTheme}
                          text={projectBuildLogs[project.id] || "Preparing project image...\r\n"}
                        />
                      ) : null}
                      {hasBuildLog && isStoredLogOpen ? (
                        <pre className="log-box">
                          {storedLogText && storedLogText.trim()
                            ? storedLogText
                            : "No stored build log found for this project yet."}
                        </pre>
                      ) : null}

                      {isEditing ? (
                        <>
                          <div className="row two">
                            <select
                              value={draft.baseImageMode}
                              onChange={(event) =>
                                updateProjectDraft(project.id, { baseImageMode: event.target.value })
                              }
                            >
                              <option value="tag">Docker image tag</option>
                              <option value="repo_path">Repo Dockerfile/path</option>
                            </select>
                            <input
                              value={draft.baseImageValue}
                              onChange={(event) =>
                                updateProjectDraft(project.id, { baseImageValue: event.target.value })
                              }
                              placeholder={baseInputPlaceholder(draft.baseImageMode)}
                            />
                          </div>

                          <textarea
                            className="script-input"
                            value={draft.setupScript}
                            onChange={(event) =>
                              updateProjectDraft(project.id, { setupScript: event.target.value })
                            }
                            placeholder="One setup command per line."
                          />

                          <div className="label">Default volumes for new chats</div>
                          <VolumeEditor
                            rows={draft.defaultVolumes}
                            onChange={(rows) => updateProjectDraft(project.id, { defaultVolumes: rows })}
                          />

                          <div className="label">Default environment variables for new chats</div>
                          <EnvVarEditor
                            rows={draft.defaultEnvVars}
                            onChange={(rows) => updateProjectDraft(project.id, { defaultEnvVars: rows })}
                          />

                          <div className="actions project-edit-actions">
                            <button
                              type="button"
                              className="btn-primary"
                              disabled={isBuilding}
                              onClick={() => handleBuildProject(project.id)}
                            >
                              {isBuilding ? <SpinnerLabel text="Building image..." /> : "Build"}
                            </button>
                            <button
                              type="button"
                              className="btn-danger project-edit-delete"
                              onClick={() => handleDeleteProject(project.id)}
                            >
                              Delete project
                            </button>
                          </div>
                        </>
                      ) : null}
                    </div>
                  </article>
                );
              })}
                </div>
              </section>
            </div>
          </section>
        ) : activeTab === "chats" ? (
          <section className="panel chats-panel">
            {renderChatsLayoutEngine()}
          </section>
        ) : (
          <section className="panel settings-panel">
            <div className="settings-heading">
              <label className="theme-control" htmlFor="theme-preference-select">
                <span>Theme</span>
                <select
                  id="theme-preference-select"
                  value={themePreference}
                  onChange={(event) => setThemePreference(normalizeThemePreference(event.target.value))}
                >
                  <option value="system">System default</option>
                  <option value="light">Light</option>
                  <option value="dark">Dark</option>
                </select>
              </label>
              <label className="theme-control" htmlFor="default-agent-type-select">
                <span>Default new-chat agent</span>
                <select
                  id="default-agent-type-select"
                  value={hubSettings.defaultAgentType}
                  onChange={(event) => handleUpdateDefaultAgentSetting(event.target.value)}
                  disabled={defaultAgentSettingSaving}
                >
                  {settingsAgentOptions.map((option) => (
                    <option key={`settings-default-agent-${option.value}`} value={option.value}>
                      {option.label}
                    </option>
                  ))}
                </select>
              </label>
              <label className="theme-control" htmlFor="chat-layout-engine-select">
                <span>Chats layout engine</span>
                <select
                  id="chat-layout-engine-select"
                  value={hubSettings.chatLayoutEngine}
                  onChange={(event) => handleUpdateChatLayoutEngineSetting(event.target.value)}
                  disabled={chatLayoutEngineSettingSaving}
                >
                  {settingsChatLayoutEngineOptions.map((option) => (
                    <option key={`settings-chat-layout-engine-${option.value}`} value={option.value}>
                      {option.label}
                    </option>
                  ))}
                </select>
              </label>
            </div>
            <p className="meta settings-default-agent-note">
              {defaultAgentSettingSaving
                ? "Saving default agent..."
                : "New chat controls start with this agent by default. You can still override per chat before launch."}
            </p>
            <p className="meta settings-chat-layout-note">
              {chatLayoutEngineSettingSaving
                ? "Saving chats layout engine..."
                : "Classic keeps project groups in a vertical stack. FlexLayout groups chats into dockable tabs."}
            </p>
            <div className="settings-provider-list">
            <article className="card auth-provider-card">
              <div className="project-head">
                <h3>OpenAI</h3>
                <div className="connection-summary">
                  <span className={`connection-pill ${openAiOverallConnected ? "connected" : "disconnected"}`}>
                    {openAiOverallConnected ? "connected" : "not connected"}
                  </span>
                  {openAiAccountConnected ? (
                    <button
                      type="button"
                      className="btn-danger btn-small"
                      onClick={handleDisconnectOpenAiAccount}
                      disabled={openAiAccountDisconnecting || openAiAccountLoginInFlight}
                    >
                      {openAiAccountDisconnecting ? <SpinnerLabel text="Disconnecting..." /> : "Disconnect"}
                    </button>
                  ) : (
                    <button
                      type="button"
                      className="btn-secondary btn-small"
                      onClick={() => {
                        setOpenAiCardExpansionInitialized(true);
                        setOpenAiCardExpanded((expanded) => !expanded);
                      }}
                    >
                      {openAiCardExpanded ? "Hide details" : "Show details"}
                    </button>
                  )}
                </div>
              </div>
              <p className="meta">{openAiConnectionSummary}</p>
              {openAiCardExpanded && openAiCardCanExpand ? (
                <>
                  {openAiAccountLoginInFlight ? (
                    <div className="actions">
                      <button
                        type="button"
                        className="btn-secondary"
                        onClick={handleCancelOpenAiAccountLogin}
                        disabled={openAiAccountCancelling}
                      >
                        {openAiAccountCancelling ? <SpinnerLabel text="Cancelling..." /> : "Cancel account login"}
                      </button>
                    </div>
                  ) : null}
                  {openAiAccountSession?.error ? (
                    <div className="meta build-error">{openAiAccountSession.error}</div>
                  ) : null}

                  <div className="settings-auth-block">
                    <h4>Login with OpenAI account (browser)</h4>
                    <div className="settings-auth-help">
                      <p className="meta settings-auth-help-title">When to use this helper</p>
                      <p className="meta">
                        This account login method exists as a convenience helper because OpenAI&apos;s webhook-based auth callback
                        does not work from Docker containers or remote machines.
                      </p>
                      <p className="meta">
                        Any other Codex auth method, or auth for any other agent, should be done once in the first chat launched
                        with that agent.
                      </p>
                    </div>
                    <ol className="settings-auth-help-list">
                      <li>Click <strong>Connect account</strong>.</li>
                      <li>Open the login URL and complete sign-in and consent.</li>
                      <li>If the browser ends on a localhost error page, copy the full URL and submit it below.</li>
                    </ol>
                    <div className="meta">
                      Account mode: {openAiProviderStatus.accountAuthMode || "none"}
                    </div>
                    <div className="meta">
                      Last account update: {formatTimestamp(openAiProviderStatus.accountUpdatedAt)}
                    </div>
                    <div className="actions">
                      <button
                        type="button"
                        className="btn-primary"
                        onClick={() => handleStartOpenAiAccountLogin("browser_callback")}
                        disabled={
                          openAiAccountStarting ||
                          openAiAccountCancelling ||
                          openAiAccountDisconnecting ||
                          openAiBrowserCallbackInFlight
                        }
                      >
                        {openAiAccountStarting
                          ? <SpinnerLabel text="Starting login..." />
                          : openAiBrowserCallbackInFlight
                            ? "Browser login running"
                            : "Connect account"}
                      </button>
                    </div>
                    {openAiAccountLoginUrl && openAiAccountSessionMethod === "browser_callback" ? (
                      <p className="meta">
                        Login URL:{" "}
                        <a href={openAiAccountLoginUrl} target="_blank" rel="noopener noreferrer">
                          {openAiAccountLoginUrl}
                        </a>
                      </p>
                    ) : null}
                    {openAiAccountSessionMethod === "browser_callback" ? (
                      <div className="stack compact">
                        <form className="stack compact" onSubmit={handleForwardOpenAiAccountCallback}>
                          <div className="settings-auth-input-row">
                            <input
                              value={openAiAccountCallbackInput}
                              onChange={(event) => setOpenAiAccountCallbackInput(event.target.value)}
                              placeholder="Paste callback URL (or query like code=...&state=...)"
                              autoComplete="off"
                              spellCheck={false}
                            />
                          </div>
                          <div className="actions">
                            <button type="submit" className="btn-secondary">
                              Submit callback URL
                            </button>
                          </div>
                        </form>
                      </div>
                    ) : null}
                  </div>

                  <div className="settings-auth-block">
                    <h4>Test Chat Title Generation</h4>
                    <p className="meta">
                      Runs a live title-generation request through the same backend path used by chat titles.
                      Account credentials are used first when connected, then API key.
                    </p>
                    <form className="stack compact" onSubmit={handleTestOpenAiTitleGeneration}>
                      <textarea
                        value={openAiTitleTestPrompt}
                        onChange={(event) => setOpenAiTitleTestPrompt(event.target.value)}
                        placeholder="Enter a sample user prompt (for example: debug websocket reconnect flake in CI)"
                        spellCheck={false}
                      />
                      <div className="actions">
                        <button type="submit" className="btn-secondary" disabled={openAiTitleTestRunning}>
                          {openAiTitleTestRunning ? <SpinnerLabel text="Testing..." /> : "Run title test"}
                        </button>
                      </div>
                    </form>
                    {openAiTitleTestResult ? (
                      <div className="stack compact">
                        <div className="meta">
                          Test result:{" "}
                          <span className={`project-build-state ${openAiTitleTestResult.ok ? "ready" : "failed"}`}>
                            {openAiTitleTestResult.ok ? "success" : "failed"}
                          </span>
                        </div>
                        <div className="meta">
                          Title generation auth mode: {openAiTitleTestResult.connectivity.titleGenerationAuthMode}
                        </div>
                        <div className="meta">
                          API key connected:{" "}
                          {openAiTitleTestResult.connectivity.apiKeyConnected
                            ? `yes (${openAiTitleTestResult.connectivity.apiKeyHint || "saved"})`
                            : "no"}
                        </div>
                        <div className="meta">
                          API key updated: {formatTimestamp(openAiTitleTestResult.connectivity.apiKeyUpdatedAt)}
                        </div>
                        <div className="meta">
                          OpenAI account connected:{" "}
                          {openAiTitleTestResult.connectivity.accountConnected
                            ? `yes (${openAiTitleTestResult.connectivity.accountAuthMode || "chatgpt"})`
                            : "no"}
                        </div>
                        <div className="meta">
                          OpenAI account updated: {formatTimestamp(openAiTitleTestResult.connectivity.accountUpdatedAt)}
                        </div>
                        {openAiTitleTestResult.model ? (
                          <div className="meta">Model: {openAiTitleTestResult.model}</div>
                        ) : null}
                        {openAiTitleTestResult.title ? (
                          <div className="meta">
                            Generated title: <code>{openAiTitleTestResult.title}</code>
                          </div>
                        ) : null}
                        {openAiTitleTestResult.error ? (
                          <div className="meta build-error">{openAiTitleTestResult.error}</div>
                        ) : null}
                        {openAiTitleTestResult.issues.length > 0 ? (
                          <ul className="settings-auth-help-list">
                            {openAiTitleTestResult.issues.map((issue, index) => (
                              <li key={`title-test-issue-${index}`}>{issue}</li>
                            ))}
                          </ul>
                        ) : null}
                      </div>
                    ) : null}
                  </div>
                  <p className="meta settings-auth-note">
                    API keys are stored only on this machine with restricted file permissions and are never returned by the API
                    after save.
                  </p>
                </>
              ) : null}
            </article>
            <article className="card auth-provider-card">
              <div className="project-head">
                <h3>GitHub</h3>
                <div className="connection-summary">
                  <span className={`connection-pill ${githubConnected ? "connected" : "disconnected"}`}>
                    {githubConnected ? "connected" : "not connected"}
                  </span>
                  <button
                    type="button"
                    className="btn-secondary btn-small"
                    onClick={() => setGithubCardExpanded((expanded) => !expanded)}
                  >
                    {githubCardExpanded ? "Hide details" : "Show details"}
                  </button>
                </div>
              </div>
              <p className="meta">{githubConnectionSummary}</p>
              {githubCardExpanded ? (
                <>
                  <p className="meta">
                    GitHub App connections run as the app installation. Personal access token connections run as your GitHub
                    user and also configure git commit identity in each chat container.
                  </p>
                  <div className="settings-auth-block">
                    <h4>Connect as You (Personal Access Token)</h4>
                    <p className="meta">
                      Use this mode for push/commit/PR actions as your user identity. The connected token also sets git
                      global <code>user.name</code> and <code>user.email</code> inside chat containers.
                    </p>
                    <form className="stack compact" onSubmit={handleConnectGithubPersonalAccessToken}>
                      <div className="settings-auth-input-row">
                        <input
                          type={showGithubPersonalAccessTokenDraft ? "text" : "password"}
                          value={githubPersonalAccessTokenDraft}
                          onChange={(event) => setGithubPersonalAccessTokenDraft(event.target.value)}
                          placeholder="github_pat_..."
                          spellCheck={false}
                          autoComplete="off"
                          disabled={githubSaving || githubDisconnecting}
                        />
                        <button
                          type="button"
                          className="btn-secondary btn-small"
                          onClick={() => setShowGithubPersonalAccessTokenDraft((current) => !current)}
                          disabled={githubSaving || githubDisconnecting}
                        >
                          {showGithubPersonalAccessTokenDraft ? "Hide token" : "Show token"}
                        </button>
                      </div>
                      <label htmlFor="github-personal-access-host" className="meta">GitHub host</label>
                      <input
                        id="github-personal-access-host"
                        value={githubPersonalAccessHostDraft}
                        onChange={(event) => setGithubPersonalAccessHostDraft(event.target.value)}
                        placeholder="github.com"
                        spellCheck={false}
                        autoComplete="off"
                        disabled={githubSaving || githubDisconnecting}
                      />
                      <label htmlFor="github-personal-access-owner-scopes" className="meta">
                        Repository owners (optional)
                      </label>
                      <input
                        id="github-personal-access-owner-scopes"
                        value={githubPersonalAccessOwnerScopesDraft}
                        onChange={(event) => setGithubPersonalAccessOwnerScopesDraft(event.target.value)}
                        placeholder="acme-org, agentuser"
                        spellCheck={false}
                        autoComplete="off"
                        disabled={githubSaving || githubDisconnecting}
                      />
                      <p className="meta">
                        Leave owner scopes blank to use this token for any repository owner on the selected host.
                      </p>
                      <div className="actions">
                        <button
                          type="submit"
                          className="btn-primary"
                          disabled={!githubPersonalAccessTokenDraft.trim() || githubSaving || githubDisconnecting}
                        >
                          {githubSaving ? <SpinnerLabel text="Connecting..." /> : "Connect token"}
                        </button>
                        <button
                          type="button"
                          className="btn-secondary"
                          disabled={githubPersonalAccessTokens.length === 0 || githubSaving || githubDisconnecting}
                          onClick={handleDisconnectGithubApp}
                        >
                          {githubDisconnecting ? <SpinnerLabel text="Disconnecting..." /> : "Disconnect all"}
                        </button>
                      </div>
                    </form>
                    <div className="meta">
                      Connected tokens: {githubPersonalAccessTokens.length}
                    </div>
                    {githubPersonalAccessTokens.length > 0 ? (
                      <div className="stack compact">
                        {githubPersonalAccessTokens.map((token) => (
                          <div key={`github-pat-token-${token.tokenId}`} className="settings-auth-token-item">
                            <div className="meta">
                              User: {token.accountLogin || "unknown"}
                              {token.accountName && token.accountName !== token.accountLogin ? ` (${token.accountName})` : ""}
                            </div>
                            <div className="meta">Host: {token.host || "unknown"}</div>
                            <div className="meta">
                              Owner scopes: {token.ownerScopes.length > 0 ? token.ownerScopes.join(", ") : "all owners"}
                            </div>
                            <div className="meta">
                              Git commit identity: {`${token.gitUserName || token.accountName || token.accountLogin || "unknown"} <${
                                token.gitUserEmail || token.accountEmail || "unknown"
                              }>`}
                            </div>
                            <div className="meta">Token hint: {token.tokenHint || "saved"}</div>
                            <div className="meta">Token scopes: {token.tokenScopes || "unavailable"}</div>
                            <div className="meta">Token verified: {formatTimestamp(token.verifiedAt)}</div>
                            <div className="meta">Token connected: {formatTimestamp(token.connectedAt)}</div>
                            <div className="actions">
                              <button
                                type="button"
                                className="btn-secondary btn-small"
                                disabled={
                                  githubSaving ||
                                  githubDisconnecting ||
                                  Boolean(githubPatRemovingTokenId && githubPatRemovingTokenId !== token.tokenId)
                                }
                                onClick={() => {
                                  handleDisconnectGithubPersonalAccessToken(token.tokenId);
                                }}
                              >
                                {githubPatRemovingTokenId === token.tokenId ? <SpinnerLabel text="Removing..." /> : "Remove token"}
                              </button>
                            </div>
                          </div>
                        ))}
                      </div>
                    ) : (
                      <div className="meta">No personal access tokens connected.</div>
                    )}
                  </div>

                  {githubAppConfigured ? (
                    <div className="settings-auth-block">
                      <h4>Connect as App (GitHub App installation)</h4>
                      <div className="actions">
                        {githubProviderStatus.installUrl ? (
                          <a
                            className="btn-secondary"
                            href={githubProviderStatus.installUrl}
                            target="_blank"
                            rel="noreferrer noopener"
                          >
                            Open install page
                          </a>
                        ) : null}
                        <button
                          type="button"
                          className="btn-secondary"
                          disabled={githubInstallationsLoading || githubSaving || githubDisconnecting}
                          onClick={() => {
                            refreshGithubInstallations().catch((err) => {
                              setError(err.message || String(err));
                            });
                          }}
                        >
                          {githubInstallationsLoading ? <SpinnerLabel text="Refreshing..." /> : "Refresh installations"}
                        </button>
                      </div>
                      <form className="stack compact" onSubmit={handleConnectGithubApp}>
                        <label htmlFor="github-installation-select" className="meta">Installation</label>
                        <select
                          id="github-installation-select"
                          value={githubSelectedInstallationId}
                          onChange={(event) => setGithubSelectedInstallationId(event.target.value)}
                          disabled={githubInstallationsLoading || githubSaving || githubDisconnecting}
                        >
                          <option value="">
                            {githubInstallationsLoading ? "Loading installations..." : "Select a GitHub App installation"}
                          </option>
                          {githubInstallations.map((installation) => (
                            <option key={`github-installation-${installation.id}`} value={String(installation.id)}>
                              {`#${installation.id} ${installation.accountLogin ? `- ${installation.accountLogin}` : ""}${
                                installation.accountType ? ` (${installation.accountType})` : ""
                              }`}
                            </option>
                          ))}
                        </select>
                        {selectedGithubInstallation ? (
                          <div className="stack compact">
                            <div className="meta">
                              Selected account: {selectedGithubInstallation.accountLogin || "unknown"}
                              {selectedGithubInstallation.accountType ? ` (${selectedGithubInstallation.accountType})` : ""}
                            </div>
                            <div className="meta">
                              Repository scope: {selectedGithubInstallation.repositorySelection || "unknown"}
                            </div>
                            <div className="meta">
                              Installation updated: {formatTimestamp(selectedGithubInstallation.updatedAt)}
                            </div>
                            {selectedGithubInstallation.suspendedAt ? (
                              <div className="meta build-error">
                                Suspended at: {formatTimestamp(selectedGithubInstallation.suspendedAt)}
                              </div>
                            ) : null}
                          </div>
                        ) : null}
                        <div className="actions">
                          <button
                            type="submit"
                            className="btn-primary"
                            disabled={
                              !githubSelectedInstallationId || githubSaving || githubDisconnecting || githubInstallationsLoading
                            }
                          >
                            {githubSaving ? <SpinnerLabel text="Connecting..." /> : "Connect installation"}
                          </button>
                          <button
                            type="button"
                            className="btn-secondary"
                            disabled={!githubConnected || githubSaving || githubDisconnecting}
                            onClick={handleDisconnectGithubApp}
                          >
                            {githubDisconnecting ? <SpinnerLabel text="Disconnecting..." /> : "Disconnect"}
                          </button>
                        </div>
                      </form>
                      <div className="meta">
                        Connected installation: {githubConnectedWithApp ? `#${githubProviderStatus.installationId}` : "none"}
                        {githubConnectedWithApp && githubProviderStatus.installationAccountLogin
                          ? ` (${githubProviderStatus.installationAccountLogin})`
                          : ""}
                      </div>
                      <div className="meta">Connection updated: {formatTimestamp(githubProviderStatus.updatedAt)}</div>
                      <div className="meta">
                        Repository scope: {githubConnectedWithApp ? (githubProviderStatus.repositorySelection || "unknown") : "unknown"}
                      </div>
                    </div>
                  ) : (
                    <div className="settings-auth-block">
                      <h4>Create and connect GitHub App</h4>
                      <p className="meta">
                        Click <strong>Connect to GitHub</strong>. Agent Hub creates a GitHub App via manifest flow and
                        stores the credentials locally. No manual environment variables are required.
                      </p>
                      <div className="actions">
                        <button
                          type="button"
                          className="btn-primary"
                          onClick={handleStartGithubAppSetup}
                          disabled={githubAppSetupStarting || githubAppSetupInFlight}
                        >
                          {githubAppSetupStarting ? <SpinnerLabel text="Preparing..." /> : "Connect to GitHub"}
                        </button>
                        <button
                          type="button"
                          className="btn-secondary"
                          onClick={() => {
                            refreshGithubAppSetupSession().catch((err) => {
                              setError(err.message || String(err));
                            });
                          }}
                          disabled={githubAppSetupStarting}
                        >
                          Refresh setup status
                        </button>
                      </div>
                      <div className="meta">Setup status: {githubAppSetupStatusLabel}</div>
                      {githubAppSetupSession.startedAt ? (
                        <div className="meta">Setup started: {formatTimestamp(githubAppSetupSession.startedAt)}</div>
                      ) : null}
                      {githubAppSetupSession.expiresAt ? (
                        <div className="meta">Setup expires: {formatTimestamp(githubAppSetupSession.expiresAt)}</div>
                      ) : null}
                      {githubAppSetupSession.appSlug ? (
                        <div className="meta">Configured app: <code>{githubAppSetupSession.appSlug}</code></div>
                      ) : null}
                      {githubAppSetupError ? (
                        <div className="meta build-error">{githubAppSetupError}</div>
                      ) : null}
                      {githubProviderStatus.error ? (
                        <div className="meta build-error">{githubProviderStatus.error}</div>
                      ) : null}
                    </div>
                  )}
                  <p className="meta settings-auth-note">
                    GitHub access credentials are stored only on this machine with restricted file permissions. Use PAT mode
                    for personal identity actions and GitHub App mode for installation-scoped automation.
                  </p>
                </>
              ) : null}
            </article>
            </div>
          </section>
        )}
        </main>
      </div>
      {artifactPreview ? (
        <div className="artifact-preview-overlay" role="presentation" onClick={() => setArtifactPreview(null)}>
          <section
            className="artifact-preview-modal"
            role="dialog"
            aria-modal="true"
            aria-label={`Preview ${artifactPreview.name}`}
            onClick={(event) => event.stopPropagation()}
          >
            <header className="artifact-preview-header">
              <h3 className="artifact-preview-title" title={artifactPreview.name}>{artifactPreview.name}</h3>
              <div className="artifact-preview-actions">
                <a
                  className="icon-button artifact-preview-action"
                  href={artifactPreview.downloadUrl || artifactPreview.previewUrl}
                  download={artifactPreview.name}
                  aria-label={`Download ${artifactPreview.name}`}
                  title={`Download ${artifactPreview.name}`}
                >
                  <DownloadArrowIcon />
                </a>
                <button
                  type="button"
                  className="icon-button artifact-preview-action"
                  aria-label={`Close preview for ${artifactPreview.name}`}
                  title="Close preview"
                  onClick={() => setArtifactPreview(null)}
                >
                  <CloseIcon />
                </button>
              </div>
            </header>
            <div className="artifact-preview-body">
              {artifactPreview.kind === "video" ? (
                <video
                  className="artifact-preview-video"
                  src={artifactPreview.previewUrl}
                  controls
                  autoPlay
                  preload="metadata"
                  playsInline
                />
              ) : (
                <img className="artifact-preview-image" src={artifactPreview.previewUrl} alt={artifactPreview.name} />
              )}
            </div>
          </section>
        </div>
      ) : null}

    </div>
  );
}

export default function App() {
  useEffect(() => {
    const preference = loadThemePreference();
    applyThemePreference(preference);
    applyFaviconForTheme(resolveEffectiveTheme(preference, detectSystemPrefersDark()));
  }, []);

  if (window.location.pathname === "/openai-auth/callback") {
    return <OpenAiAuthCallbackPage />;
  }
  return <HubApp />;
}
