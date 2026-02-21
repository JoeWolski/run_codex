import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { FitAddon } from "@xterm/addon-fit";
import { Terminal } from "@xterm/xterm";

const THEME_STORAGE_KEY = "agent_hub_theme";
const START_MODEL_OPTIONS = ["default", "gpt-5.3-codex", "gpt-5.3-codex-spark"];
const REASONING_MODE_OPTIONS = ["default", "minimal", "low", "medium", "high", "xhigh"];

function normalizeStartModel(value) {
  const normalized = String(value || "").trim();
  if (START_MODEL_OPTIONS.includes(normalized)) {
    return normalized;
  }
  return "default";
}

function normalizeReasoningMode(value) {
  const normalized = String(value || "").trim().toLowerCase();
  if (REASONING_MODE_OPTIONS.includes(normalized)) {
    return normalized;
  }
  return "default";
}

function buildChatStartArgs(model, reasoningMode) {
  const args = [];
  const resolvedModel = normalizeStartModel(model);
  const resolvedReasoningMode = normalizeReasoningMode(reasoningMode);
  if (resolvedModel !== "default") {
    args.push("--model", resolvedModel);
  }
  if (resolvedReasoningMode !== "default") {
    args.push("-c", `model_reasoning_effort="${resolvedReasoningMode}"`);
  }
  return args;
}

function normalizeThemePreference(value) {
  const normalized = String(value || "").toLowerCase();
  if (normalized === "light" || normalized === "dark" || normalized === "system") {
    return normalized;
  }
  return "system";
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

function emptyVolume() {
  return { host: "", container: "", mode: "rw" };
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
  return "Docker image tag (e.g. nvcr.io/nvidia/isaac-lab:2.3.2)";
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

function setupCommandCount(text) {
  return String(text || "")
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean).length;
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

function terminalSocketUrl(chatId) {
  const protocol = window.location.protocol === "https:" ? "wss" : "ws";
  return `${protocol}://${window.location.host}/api/chats/${chatId}/terminal`;
}

function hubEventsSocketUrl() {
  const protocol = window.location.protocol === "https:" ? "wss" : "ws";
  return `${protocol}://${window.location.host}/api/events`;
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

function ChatTerminal({ chatId, running }) {
  const shellRef = useRef(null);
  const hostRef = useRef(null);
  const [status, setStatus] = useState(running ? "connecting" : "offline");

  useEffect(() => {
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
      theme: {
        background: "#0b1018",
        foreground: "#e7edf7",
        cursor: "#10a37f"
      }
    });
    const fitAddon = new FitAddon();
    terminal.loadAddon(fitAddon);
    terminal.open(hostRef.current);
    fitAddon.fit();
    terminal.focus();

    const ws = new WebSocket(terminalSocketUrl(chatId));
    setStatus("connecting");

    const sendInput = (text) => {
      const payload = String(text || "");
      if (!payload) {
        return false;
      }
      if (ws.readyState !== WebSocket.OPEN) {
        return false;
      }
      ws.send(JSON.stringify({ type: "input", data: payload }));
      return true;
    };

    const sendPasteText = (text) => {
      const normalized = String(text || "").replace(/\r\n/g, "\n");
      if (!normalized) {
        return false;
      }
      return sendInput(normalized);
    };

    const sendResize = () => {
      if (ws.readyState !== WebSocket.OPEN) {
        return;
      }
      const cols = Math.max(1, terminal.cols || 1);
      const rows = Math.max(1, terminal.rows || 1);
      ws.send(JSON.stringify({ type: "resize", cols, rows }));
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
        if (ws.readyState === WebSocket.OPEN) {
          ws.send(JSON.stringify({ type: "submit" }));
        }
      }
    });

    const onOpen = () => {
      setStatus("connected");
      fitAddon.fit();
      sendResize();
      terminal.focus();
    };
    const onMessage = (event) => {
      if (typeof event.data === "string") {
        terminal.write(event.data);
      }
    };
    const onClose = () => {
      setStatus("closed");
    };
    const onError = () => {
      setStatus("error");
    };
    const onResize = () => {
      fitAddon.fit();
      sendResize();
    };

    const onShellPointerDown = () => {
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
    if (shellElement) {
      shellElement.addEventListener("pointerdown", onShellPointerDown);
      shellElement.addEventListener("paste", onShellPaste);
    }

    let resizeObserver;
    if (typeof ResizeObserver !== "undefined" && shellElement) {
      resizeObserver = new ResizeObserver(() => {
        fitAddon.fit();
        sendResize();
      });
      resizeObserver.observe(shellElement);
    }

    ws.addEventListener("open", onOpen);
    ws.addEventListener("message", onMessage);
    ws.addEventListener("close", onClose);
    ws.addEventListener("error", onError);
    window.addEventListener("resize", onResize);

    return () => {
      if (resizeObserver) {
        resizeObserver.disconnect();
      }
      if (shellElement) {
        shellElement.removeEventListener("pointerdown", onShellPointerDown);
        shellElement.removeEventListener("paste", onShellPaste);
      }
      window.removeEventListener("resize", onResize);
      ws.removeEventListener("open", onOpen);
      ws.removeEventListener("message", onMessage);
      ws.removeEventListener("close", onClose);
      ws.removeEventListener("error", onError);
      inputDisposable.dispose();
      keyDisposable.dispose();
      if (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING) {
        ws.close();
      }
      terminal.dispose();
    };
  }, [chatId, running]);

  return (
    <div className="terminal-shell chat-terminal-shell" ref={shellRef}>
      <div className="terminal-toolbar">
        <span className={`terminal-badge ${status}`}>{status}</span>
      </div>
      <div className="terminal-view" ref={hostRef} />
    </div>
  );
}

function ProjectBuildTerminal({ text }) {
  const hostRef = useRef(null);
  const terminalRef = useRef(null);
  const fitRef = useRef(null);

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
      theme: {
        background: "#0b1018",
        foreground: "#e7edf7"
      }
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

  return (
    <div className="terminal-shell project-build-shell">
      <div className="terminal-toolbar">
        <span className="terminal-title">Image build output</span>
      </div>
      <div className="terminal-view project-build-view" ref={hostRef} />
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
          <select value={row.mode} onChange={(event) => updateRow(index, { mode: event.target.value })}>
            <option value="rw">Read-write</option>
            <option value="ro">Read-only</option>
          </select>
          <button type="button" className="btn-secondary btn-small" onClick={() => removeRow(index)}>
            Remove
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
          <button type="button" className="btn-secondary btn-small" onClick={() => removeRow(index)}>
            Remove
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

function buildProxiedOpenAiLoginUrl(loginUrl) {
  const raw = String(loginUrl || "").trim();
  if (!raw) {
    return "";
  }
  try {
    const parsed = new URL(raw);
    if (!parsed.searchParams.has("redirect_uri")) {
      return raw;
    }
    parsed.searchParams.set("redirect_uri", `${window.location.origin}/openai-auth/callback`);
    return parsed.toString();
  } catch {
    return raw;
  }
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
  const [hubState, setHubState] = useState({ projects: [], chats: [] });
  const [error, setError] = useState("");
  const [createForm, setCreateForm] = useState(() => emptyCreateForm());
  const [projectDrafts, setProjectDrafts] = useState({});
  const [editingProjects, setEditingProjects] = useState({});
  const [projectBuildLogs, setProjectBuildLogs] = useState({});
  const [projectStaticLogs, setProjectStaticLogs] = useState({});
  const [openBuildLogs, setOpenBuildLogs] = useState({});
  const [activeTab, setActiveTab] = useState("projects");
  const [openChats, setOpenChats] = useState({});
  const [openChatDetails, setOpenChatDetails] = useState({});
  const [collapsedProjectChats, setCollapsedProjectChats] = useState({});
  const [chatStartSettingsByProject, setChatStartSettingsByProject] = useState({});
  const [fullscreenChatId, setFullscreenChatId] = useState("");
  const createChatQueueRef = useRef(new Map());
  const createChatActiveProjectsRef = useRef(new Set());
  const [pendingSessions, setPendingSessions] = useState([]);
  const [pendingProjectBuilds, setPendingProjectBuilds] = useState({});
  const [pendingChatStarts, setPendingChatStarts] = useState({});
  const [themePreference, setThemePreference] = useState(() => loadThemePreference());
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
  const stateRefreshInFlightRef = useRef(false);
  const stateRefreshQueuedRef = useRef(false);
  const authRefreshInFlightRef = useRef(false);
  const authRefreshQueuedRef = useRef(false);

  const applyStatePayload = useCallback((payload) => {
    setHubState(payload);
    const serverChatMap = new Map((payload.chats || []).map((chat) => [chat.id, chat]));
    setPendingSessions((prev) =>
      prev.flatMap((session) => {
        if (!session.server_chat_id) {
          return [session];
        }
        const onServer = serverChatMap.has(session.server_chat_id);
        const seenOnServer = Boolean(session.seen_on_server || onServer);
        if (seenOnServer && !onServer) {
          return [];
        }
        if (seenOnServer === Boolean(session.seen_on_server)) {
          return [session];
        }
        return [{ ...session, seen_on_server: seenOnServer }];
      })
    );
    setPendingChatStarts((prev) => {
      const next = {};
      for (const [chatId, pending] of Object.entries(prev)) {
        if (!pending) {
          continue;
        }
        const serverChat = serverChatMap.get(chatId);
        if (serverChat && !serverChat.is_running) {
          next[chatId] = true;
        }
      }
      return next;
    });
    setPendingProjectBuilds((prev) => {
      const next = {};
      for (const project of payload.projects || []) {
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
    const provider = authPayload?.providers?.openai;
    setOpenAiProviderStatus(normalizeOpenAiProviderStatus(provider));
    setOpenAiAccountSession(normalizeOpenAiAccountSession(sessionPayload?.session));
    setOpenAiAuthLoaded(true);
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
      const knownServerIds = new Set(session.known_server_chat_ids || []);
      const matchedServerChat = serverChats.find(
        (chat) =>
          !mappedServerIds.has(chat.id) &&
          String(chat.project_id || "") === String(session.project_id || "") &&
          !knownServerIds.has(chat.id)
      );
      if (matchedServerChat) {
        mappedServerIds.add(matchedServerChat.id);
        merged.push({
          ...matchedServerChat,
          id: session.ui_id,
          server_chat_id: matchedServerChat.id,
          is_pending_start: true
        });
        continue;
      }
      merged.push({
        id: session.ui_id,
        server_chat_id: serverId,
        name: "new-chat",
        display_name: "New chat",
        display_subtitle: "Creating workspace and starting worker…",
        status: "starting",
        is_running: false,
        is_pending_start: true,
        project_id: session.project_id,
        project_name: session.project_name || "Unknown",
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
  }, [hubState.chats, pendingSessions]);

  useEffect(() => {
    let cancelled = false;
    Promise.all([refreshState(), refreshAuthSettings()])
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
  }, [refreshState, refreshAuthSettings]);

  useEffect(() => {
    let stopped = false;
    let reconnectTimer = null;
    let ws = null;

    const applyAuthPayload = (authPayload) => {
      const provider = authPayload?.providers?.openai;
      setOpenAiProviderStatus(normalizeOpenAiProviderStatus(provider));
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
  }, [applyStatePayload, queueAuthRefresh, queueStateRefresh]);

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
      for (const chat of visibleChats) {
        next[chat.id] = prev[chat.id] ?? true;
      }
      return next;
    });
  }, [visibleChats]);

  useEffect(() => {
    setOpenChatDetails((prev) => {
      const next = {};
      for (const chat of visibleChats) {
        next[chat.id] = prev[chat.id] ?? false;
      }
      return next;
    });
  }, [visibleChats]);

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
        const current = prev[project.id] || {};
        next[project.id] = {
          model: normalizeStartModel(current.model),
          reasoning: normalizeReasoningMode(current.reasoning)
        };
      }
      return next;
    });
  }, [hubState.projects]);

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
    setChatStartSettingsByProject((prev) => {
      const current = prev[projectId] || { model: "default", reasoning: "default" };
      const nextModel = patch.model === undefined ? current.model : normalizeStartModel(patch.model);
      const nextReasoning = patch.reasoning === undefined ? current.reasoning : normalizeReasoningMode(patch.reasoning);
      return {
        ...prev,
        [projectId]: {
          model: nextModel,
          reasoning: nextReasoning
        }
      };
    });
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

  const processCreateChatQueue = useCallback(async (projectId) => {
    if (createChatActiveProjectsRef.current.has(projectId)) {
      return;
    }
    createChatActiveProjectsRef.current.add(projectId);

    try {
      while (true) {
        const queue = createChatQueueRef.current.get(projectId) || [];
        const nextJob = queue.shift();
        if (!nextJob) {
          createChatQueueRef.current.delete(projectId);
          break;
        }
        createChatQueueRef.current.set(projectId, queue);

        const uiId = nextJob.uiId;
        const agentArgs = Array.isArray(nextJob.agentArgs)
          ? nextJob.agentArgs.map((arg) => String(arg)).filter((arg) => arg.trim())
          : [];
        try {
          const response = await fetchJson(`/api/projects/${projectId}/chats/start`, {
            method: "POST",
            body: JSON.stringify({ agent_args: agentArgs })
          });
          const chatId = response?.chat?.id;
          if (!chatId) {
            removeOptimisticChatRow(uiId);
            continue;
          }
          setPendingSessions((prev) =>
            prev.map((session) =>
              session.ui_id === uiId ? { ...session, server_chat_id: chatId } : session
            )
          );
          setPendingChatStarts((prev) => ({ ...prev, [chatId]: true }));
          setError("");
          refreshState().catch(() => {});
        } catch (err) {
          removeOptimisticChatRow(uiId);
          setError(err.message || String(err));
          refreshState().catch(() => {});
        }
      }
    } finally {
      createChatActiveProjectsRef.current.delete(projectId);
      if ((createChatQueueRef.current.get(projectId) || []).length > 0) {
        processCreateChatQueue(projectId).catch(() => {});
      }
    }
  }, [refreshState, removeOptimisticChatRow]);

  async function handleCreateProject(event) {
    event.preventDefault();
    try {
      const mounts = buildMountPayload(createForm.defaultVolumes);
      const envVars = buildEnvPayload(createForm.defaultEnvVars);
      const payload = {
        repo_url: createForm.repoUrl.trim(),
        name: createForm.name,
        default_branch: createForm.defaultBranch,
        base_image_mode: createForm.baseImageMode,
        base_image_value: createForm.baseImageValue,
        setup_script: createForm.setupScript,
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

  async function handleSaveProjectSettings(projectId) {
    setEditingProjects((prev) => ({ ...prev, [projectId]: false }));
    markProjectBuilding(projectId);
    try {
      await persistProjectSettings(projectId);
      setPendingProjectBuilds((prev) => {
        const next = { ...prev };
        delete next[projectId];
        return next;
      });
      setError("");
      refreshState().catch(() => {});
    } catch (err) {
      setEditingProjects((prev) => ({ ...prev, [projectId]: true }));
      setPendingProjectBuilds((prev) => {
        const next = { ...prev };
        delete next[projectId];
        return next;
      });
      setError(err.message || String(err));
      refreshState().catch(() => {});
    }
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
    const uiId = `pending-${Date.now()}-${Math.random().toString(16).slice(2, 8)}`;
    const project = projectsById.get(projectId);
    const selectedStartSettings = startSettings || chatStartSettingsByProject[projectId] || {
      model: "default",
      reasoning: "default"
    };
    const agentArgs = buildChatStartArgs(selectedStartSettings.model, selectedStartSettings.reasoning);
    const knownServerChatIds = (hubState.chats || [])
      .filter((chat) => String(chat.project_id || "") === String(projectId))
      .map((chat) => chat.id);
    setPendingSessions((prev) => [{
      ui_id: uiId,
      project_id: projectId,
      project_name: project?.name || "Unknown",
      server_chat_id: "",
      known_server_chat_ids: knownServerChatIds,
      seen_on_server: false
    }, ...prev]);
    setActiveTab("chats");
    setOpenChats((prev) => ({ ...prev, [uiId]: true }));
    setOpenChatDetails((prev) => ({ ...prev, [uiId]: false }));
    setCollapsedProjectChats((prev) => ({ ...prev, [projectId]: false }));

    const queue = createChatQueueRef.current.get(projectId) || [];
    queue.push({ uiId, agentArgs });
    createChatQueueRef.current.set(projectId, queue);
    processCreateChatQueue(projectId).catch(() => {});
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
    createChatQueueRef.current.delete(projectId);
    createChatActiveProjectsRef.current.delete(projectId);
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
    setPendingChatStarts((prev) => ({ ...prev, [chatId]: true }));
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
    for (const project of hubState.projects) {
      byProject.set(project.id, []);
    }
    const orphanChats = [];
    for (const chat of visibleChats) {
      if (!byProject.has(chat.project_id)) {
        orphanChats.push(chat);
        continue;
      }
      byProject.get(chat.project_id).push(chat);
    }
    return { byProject, orphanChats };
  }, [hubState.projects, visibleChats]);

  const openAiAccountProxyLoginUrl = useMemo(
    () => buildProxiedOpenAiLoginUrl(openAiAccountSession?.loginUrl),
    [openAiAccountSession?.loginUrl]
  );
  const openAiAccountDirectLoginUrl = String(openAiAccountSession?.loginUrl || "").trim();
  const openAiAccountSessionMethod = String(openAiAccountSession?.method || "");
  const openAiAccountLoginInFlight = Boolean(
    openAiAccountSession &&
      ["starting", "running", "waiting_for_browser", "waiting_for_device_code", "callback_received"].includes(
        String(openAiAccountSession.status || "")
      )
  );
  const openAiBrowserCallbackInFlight = openAiAccountLoginInFlight && openAiAccountSessionMethod === "browser_callback";
  const openAiDeviceAuthInFlight = openAiAccountLoginInFlight && openAiAccountSessionMethod === "device_auth";
  const openAiOverallConnected = openAiProviderStatus.accountConnected || openAiProviderStatus.connected;
  const openAiConnectionSummary = openAiProviderStatus.accountConnected && openAiProviderStatus.connected
    ? "Connected with OpenAI account and API key."
    : openAiProviderStatus.accountConnected
      ? "Connected with OpenAI account."
      : openAiProviderStatus.connected
        ? "Connected with API key."
        : "Not connected yet. Expand this section and choose one login method.";

  useEffect(() => {
    if (!openAiAuthLoaded || openAiCardExpansionInitialized) {
      return;
    }
    setOpenAiCardExpanded(!openAiOverallConnected);
    setOpenAiCardExpansionInitialized(true);
  }, [openAiAuthLoaded, openAiCardExpansionInitialized, openAiOverallConnected]);

  useEffect(() => {
    if (openAiAccountLoginInFlight) {
      setOpenAiCardExpanded(true);
    }
  }, [openAiAccountLoginInFlight]);

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
    if (!fullscreenChatId) {
      return;
    }
    const exists = visibleChats.some((chat) => chat.id === fullscreenChatId);
    if (!exists) {
      setFullscreenChatId("");
    }
  }, [visibleChats, fullscreenChatId]);

  useEffect(() => {
    if (activeTab !== "chats" && fullscreenChatId) {
      setFullscreenChatId("");
    }
  }, [activeTab, fullscreenChatId]);

  useEffect(() => {
    if (!fullscreenChatId) {
      return undefined;
    }
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const onKeyDown = (event) => {
      if (event.key === "Escape") {
        setFullscreenChatId("");
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => {
      document.body.style.overflow = previousOverflow;
      window.removeEventListener("keydown", onKeyDown);
    };
  }, [fullscreenChatId]);

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

  function renderChatCard(chat) {
    const resolvedChatId = resolveServerChatId(chat);
    const chatHasServer = hasServerChat(chat);
    const isRunning = Boolean(chat.is_running);
    const isStarting = Boolean(
      pendingChatStarts[resolvedChatId] || chat.is_pending_start || String(chat.status || "") === "starting"
    );
    const titleStatus = String(chat.title_status || "idle").toLowerCase();
    const volumeCount = (chat.ro_mounts || []).length + (chat.rw_mounts || []).length;
    const envCount = (chat.env_vars || []).length;
    const rowOpen = fullscreenChatId === chat.id ? true : (openChats[chat.id] ?? true);
    const detailsOpen = openChatDetails[chat.id] ?? false;
    const isFullscreenChat = fullscreenChatId === chat.id;
    const containerClassName = ["card", isFullscreenChat ? "chat-card-popped" : ""].filter(Boolean).join(" ");
    const titleText = chat.display_name || chat.name;
    const titleStateLabel = titleStatus === "error" ? "Title error" : "";
    const rowSubtitle = isStarting
      ? "Starting chat and preparing terminal..."
      : chat.display_subtitle || "No recent assistant summary yet.";

    return (
      <article className={containerClassName} key={chat.id}>
        <div
          className="chat-card-header"
          role="button"
          tabIndex={0}
          onClick={() => toggleChatRow(chat)}
          onKeyDown={(event) => {
            if (event.key === "Enter" || event.key === " ") {
              event.preventDefault();
              toggleChatRow(chat);
            }
          }}
        >
          <div className="chat-card-header-main">
            <div className="chat-card-title-row">
              <h3 className="chat-card-title">{titleText}</h3>
              {titleStateLabel ? (
                <span className={`chat-title-state ${titleStatus}`}>{titleStateLabel}</span>
              ) : null}
            </div>
            {!rowOpen ? (
              <div className="meta chat-summary">{rowSubtitle}</div>
            ) : null}
          </div>
          <div className="chat-card-header-actions">
            <button
              type="button"
              className="icon-button chat-header-icon chat-header-popout"
              title={isFullscreenChat ? "Minimize" : "Pop out"}
              aria-label={isFullscreenChat ? `Minimize ${chat.display_name || chat.name}` : `Pop out ${chat.display_name || chat.name}`}
              onClick={(event) => {
                event.stopPropagation();
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
              onClick={(event) => {
                event.stopPropagation();
                setOpenChatDetails((prev) => ({ ...prev, [chat.id]: !(prev[chat.id] ?? false) }));
              }}
            >
              <EllipsisIcon />
            </button>
            <button
              type="button"
              className="icon-button chat-header-icon chat-header-delete"
              title={`Delete ${titleText}`}
              aria-label={`Delete ${titleText}`}
              onClick={(event) => {
                event.stopPropagation();
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
          </div>
        </div>

        {rowOpen ? (
          <div className="stack compact chat-card-body">
            {detailsOpen ? (
              <section className="chat-details">
                <div className="meta">
                  Status:{" "}
                  <span className={`status ${isRunning ? "running" : isStarting ? "starting" : "stopped"}`}>
                    {isRunning ? chat.status : isStarting ? "starting" : chat.status}
                  </span>
                </div>
                <div className="meta">Title status: {titleStatus || "idle"}</div>
                {chat.title_error ? <div className="meta build-error">Title generation error: {chat.title_error}</div> : null}
                <div className="meta">Chat ID: {resolvedChatId || "starting..."}</div>
                <div className="meta">Workspace: {chat.workspace}</div>
                <div className="meta">Container folder: {chat.container_workspace || "not started yet"}</div>
                {chat.setup_snapshot_image ? (
                  <div className="meta">Setup snapshot image: {chat.setup_snapshot_image}</div>
                ) : null}
                <div className="meta">Volumes: {volumeCount} | Env vars: {envCount}</div>
              </section>
            ) : null}

            {isStarting && !isRunning ? (
              <div className="terminal-shell chat-terminal-shell chat-terminal-placeholder">
                <div className="terminal-overlay">
                  <span className="inline-spinner" aria-hidden="true" />
                </div>
              </div>
            ) : isRunning ? (
              <ChatTerminal chatId={resolvedChatId} running={isRunning} />
            ) : chatHasServer ? (
              <div className="stack compact">
                <div className="meta chat-terminal-stopped">Chat is stopped. Start it to reconnect the terminal.</div>
                <div className="actions chat-actions">
                  <button
                    type="button"
                    className="btn-primary chat-primary-action"
                    onClick={() => handleStartChat(resolvedChatId)}
                  >
                    Start chat
                  </button>
                </div>
              </div>
            ) : null}
          </div>
        ) : null}
      </article>
    );
  }

  return (
    <div className="app-root">
      <header className="app-header">
        <div className="header-row">
          <div className="brand-block">
            <h1>Agent Hub</h1>
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

      <div className="content-shell">
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

                  <button type="submit" className="btn-primary">
                    Add project
                  </button>
                </form>
              </section>

              <section className="projects-list">
                <h3 className="section-title">Existing projects</h3>
                <div className="stack">
              {hubState.projects.length === 0 ? <div className="empty">No projects yet.</div> : null}
              {hubState.projects.map((project) => {
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
                const canShowStoredLogButton = buildStatus === "ready" || buildStatus === "failed";
                const isEditing = Boolean(editingProjects[project.id]);
                const isStoredLogOpen = Boolean(openBuildLogs[project.id]);
                const storedLogText = projectStaticLogs[project.id];

                return (
                  <article className="card project-card" key={project.id}>
                    <div className="project-head">
                      <h3>{project.name}</h3>
                      {!isEditing ? (
                        <button
                          type="button"
                          className="icon-button"
                          title="Edit project settings"
                          aria-label={`Edit ${project.name}`}
                          onClick={() => handleEditProject(project)}
                        >
                          ✎
                        </button>
                      ) : null}
                    </div>
                    <div className="meta">ID: {project.id}</div>
                    <div className="meta">Repo: {project.repo_url}</div>
                    <div className="meta">Branch: {project.default_branch || "master"}</div>
                    <div className="meta">Status: <span className={`project-build-state ${statusInfo.key}`}>{statusInfo.label}</span></div>
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
                      <button
                        type="button"
                        className="btn-primary"
                        disabled={!canStartChat && isBuilding}
                        onClick={() => (canStartChat ? handleCreateChat(project.id) : handleBuildProject(project.id))}
                      >
                        {canStartChat
                          ? "New chat"
                          : isBuilding
                            ? <SpinnerLabel text="Building image..." />
                            : "Build"}
                      </button>
                      {isBuilding ? (
                        <ProjectBuildTerminal
                          text={projectBuildLogs[project.id] || "Preparing project image...\r\n"}
                        />
                      ) : null}
                      {canShowStoredLogButton ? (
                        <div className="actions">
                          <button
                            type="button"
                            className="btn-secondary btn-small build-log-toggle"
                            onClick={() => handleToggleStoredBuildLog(project.id)}
                          >
                            {isStoredLogOpen ? "Hide stored build log" : "Show stored build log"}
                          </button>
                        </div>
                      ) : null}
                      {canShowStoredLogButton && isStoredLogOpen ? (
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

                          <div className="actions">
                            <button
                              type="button"
                              className="btn-primary"
                              onClick={() => handleSaveProjectSettings(project.id)}
                            >
                              Save project settings
                            </button>
                            <button
                              type="button"
                              className="btn-secondary"
                              onClick={() => handleCancelProjectEdit(project)}
                            >
                              Cancel
                            </button>
                          </div>
                        </>
                      ) : null}

                      <div className="actions">
                        <button type="button" className="btn-danger" onClick={() => handleDeleteProject(project.id)}>
                          Delete project
                        </button>
                      </div>
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
            <div className="stack chat-groups">
              {hubState.projects.length === 0 ? <div className="empty">No projects yet.</div> : null}
              {hubState.projects.map((project) => {
                const projectChats = chatsByProject.byProject.get(project.id) || [];
                const buildStatus = String(project.build_status || "pending");
                const canStartChat = buildStatus === "ready";
                const isBuilding = buildStatus === "building" || Boolean(pendingProjectBuilds[project.id]);
                const projectRowsCollapsed = Boolean(collapsedProjectChats[project.id]);
                const projectStartSettings = chatStartSettingsByProject[project.id] || {
                  model: "default",
                  reasoning: "default"
                };
                return (
                  <article className="card project-chat-group" key={`group-${project.id}`}>
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
                              aria-label={`Start model for ${project.name}`}
                              value={projectStartSettings.model}
                              onClick={(event) => event.stopPropagation()}
                              onKeyDown={(event) => event.stopPropagation()}
                              onChange={(event) => {
                                event.stopPropagation();
                                updateProjectChatStartSettings(project.id, { model: event.target.value });
                              }}
                            >
                              <option value="default">default</option>
                              <option value="gpt-5.3-codex">gpt-5.3-codex</option>
                              <option value="gpt-5.3-codex-spark">gpt-5.3-codex-spark</option>
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
                              {REASONING_MODE_OPTIONS.map((reasoningMode) => (
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
                          disabled={!canStartChat && isBuilding}
                          onClick={(event) => {
                            event.stopPropagation();
                            if (canStartChat) {
                              handleCreateChat(project.id, projectStartSettings);
                            } else {
                              handleBuildProject(project.id);
                            }
                          }}
                        >
                          {canStartChat
                            ? "New chat"
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
              })}
              {chatsByProject.orphanChats.length > 0 ? (
                <article className="card project-chat-group" key="group-orphan">
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
              ) : null}
            </div>
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
            </div>
            <article className="card auth-provider-card">
              <div className="project-head">
                <h3>OpenAI</h3>
                <div className="connection-summary">
                  <span className={`connection-pill ${openAiOverallConnected ? "connected" : "disconnected"}`}>
                    {openAiOverallConnected ? "connected" : "not connected"}
                  </span>
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
                </div>
              </div>
              <p className="meta">{openAiConnectionSummary}</p>
              {openAiCardExpanded ? (
                <>
                  <p className="meta">
                    Connect with either your OpenAI account or an API key. New chat instances and project setup runs will use
                    whichever credential is available.
                  </p>
                  <div className="actions">
                    <button
                      type="button"
                      className="btn-secondary"
                      onClick={handleCancelOpenAiAccountLogin}
                      disabled={!openAiAccountLoginInFlight || openAiAccountCancelling}
                    >
                      {openAiAccountCancelling ? <SpinnerLabel text="Cancelling..." /> : "Cancel account login"}
                    </button>
                    <button
                      type="button"
                      className="btn-secondary"
                      onClick={handleDisconnectOpenAiAccount}
                      disabled={
                        !openAiProviderStatus.accountConnected ||
                        openAiAccountDisconnecting ||
                        openAiAccountLoginInFlight
                      }
                    >
                      {openAiAccountDisconnecting ? <SpinnerLabel text="Disconnecting..." /> : "Disconnect account"}
                    </button>
                  </div>
                  {openAiAccountSession ? (
                    <div className="stack compact">
                      <div className="meta">
                        Account login status: {openAiAccountSession.status || "starting"}
                      </div>
                      {openAiAccountSession.error ? (
                        <div className="meta build-error">{openAiAccountSession.error}</div>
                      ) : null}
                      {openAiAccountSession.logTail ? (
                        <pre className="log-box settings-auth-log">{openAiAccountSession.logTail}</pre>
                      ) : null}
                    </div>
                  ) : null}

                  <div className="settings-auth-block">
                    <h4>Login with OpenAI account (browser)</h4>
                    <ol className="settings-auth-help-list">
                      <li>Click <strong>Start browser login</strong>.</li>
                      <li>Click <strong>Open auth page</strong> and complete sign-in and consent.</li>
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
                            : "Start browser login"}
                      </button>
                      {openAiAccountDirectLoginUrl && openAiAccountSessionMethod === "browser_callback" ? (
                        <button
                          type="button"
                          className="btn-secondary"
                          onClick={() => window.open(openAiAccountProxyLoginUrl, "_blank", "noopener,noreferrer")}
                        >
                          Open auth page
                        </button>
                      ) : null}
                      {openAiAccountDirectLoginUrl &&
                      openAiAccountSessionMethod === "browser_callback" &&
                      openAiAccountProxyLoginUrl !== openAiAccountDirectLoginUrl ? (
                        <button
                          type="button"
                          className="btn-secondary"
                          onClick={() => window.open(openAiAccountDirectLoginUrl, "_blank", "noopener,noreferrer")}
                        >
                          Open direct localhost URL
                        </button>
                      ) : null}
                    </div>
                    {openAiAccountSessionMethod === "browser_callback" ? (
                      <div className="stack compact">
                        <p className="meta">
                          Local callback URL:{" "}
                          <code>{openAiAccountSession?.localCallbackUrl || "http://localhost:1455/auth/callback"}</code>
                        </p>
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
                    <h4>Login with OpenAI account (device code)</h4>
                    <ol className="settings-auth-help-list">
                      <li>Click <strong>Start device code login</strong>.</li>
                      <li>Click <strong>Open device auth page</strong>.</li>
                      <li>Enter the one-time code shown below, then approve access.</li>
                    </ol>
                    <div className="actions">
                      <button
                        type="button"
                        className="btn-primary"
                        onClick={() => handleStartOpenAiAccountLogin("device_auth")}
                        disabled={
                          openAiAccountStarting ||
                          openAiAccountCancelling ||
                          openAiAccountDisconnecting ||
                          openAiDeviceAuthInFlight
                        }
                      >
                        {openAiAccountStarting
                          ? <SpinnerLabel text="Starting login..." />
                          : openAiDeviceAuthInFlight
                            ? "Device login running"
                            : "Start device code login"}
                      </button>
                      {openAiAccountDirectLoginUrl && openAiAccountSessionMethod === "device_auth" ? (
                        <button
                          type="button"
                          className="btn-secondary"
                          onClick={() => window.open(openAiAccountDirectLoginUrl, "_blank", "noopener,noreferrer")}
                        >
                          Open device auth page
                        </button>
                      ) : null}
                    </div>
                    {openAiAccountSessionMethod === "device_auth" && openAiAccountSession?.deviceCode ? (
                      <p className="meta">
                        Enter one-time code: <code>{openAiAccountSession.deviceCode}</code>
                      </p>
                    ) : null}
                  </div>

                  <div className="settings-auth-block">
                    <h4>Login with API key</h4>
                    <div className="settings-auth-help">
                      <p className="meta settings-auth-help-title">How to get an OpenAI API key</p>
                      <ol className="settings-auth-help-list">
                        <li>
                          Open{" "}
                          <a
                            href="https://platform.openai.com/api-keys"
                            target="_blank"
                            rel="noopener noreferrer"
                          >
                            https://platform.openai.com/api-keys
                          </a>
                          {" "}and sign in.
                        </li>
                        <li>Create a new secret key.</li>
                        <li>Copy the key immediately (it may only be shown once).</li>
                        <li>Paste it here and keep &quot;Verify with OpenAI before saving&quot; enabled.</li>
                      </ol>
                    </div>
                    <div className="meta">Saved key: {openAiProviderStatus.keyHint || "none"}</div>
                    <div className="meta">Last updated: {formatTimestamp(openAiProviderStatus.updatedAt)}</div>

                    <form className="stack compact" onSubmit={handleConnectOpenAi}>
                      <div className="settings-auth-input-row">
                        <input
                          type={showOpenAiDraftKey ? "text" : "password"}
                          value={openAiDraftKey}
                          onChange={(event) => setOpenAiDraftKey(event.target.value)}
                          placeholder="Paste OpenAI API key (sk-...)"
                          autoComplete="off"
                          spellCheck={false}
                        />
                        <button
                          type="button"
                          className="btn-secondary"
                          onClick={() => setShowOpenAiDraftKey((prev) => !prev)}
                        >
                          {showOpenAiDraftKey ? "Hide" : "Show"}
                        </button>
                      </div>
                      <label className="settings-checkbox-row">
                        <input
                          type="checkbox"
                          checked={verifyOpenAiOnSave}
                          onChange={(event) => setVerifyOpenAiOnSave(event.target.checked)}
                        />
                        <span>Verify with OpenAI before saving</span>
                      </label>
                      <div className="actions">
                        <button
                          type="submit"
                          className="btn-primary"
                          disabled={openAiSaving || openAiDisconnecting}
                        >
                          {openAiSaving
                            ? <SpinnerLabel text={verifyOpenAiOnSave ? "Verifying..." : "Saving..."} />
                            : "Connect API key"}
                        </button>
                        <button
                          type="button"
                          className="btn-secondary"
                          disabled={!openAiProviderStatus.connected || openAiSaving || openAiDisconnecting}
                          onClick={handleDisconnectOpenAi}
                        >
                          {openAiDisconnecting ? <SpinnerLabel text="Disconnecting..." /> : "Disconnect API key"}
                        </button>
                      </div>
                    </form>
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
          </section>
        )}
        </main>
      </div>

    </div>
  );
}

export default function App() {
  useEffect(() => {
    applyThemePreference(loadThemePreference());
  }, []);

  if (window.location.pathname === "/openai-auth/callback") {
    return <OpenAiAuthCallbackPage />;
  }
  return <HubApp />;
}
