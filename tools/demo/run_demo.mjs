#!/usr/bin/env node

import fs from "node:fs";
import fsp from "node:fs/promises";
import path from "node:path";
import process from "node:process";
import { spawn, spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { firefox } from "playwright";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const repoRoot = path.resolve(__dirname, "..", "..");
const hostVisibleRepoRoot = fs.existsSync("/home/joew/projects/agent_hub")
  ? "/home/joew/projects/agent_hub"
  : repoRoot;

const DEFAULTS = {
  mode: "all",
  port: 8765,
  display: ":77",
  screenWidth: 1920,
  screenHeight: 1200,
  viewportWidth: 1580,
  viewportHeight: 980,
  projectName: "Agent Hub Frontend Demo Project",
  repoUrl: "https://github.com/JoeWolski/agent_hub.git",
  configFile: path.join(hostVisibleRepoRoot, "config", "agent.config.toml"),
  outputDir: path.join(hostVisibleRepoRoot, "tools", "demo", "output"),
  scriptFile: path.join(hostVisibleRepoRoot, "tools", "demo", "output", "demo_script.json"),
  videoFile: path.join(hostVisibleRepoRoot, "tools", "demo", "output", "agent_hub_demo.mp4"),
  scenarioFile: path.join(hostVisibleRepoRoot, "tools", "demo", "scenarios", "frontend_default.json"),
  theme: "system",
  instructionText: "# Please make a fake image and a fake view, then show a fake video preview."
};

const DEFAULT_TIMING_MS = {
  after_page_load_ms: 900,
  after_add_project_click_ms: 500,
  after_build_ready_ms: 1000,
  after_first_chat_start_ms: 900,
  after_second_chat_start_ms: 1000,
  before_terminal_typing_ms: 700,
  typing_char_delay_ms: 24,
  after_instruction_submit_ms: 1300,
  after_artifacts_publish_ms: 1200,
  hold_video_preview_ms: 5000,
  mouse_move_duration_ms: 360
};

function isRemoteRepoSpec(value) {
  return /^(https?:\/\/|ssh:\/\/|git@)/i.test(String(value || "").trim());
}

function normalizeRepoSpec(value) {
  const raw = String(value || "").trim();
  if (!raw) {
    throw new Error("Repository URL/path cannot be empty.");
  }
  if (isRemoteRepoSpec(raw)) {
    return raw;
  }
  return path.resolve(raw);
}

function normalizeTheme(value) {
  const normalized = String(value || "").trim().toLowerCase();
  if (normalized === "system" || normalized === "light" || normalized === "dark") {
    return normalized;
  }
  throw new Error(`Invalid --theme value: ${value}. Expected one of: system, light, dark`);
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function nowIso() {
  return new Date().toISOString();
}

function logStep(message) {
  process.stdout.write(`[demo] ${message}\n`);
}

function parseArgs(argv) {
  const options = { ...DEFAULTS };
  let idx = 0;
  const first = argv[0];
  if (first && !first.startsWith("--") && ["plan", "record", "all", "validate"].includes(first)) {
    options.mode = first;
    idx = 1;
  }

  for (let i = idx; i < argv.length; i += 1) {
    const arg = argv[i];
    const next = argv[i + 1];
    if (arg === "--port" && next) {
      options.port = Number(next);
      i += 1;
      continue;
    }
    if (arg === "--display" && next) {
      options.display = String(next);
      i += 1;
      continue;
    }
    if (arg === "--repo-url" && next) {
      options.repoUrl = normalizeRepoSpec(next);
      i += 1;
      continue;
    }
    if (arg === "--config-file" && next) {
      options.configFile = path.resolve(next);
      i += 1;
      continue;
    }
    if (arg === "--project-name" && next) {
      options.projectName = String(next);
      i += 1;
      continue;
    }
    if (arg === "--output-dir" && next) {
      options.outputDir = path.resolve(next);
      i += 1;
      continue;
    }
    if (arg === "--script-file" && next) {
      options.scriptFile = path.resolve(next);
      i += 1;
      continue;
    }
    if (arg === "--video-file" && next) {
      options.videoFile = path.resolve(next);
      i += 1;
      continue;
    }
    if (arg === "--scenario-file" && next) {
      options.scenarioFile = path.resolve(next);
      i += 1;
      continue;
    }
    if (arg === "--theme" && next) {
      options.theme = normalizeTheme(next);
      i += 1;
      continue;
    }
    if (arg === "--viewport-width" && next) {
      options.viewportWidth = Number(next);
      i += 1;
      continue;
    }
    if (arg === "--viewport-height" && next) {
      options.viewportHeight = Number(next);
      i += 1;
      continue;
    }
    if (arg === "--screen-width" && next) {
      options.screenWidth = Number(next);
      i += 1;
      continue;
    }
    if (arg === "--screen-height" && next) {
      options.screenHeight = Number(next);
      i += 1;
      continue;
    }
    if (arg === "--instruction" && next) {
      options.instructionText = String(next);
      i += 1;
      continue;
    }
    throw new Error(`Unknown or incomplete argument: ${arg}`);
  }

  if (!Number.isFinite(options.port) || options.port <= 0) {
    throw new Error(`Invalid --port value: ${options.port}`);
  }
  if (!isRemoteRepoSpec(options.repoUrl) && !fs.existsSync(options.repoUrl)) {
    throw new Error(`--repo-url does not exist: ${options.repoUrl}`);
  }
  if (!fs.existsSync(options.scenarioFile)) {
    throw new Error(`--scenario-file does not exist: ${options.scenarioFile}`);
  }
  if (!fs.existsSync(options.configFile)) {
    throw new Error(`--config-file does not exist: ${options.configFile}`);
  }
  options.scriptFile = path.resolve(options.scriptFile);
  options.videoFile = path.resolve(options.videoFile);
  options.outputDir = path.resolve(options.outputDir);
  options.theme = normalizeTheme(options.theme);
  return options;
}

function envWithDisplay(display, extra = {}) {
  return {
    ...process.env,
    DISPLAY: display,
    ...extra
  };
}

function runChecked(cmd, args, opts = {}) {
  const result = spawnSync(cmd, args, {
    encoding: "utf-8",
    cwd: opts.cwd || process.cwd(),
    env: opts.env || process.env,
    stdio: opts.stdio || "pipe"
  });
  if (result.status !== 0) {
    const stdout = (result.stdout || "").trim();
    const stderr = (result.stderr || "").trim();
    const combined = [stdout, stderr].filter(Boolean).join("\n");
    throw new Error(`Command failed (${cmd} ${args.join(" ")}):\n${combined}`);
  }
  return (result.stdout || "").trim();
}

function runMaybe(cmd, args, opts = {}) {
  const result = spawnSync(cmd, args, {
    encoding: "utf-8",
    cwd: opts.cwd || process.cwd(),
    env: opts.env || process.env,
    stdio: opts.stdio || "pipe"
  });
  return {
    ok: result.status === 0,
    stdout: (result.stdout || "").trim(),
    stderr: (result.stderr || "").trim(),
    code: result.status
  };
}

function startLoggedProcess({ name, cmd, args, cwd, env, logPath }) {
  const child = spawn(cmd, args, {
    cwd,
    env,
    stdio: ["pipe", "pipe", "pipe"]
  });
  const logStream = fs.createWriteStream(logPath, { flags: "w" });
  const prefix = `[${nowIso()}] ${name}`;
  logStream.write(`${prefix} started: ${cmd} ${args.join(" ")}\n`);
  child.stdout.on("data", (chunk) => {
    logStream.write(chunk);
  });
  child.stderr.on("data", (chunk) => {
    logStream.write(chunk);
  });
  child.on("close", (code, signal) => {
    logStream.write(`\n[${nowIso()}] ${name} exited code=${code} signal=${signal || ""}\n`);
    logStream.end();
  });
  return child;
}

async function stopProcess(child, { name, graceMs = 8000 }) {
  if (!child || child.exitCode !== null) {
    return;
  }
  child.kill("SIGTERM");
  const started = Date.now();
  while (child.exitCode === null && Date.now() - started < graceMs) {
    await sleep(120);
  }
  if (child.exitCode === null) {
    child.kill("SIGKILL");
    const forceStart = Date.now();
    while (child.exitCode === null && Date.now() - forceStart < 2000) {
      await sleep(80);
    }
  }
  if (child.exitCode === null) {
    throw new Error(`Unable to stop process: ${name}`);
  }
}

async function waitProcessExit(child, timeoutMs) {
  if (!child || child.exitCode !== null) {
    return true;
  }
  const started = Date.now();
  while (child.exitCode === null && Date.now() - started < timeoutMs) {
    await sleep(120);
  }
  return child.exitCode !== null;
}

async function waitFor(predicate, { timeoutMs, intervalMs = 500, label }) {
  const started = Date.now();
  while (Date.now() - started < timeoutMs) {
    try {
      const value = await predicate();
      if (value) {
        return value;
      }
    } catch {
      // swallow and retry until timeout
    }
    await sleep(intervalMs);
  }
  throw new Error(`Timeout waiting for ${label}`);
}

async function getHubState(baseUrl) {
  const response = await fetch(`${baseUrl}/api/state`);
  if (!response.ok) {
    const body = await response.text();
    throw new Error(`Hub state request failed (${response.status}): ${body}`);
  }
  return response.json();
}

async function waitForHubReady(baseUrl) {
  await waitFor(
    async () => {
      const response = await fetch(`${baseUrl}/api/state`);
      return response.ok;
    },
    { timeoutMs: 300_000, intervalMs: 800, label: "hub readiness" }
  );
}

function normalizeScenarioDefinition(rawScenario, scenarioPath) {
  if (!rawScenario || typeof rawScenario !== "object") {
    throw new Error(`Scenario file is not a valid object: ${scenarioPath}`);
  }
  if (!Array.isArray(rawScenario.steps) || rawScenario.steps.length === 0) {
    throw new Error(`Scenario file must define a non-empty 'steps' array: ${scenarioPath}`);
  }
  const steps = rawScenario.steps.map((step, index) => {
    if (!step || typeof step !== "object") {
      throw new Error(`Scenario step ${index + 1} is invalid in ${scenarioPath}`);
    }
    const action = String(step.action || "").trim();
    if (!action) {
      throw new Error(`Scenario step ${index + 1} is missing 'action' in ${scenarioPath}`);
    }
    const label = String(step.label || step.title || action).trim();
    const params = step.params && typeof step.params === "object" ? step.params : {};
    return {
      action,
      label,
      params
    };
  });
  const timingSource = rawScenario.timing_ms && typeof rawScenario.timing_ms === "object"
    ? rawScenario.timing_ms
    : {};
  const timing = { ...DEFAULT_TIMING_MS };
  for (const [key, value] of Object.entries(timingSource)) {
    if (Number.isFinite(Number(value)) && Number(value) >= 0) {
      timing[key] = Number(value);
    }
  }
  return {
    id: String(rawScenario.id || path.basename(scenarioPath, path.extname(scenarioPath))),
    title: String(rawScenario.title || "Agent Hub Demo Scenario"),
    description: String(rawScenario.description || ""),
    timing_ms: timing,
    steps
  };
}

async function loadScenarioDefinition(scenarioPath) {
  let parsed;
  try {
    parsed = JSON.parse(await fsp.readFile(scenarioPath, "utf-8"));
  } catch (error) {
    throw new Error(`Unable to parse scenario JSON at ${scenarioPath}: ${error.message}`);
  }
  return normalizeScenarioDefinition(parsed, scenarioPath);
}

function buildScenarioScript(options, scenarioDefinition) {
  return {
    version: 2,
    generated_at: "",
    scenario_id: scenarioDefinition.id,
    scenario_title: scenarioDefinition.title,
    repo_url: options.repoUrl,
    project_name: options.projectName,
    instruction_text: options.instructionText,
    steps: scenarioDefinition.steps.map((step, index) => ({
      order: index + 1,
      action: step.action,
      label: step.label
    })),
    timing_ms: {
      ...scenarioDefinition.timing_ms
    },
    observed_ms: {}
  };
}

function parseMouseLocation(raw) {
  const lines = String(raw || "").split("\n");
  const data = {};
  for (const line of lines) {
    const [k, v] = line.split("=");
    if (!k || !v) {
      continue;
    }
    data[k.trim()] = Number(v.trim());
  }
  if (!Number.isFinite(data.X) || !Number.isFinite(data.Y)) {
    return { x: 0, y: 0 };
  }
  return { x: data.X, y: data.Y };
}

function getMouseLocation(display) {
  const result = runMaybe("xdotool", ["getmouselocation", "--shell"], { env: envWithDisplay(display) });
  if (!result.ok) {
    return { x: 0, y: 0 };
  }
  return parseMouseLocation(result.stdout);
}

async function moveMouseHuman(display, mouseState, x, y, durationMs = 360) {
  const clampedX = Math.max(0, Math.round(x));
  const clampedY = Math.max(0, Math.round(y));
  if (!Number.isFinite(mouseState.x) || !Number.isFinite(mouseState.y)) {
    const start = getMouseLocation(display);
    mouseState.x = start.x;
    mouseState.y = start.y;
  }
  const startX = mouseState.x;
  const startY = mouseState.y;
  const distance = Math.hypot(clampedX - startX, clampedY - startY);
  const steps = Math.max(8, Math.min(36, Math.round(distance / 25)));
  const stepDelay = Math.max(6, Math.floor(durationMs / steps));
  for (let i = 1; i <= steps; i += 1) {
    const t = i / steps;
    const eased = t * t * (3 - 2 * t);
    const nextX = Math.round(startX + (clampedX - startX) * eased);
    const nextY = Math.round(startY + (clampedY - startY) * eased);
    runChecked("xdotool", ["mousemove", String(nextX), String(nextY)], { env: envWithDisplay(display) });
    await sleep(stepDelay);
  }
  mouseState.x = clampedX;
  mouseState.y = clampedY;
}

async function locatorCenter(page, locator) {
  await locator.waitFor({ state: "visible", timeout: 120_000 });
  await locator.scrollIntoViewIfNeeded();
  const box = await locator.boundingBox();
  if (!box) {
    throw new Error("Unable to compute locator bounding box.");
  }
  const metrics = await page.evaluate(() => ({
    screenX: window.screenX || 0,
    screenY: window.screenY || 0,
    outerHeight: window.outerHeight || 0,
    innerHeight: window.innerHeight || 0
  }));
  const browserChromeY = Math.max(0, Number(metrics.outerHeight) - Number(metrics.innerHeight));
  const x = Math.round(Number(metrics.screenX) + box.x + box.width / 2);
  const y = Math.round(Number(metrics.screenY) + browserChromeY + box.y + box.height / 2);
  return { x, y };
}

async function clickLocator(page, display, mouseState, locator, label, timing) {
  const center = await locatorCenter(page, locator);
  await moveMouseHuman(display, mouseState, center.x, center.y, timing.mouse_move_duration_ms);
  await locator.click({ timeout: 120_000 });
  await sleep(180);
  if (label) {
    // Keep motion deterministic by pacing each click.
    await sleep(80);
  }
}

async function clearAndType(page, display, mouseState, locator, text, timing) {
  await clickLocator(page, display, mouseState, locator, "type-target", timing);
  await locator.fill("");
  await sleep(80);
  if (text) {
    await locator.type(text, { delay: Math.max(1, timing.typing_char_delay_ms) });
  }
}

async function waitForProjectReady(baseUrl, projectName) {
  return waitFor(
    async () => {
      const state = await getHubState(baseUrl);
      const project = (state.projects || []).find((item) => String(item.name || "") === projectName);
      if (!project) {
        return false;
      }
      return project.build_status === "ready" ? project : false;
    },
    {
      timeoutMs: 3_600_000,
      intervalMs: 1000,
      label: `project '${projectName}' build ready`
    }
  );
}

async function waitForRunningChats(baseUrl, projectName, minCount) {
  return waitFor(
    async () => {
      const state = await getHubState(baseUrl);
      const project = (state.projects || []).find((item) => String(item.name || "") === projectName);
      if (!project) {
        return false;
      }
      const running = (state.chats || []).filter(
        (chat) => chat.project_id === project.id && String(chat.status || "") === "running"
      );
      return running.length >= minCount ? running : false;
    },
    {
      timeoutMs: 600_000,
      intervalMs: 800,
      label: `${minCount} running chats for '${projectName}'`
    }
  );
}

function normalizeRealPath(value) {
  try {
    return fs.realpathSync(value);
  } catch {
    return path.resolve(value);
  }
}

async function resolveArtifactTokenForWorkspace(workspacePath) {
  const normalizedWorkspace = normalizeRealPath(workspacePath);
  logStep(`Resolving artifact token for workspace ${workspacePath}`);
  return waitFor(
    async () => {
      const list = runMaybe("docker", ["ps", "--format", "{{.ID}}"]);
      if (!list.ok || !list.stdout) {
        return false;
      }
      const ids = list.stdout
        .split("\n")
        .map((line) => line.trim())
        .filter(Boolean);
      for (const id of ids) {
        const inspectResult = runMaybe("docker", ["inspect", id]);
        if (!inspectResult.ok || !inspectResult.stdout) {
          continue;
        }
        let parsed;
        try {
          parsed = JSON.parse(inspectResult.stdout);
        } catch {
          continue;
        }
        const container = Array.isArray(parsed) ? parsed[0] : null;
        if (!container) {
          continue;
        }
        const mounts = Array.isArray(container.Mounts) ? container.Mounts : [];
        const mountMatch = mounts.some((mount) => {
          const source = mount?.Source;
          if (!source) {
            return false;
          }
          return normalizeRealPath(source) === normalizedWorkspace;
        });
        if (!mountMatch) {
          continue;
        }
        const envList = Array.isArray(container?.Config?.Env) ? container.Config.Env : [];
        const tokenEntry = envList.find((entry) => String(entry).startsWith("AGENT_HUB_ARTIFACT_TOKEN="));
        if (!tokenEntry) {
          continue;
        }
        const token = tokenEntry.split("=", 2)[1] || "";
        if (token.trim()) {
          return token.trim();
        }
      }
      return false;
    },
    {
      timeoutMs: 180_000,
      intervalMs: 1000,
      label: `artifact token for workspace ${workspacePath}`
    }
  );
}

function createFakeArtifacts(workspacePath) {
  const outputDir = path.join(workspacePath, "demo_outputs");
  fs.mkdirSync(outputDir, { recursive: true });
  const fakeImage = path.join(outputDir, "fake-image.png");
  const fakeView = path.join(outputDir, "fake-view.png");
  const fakeVideo = path.join(outputDir, "fake-preview.mp4");

  runChecked("ffmpeg", [
    "-y",
    "-f",
    "lavfi",
    "-i",
    "color=c=#1d4ed8:s=960x540:d=1",
    "-frames:v",
    "1",
    fakeImage
  ]);

  runChecked("ffmpeg", [
    "-y",
    "-f",
    "lavfi",
    "-i",
    "color=c=#0f766e:s=960x540:d=1",
    "-vf",
    "drawgrid=width=80:height=60:thickness=2:color=white@0.5",
    "-frames:v",
    "1",
    fakeView
  ]);

  runChecked("ffmpeg", [
    "-y",
    "-f",
    "lavfi",
    "-i",
    "testsrc=size=960x540:rate=24",
    "-t",
    "3",
    "-pix_fmt",
    "yuv420p",
    fakeVideo
  ]);

  return [fakeImage, fakeView, fakeVideo];
}

async function publishArtifactsForChat({ baseUrl, chatId, workspacePath, repoRootPath }) {
  const token = await resolveArtifactTokenForWorkspace(workspacePath);
  const files = createFakeArtifacts(workspacePath);
  logStep(`Publishing artifacts via hub_artifact (${files.map((file) => path.basename(file)).join(", ")})`);
  const hubArtifact = path.join(repoRootPath, "docker", "agent_cli", "hub_artifact");
  const artifactsUrl = `${baseUrl}/api/chats/${chatId}/artifacts/publish`;
  runChecked("bash", [hubArtifact, "publish", ...files], {
    cwd: repoRootPath,
    env: envWithDisplay("", {
      AGENT_HUB_ARTIFACTS_URL: artifactsUrl,
      AGENT_HUB_ARTIFACT_TOKEN: token,
      HUB_ARTIFACT_RETRY_DELAY_BASE_SEC: "0"
    })
  });
  return files;
}

async function maybeFocusWindow(display) {
  const probes = ["Agent Hub", "Chromium", "Chrome"];
  for (const probe of probes) {
    const found = runMaybe("xdotool", ["search", "--name", probe], { env: envWithDisplay(display) });
    if (!found.ok || !found.stdout) {
      continue;
    }
    const windowId = found.stdout.split("\n").map((line) => line.trim()).find(Boolean);
    if (!windowId) {
      continue;
    }
    runMaybe("xdotool", ["windowactivate", "--sync", windowId], { env: envWithDisplay(display) });
    await sleep(200);
    return;
  }
}

function stepMetricKey(index, actionName) {
  const sanitizedAction = String(actionName || "")
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "") || "step";
  return `step_${String(index).padStart(2, "0")}_${sanitizedAction}_ms`;
}

function selectProjectCard(page, projectName) {
  return page
    .locator(".project-card")
    .filter({ has: page.locator("h3", { hasText: projectName }) })
    .first();
}

function selectProjectGroup(page, projectName) {
  return page
    .locator(".project-chat-group")
    .filter({ has: page.locator("h3", { hasText: projectName }) })
    .first();
}

function selectChatCardsInGroup(page, projectGroup) {
  return projectGroup.locator("article.card", {
    has: page.locator(".chat-card-header")
  });
}

async function actionOpenUi(ctx) {
  await ctx.page.goto(ctx.baseUrl, { waitUntil: "domcontentloaded" });
  await ctx.page.locator(".app-root").first().waitFor({ state: "visible", timeout: 120_000 });
  await maybeFocusWindow(ctx.display);
  await sleep(ctx.timing.after_page_load_ms);
}

async function actionCreateProject(ctx) {
  const projectsTab = ctx.page.locator(".tab-button", { hasText: "Projects" }).first();
  await clickLocator(ctx.page, ctx.display, ctx.mouseState, projectsTab, "projects-tab", ctx.timing);

  const repoInput = ctx.page.locator("section.projects-create input[placeholder*='git@github.com']").first();
  const nameInput = ctx.page.locator("section.projects-create input[placeholder='Optional project name']").first();
  const addProjectButton = ctx.page.locator("section.projects-create button", { hasText: "Add project" }).first();

  await clearAndType(ctx.page, ctx.display, ctx.mouseState, repoInput, ctx.options.repoUrl, ctx.timing);
  await clearAndType(ctx.page, ctx.display, ctx.mouseState, nameInput, ctx.options.projectName, ctx.timing);
  await clickLocator(ctx.page, ctx.display, ctx.mouseState, addProjectButton, "add-project", ctx.timing);
  await sleep(ctx.timing.after_add_project_click_ms);
}

async function actionWaitProjectReady(ctx) {
  const buildStart = Date.now();
  await waitForProjectReady(ctx.baseUrl, ctx.options.projectName);
  ctx.observed.build_ready_ms = Date.now() - buildStart;
  await sleep(ctx.timing.after_build_ready_ms);
}

async function actionStartFirstChat(ctx) {
  const projectCard = selectProjectCard(ctx.page, ctx.options.projectName);
  await projectCard.waitFor({ state: "visible", timeout: 120_000 });
  ctx.ui.projectCard = projectCard;

  const newChatButtonProjects = projectCard.locator("button.btn-primary", { hasText: "New chat" }).first();
  await clickLocator(ctx.page, ctx.display, ctx.mouseState, newChatButtonProjects, "first-chat-start", ctx.timing);
  const firstChatStart = Date.now();
  await waitForRunningChats(ctx.baseUrl, ctx.options.projectName, 1);
  ctx.observed.first_chat_ready_ms = Date.now() - firstChatStart;
  await sleep(ctx.timing.after_first_chat_start_ms);
}

async function actionStartSecondChat(ctx) {
  const chatsTab = ctx.page.locator(".tab-button", { hasText: "Chats" }).first();
  await clickLocator(ctx.page, ctx.display, ctx.mouseState, chatsTab, "chats-tab", ctx.timing);
  await sleep(700);

  const projectGroup = selectProjectGroup(ctx.page, ctx.options.projectName);
  await projectGroup.waitFor({ state: "visible", timeout: 120_000 });
  ctx.ui.projectGroup = projectGroup;

  const chatCardLocator = selectChatCardsInGroup(ctx.page, projectGroup);
  ctx.ui.chatCardLocator = chatCardLocator;
  const visibleChatCards = await chatCardLocator.count();
  if (visibleChatCards === 0) {
    const groupHeader = projectGroup.locator(".project-chat-row").first();
    await clickLocator(ctx.page, ctx.display, ctx.mouseState, groupHeader, "expand-chat-group", ctx.timing);
    await sleep(400);
  }

  const newChatButtonChats = projectGroup.locator("button.project-group-action", { hasText: "New chat" }).first();
  await clickLocator(ctx.page, ctx.display, ctx.mouseState, newChatButtonChats, "second-chat-start", ctx.timing);
  const secondChatStart = Date.now();
  await waitForRunningChats(ctx.baseUrl, ctx.options.projectName, 2);
  ctx.observed.second_chat_ready_ms = Date.now() - secondChatStart;
  await sleep(ctx.timing.after_second_chat_start_ms);
}

async function actionFocusFirstChatTerminal(ctx) {
  if (!ctx.ui.projectGroup) {
    ctx.ui.projectGroup = selectProjectGroup(ctx.page, ctx.options.projectName);
    await ctx.ui.projectGroup.waitFor({ state: "visible", timeout: 120_000 });
  }
  if (!ctx.ui.chatCardLocator) {
    ctx.ui.chatCardLocator = selectChatCardsInGroup(ctx.page, ctx.ui.projectGroup);
  }

  let chatCardCount = await ctx.ui.chatCardLocator.count();
  for (let attempt = 1; chatCardCount === 0 && attempt <= 4; attempt += 1) {
    logStep(`No visible chat cards yet; expand attempt ${attempt}`);
    let groupHeader = ctx.ui.projectGroup.locator(".project-chat-head").first();
    if ((await groupHeader.count()) === 0) {
      groupHeader = ctx.ui.projectGroup.locator(".project-chat-row").first();
    }
    await clickLocator(ctx.page, ctx.display, ctx.mouseState, groupHeader, "expand-chat-group-after-start", ctx.timing);
    await sleep(700);
    chatCardCount = await ctx.ui.chatCardLocator.count();
  }
  if (chatCardCount === 0) {
    throw new Error("Unable to reveal project chat cards in Chats tab.");
  }

  const firstChatCard = ctx.ui.chatCardLocator.first();
  await firstChatCard.waitFor({ state: "visible", timeout: 60_000 });
  ctx.ui.firstChatCard = firstChatCard;
  logStep("First chat card is visible");

  let firstTerminalShell = firstChatCard.locator(".chat-terminal-shell:not(.chat-terminal-placeholder)").first();
  if ((await firstTerminalShell.count()) === 0) {
    logStep("Terminal shell is hidden; expanding first chat card");
    const firstHead = firstChatCard.locator(".chat-card-header").first();
    await clickLocator(ctx.page, ctx.display, ctx.mouseState, firstHead, "expand-first-chat", ctx.timing);
    await waitFor(
      async () => {
        const readyShellCount = await firstChatCard.locator(".chat-terminal-shell:not(.chat-terminal-placeholder)").count();
        return readyShellCount > 0;
      },
      {
        timeoutMs: 120_000,
        intervalMs: 600,
        label: "first chat terminal readiness"
      }
    );
    firstTerminalShell = firstChatCard.locator(".chat-terminal-shell:not(.chat-terminal-placeholder)").first();
  }
  await firstTerminalShell.waitFor({ state: "visible", timeout: 120_000 });
  ctx.ui.firstTerminalShell = firstTerminalShell;
  logStep("First chat terminal is visible");

  await clickLocator(ctx.page, ctx.display, ctx.mouseState, firstTerminalShell, "focus-terminal", ctx.timing);
  await sleep(ctx.timing.before_terminal_typing_ms);
}

async function actionTypeInstruction(ctx) {
  await ctx.page.keyboard.type(ctx.script.instruction_text, {
    delay: Math.max(1, ctx.timing.typing_char_delay_ms)
  });
  await ctx.page.keyboard.press("Enter");
  await sleep(ctx.timing.after_instruction_submit_ms);
}

async function actionPublishFakeArtifacts(ctx) {
  const stateAfterChats = await getHubState(ctx.baseUrl);
  const project = (stateAfterChats.projects || []).find((item) => String(item.name || "") === ctx.options.projectName);
  if (!project) {
    throw new Error(`Unable to locate project '${ctx.options.projectName}' after chat start.`);
  }
  const runningChats = (stateAfterChats.chats || [])
    .filter((chat) => chat.project_id === project.id && String(chat.status || "") === "running")
    .sort((a, b) => Date.parse(b.created_at || "") - Date.parse(a.created_at || ""));
  if (runningChats.length < 2) {
    throw new Error(`Expected at least 2 running chats, found ${runningChats.length}.`);
  }
  const targetChat = runningChats[0];
  const workspacePath = String(targetChat.workspace || "").trim();
  if (!workspacePath) {
    throw new Error("Target chat workspace path is missing.");
  }
  ctx.ui.targetChatId = String(targetChat.id);
  ctx.ui.targetWorkspacePath = workspacePath;

  const publishStart = Date.now();
  await publishArtifactsForChat({
    baseUrl: ctx.baseUrl,
    chatId: String(targetChat.id),
    workspacePath,
    repoRootPath: repoRoot
  });
  ctx.observed.artifact_publish_ms = Date.now() - publishStart;
  await sleep(ctx.timing.after_artifacts_publish_ms);
}

async function actionOpenFakeVideoPreview(ctx) {
  if (!ctx.ui.firstChatCard) {
    if (!ctx.ui.projectGroup) {
      ctx.ui.projectGroup = selectProjectGroup(ctx.page, ctx.options.projectName);
      await ctx.ui.projectGroup.waitFor({ state: "visible", timeout: 120_000 });
    }
    if (!ctx.ui.chatCardLocator) {
      ctx.ui.chatCardLocator = selectChatCardsInGroup(ctx.page, ctx.ui.projectGroup);
    }
    ctx.ui.firstChatCard = ctx.ui.chatCardLocator.first();
  }

  const videoTile = ctx.ui.firstChatCard
    .locator(".chat-artifact-thumbnail")
    .filter({ hasText: "fake-preview.mp4" })
    .first();
  await videoTile.waitFor({ state: "visible", timeout: 120_000 });
  await clickLocator(ctx.page, ctx.display, ctx.mouseState, videoTile, "open-video-preview", ctx.timing);
  await ctx.page.locator(".artifact-preview-modal video.artifact-preview-video").first().waitFor({
    state: "visible",
    timeout: 30_000
  });
  await sleep(ctx.timing.hold_video_preview_ms);
}

const ACTION_HANDLERS = {
  open_ui: actionOpenUi,
  create_project: actionCreateProject,
  wait_project_ready: actionWaitProjectReady,
  start_first_chat: actionStartFirstChat,
  start_second_chat: actionStartSecondChat,
  focus_first_chat_terminal: actionFocusFirstChatTerminal,
  type_instruction: actionTypeInstruction,
  publish_fake_artifacts: actionPublishFakeArtifacts,
  open_fake_video_preview: actionOpenFakeVideoPreview
};

async function runScenario({ page, baseUrl, display, options, script, scenarioDefinition }) {
  const ctx = {
    page,
    baseUrl,
    display,
    options,
    script,
    timing: script.timing_ms,
    mouseState: getMouseLocation(display),
    observed: {},
    ui: {}
  };

  for (let i = 0; i < scenarioDefinition.steps.length; i += 1) {
    const step = scenarioDefinition.steps[i];
    const action = ACTION_HANDLERS[step.action];
    if (!action) {
      throw new Error(`Unknown scenario action '${step.action}' at step ${i + 1}`);
    }
    logStep(`Step ${i + 1}/${scenarioDefinition.steps.length}: ${step.label}`);
    const started = Date.now();
    try {
      await action(ctx, step.params || {});
    } catch (error) {
      error.scenarioStep = {
        index: i + 1,
        action: step.action,
        label: step.label
      };
      throw error;
    }
    ctx.observed[stepMetricKey(i + 1, step.action)] = Date.now() - started;
  }

  return ctx.observed;
}

function startXvfb({ display, width, height, logPath }) {
  return startLoggedProcess({
    name: "xvfb",
    cmd: "Xvfb",
    args: [display, "-screen", "0", `${width}x${height}x24`, "-ac", "+extension", "RANDR"],
    cwd: repoRoot,
    env: process.env,
    logPath
  });
}

function startHub({ port, dataDir, display, configFile, logPath }) {
  return startLoggedProcess({
    name: "agent_hub",
    cmd: "uv",
    args: [
      "run",
      "agent_hub",
      "--host",
      "127.0.0.1",
      "--port",
      String(port),
      "--data-dir",
      dataDir,
      "--config-file",
      configFile,
      "--frontend-build"
    ],
    cwd: repoRoot,
    env: envWithDisplay(display),
    logPath
  });
}

function startFfmpeg({ display, width, height, outputPath, logPath }) {
  return startLoggedProcess({
    name: "ffmpeg",
    cmd: "ffmpeg",
    args: [
      "-y",
      "-f",
      "x11grab",
      "-framerate",
      "30",
      "-video_size",
      `${width}x${height}`,
      "-i",
      `${display}.0+0,0`,
      "-c:v",
      "libx264",
      "-preset",
      "veryfast",
      "-crf",
      "21",
      "-pix_fmt",
      "yuv420p",
      outputPath
    ],
    cwd: repoRoot,
    env: envWithDisplay(display),
    logPath
  });
}

async function writeFailureDiagnostics({ phaseName, options, script, scenarioDefinition, baseUrl, page, error }) {
  const failureDir = path.join(options.outputDir, "failures", phaseName, `${Date.now()}`);
  await fsp.mkdir(failureDir, { recursive: true });

  const detail = {
    generated_at: nowIso(),
    phase: phaseName,
    scenario_id: scenarioDefinition.id,
    scenario_title: scenarioDefinition.title,
    repo_url: options.repoUrl,
    project_name: options.projectName,
    current_step: error?.scenarioStep || null,
    error: {
      message: String(error?.message || "Unknown error"),
      stack: String(error?.stack || "")
    }
  };

  if (page) {
    const screenshotPath = path.join(failureDir, "failure.png");
    try {
      await page.screenshot({ path: screenshotPath, fullPage: true });
      detail.screenshot = screenshotPath;
    } catch {
      // continue with metadata/state dump
    }
  }

  try {
    const state = await getHubState(baseUrl);
    const statePath = path.join(failureDir, "hub_state.json");
    await fsp.writeFile(statePath, `${JSON.stringify(state, null, 2)}\n`, "utf-8");
    detail.hub_state = statePath;
  } catch {
    // Ignore state fetch failures while recording diagnostics.
  }

  const scriptPath = path.join(failureDir, "script_snapshot.json");
  await fsp.writeFile(scriptPath, `${JSON.stringify(script, null, 2)}\n`, "utf-8");
  detail.script_snapshot = scriptPath;

  const detailPath = path.join(failureDir, "failure_details.json");
  await fsp.writeFile(detailPath, `${JSON.stringify(detail, null, 2)}\n`, "utf-8");
  logStep(`Failure diagnostics written: ${failureDir}`);
}

async function runPhase({ phaseName, options, script, scenarioDefinition, record }) {
  logStep(`Starting ${phaseName} (${record ? "record" : "plan"})`);
  const phaseOutput = path.join(options.outputDir, phaseName);
  const logsDir = path.join(options.outputDir, "logs");
  const dataDir = path.join(phaseOutput, "hub_data");
  await fsp.mkdir(logsDir, { recursive: true });
  await fsp.rm(phaseOutput, { recursive: true, force: true });
  await fsp.mkdir(phaseOutput, { recursive: true });

  const xvfbLog = path.join(logsDir, `${phaseName}-xvfb.log`);
  const hubLog = path.join(logsDir, `${phaseName}-hub.log`);
  const ffmpegLog = path.join(logsDir, `${phaseName}-ffmpeg.log`);
  const phaseVideoFile = path.join(phaseOutput, "phase_recording.mp4");
  const baseUrl = `http://127.0.0.1:${options.port}`;

  const children = [];
  let browser;
  let context;
  let page;
  let ffmpegProcess;
  try {
    const xvfb = startXvfb({
      display: options.display,
      width: options.screenWidth,
      height: options.screenHeight,
      logPath: xvfbLog
    });
    children.push({ name: "xvfb", proc: xvfb });
    await sleep(1500);

    const hub = startHub({
      port: options.port,
      dataDir,
      display: options.display,
      configFile: options.configFile,
      logPath: hubLog
    });
    children.push({ name: "hub", proc: hub });
    await waitForHubReady(baseUrl);
    await sleep(700);

    if (record) {
      ffmpegProcess = startFfmpeg({
        display: options.display,
        width: options.screenWidth,
        height: options.screenHeight,
        outputPath: phaseVideoFile,
        logPath: ffmpegLog
      });
      children.push({ name: "ffmpeg", proc: ffmpegProcess });
      await sleep(1200);
    }

    browser = await firefox.launch({
      headless: false,
      env: envWithDisplay(options.display),
      args: [`--width=${options.viewportWidth}`, `--height=${options.viewportHeight}`]
    });
    context = await browser.newContext({
      viewport: {
        width: options.viewportWidth,
        height: options.viewportHeight
      }
    });
    await context.addInitScript(({ themeValue }) => {
      try {
        window.localStorage.setItem("agent_hub_theme", themeValue);
      } catch {
        // Ignore localStorage failures and let app defaults apply.
      }
      const root = document.documentElement;
      if (!root) {
        return;
      }
      if (themeValue === "system") {
        root.removeAttribute("data-theme");
        return;
      }
      root.setAttribute("data-theme", themeValue);
    }, { themeValue: options.theme });
    page = await context.newPage();

    let observed;
    try {
      observed = await runScenario({
        page,
        baseUrl,
        display: options.display,
        options,
        script,
        scenarioDefinition
      });
    } catch (error) {
      await writeFailureDiagnostics({
        phaseName,
        options,
        script,
        scenarioDefinition,
        baseUrl,
        page,
        error
      }).catch(() => {});
      throw error;
    }
    logStep(`Finished ${phaseName}`);
    return {
      observed,
      phaseVideoFile: record ? phaseVideoFile : ""
    };
  } finally {
    if (page) {
      await page.close().catch(() => {});
    }
    if (context) {
      await context.close().catch(() => {});
    }
    if (browser) {
      await browser.close().catch(() => {});
    }

    if (ffmpegProcess && ffmpegProcess.exitCode === null) {
      try {
        ffmpegProcess.stdin.write("q\n");
      } catch {
        // Ignore.
      }
      await waitProcessExit(ffmpegProcess, 5000);
    }

    for (let i = children.length - 1; i >= 0; i -= 1) {
      const child = children[i];
      await stopProcess(child.proc, { name: child.name }).catch(() => {});
    }
  }
}

async function ensureOutputPaths(options) {
  await fsp.mkdir(options.outputDir, { recursive: true });
  await fsp.mkdir(path.dirname(options.scriptFile), { recursive: true });
  await fsp.mkdir(path.dirname(options.videoFile), { recursive: true });
}

function mergeObservedScript(script, observed) {
  const merged = {
    ...script,
    generated_at: nowIso(),
    observed_ms: {
      ...(script.observed_ms || {}),
      ...observed
    }
  };
  return merged;
}

async function main() {
  const options = parseArgs(process.argv.slice(2));
  await ensureOutputPaths(options);
  const scenarioDefinition = await loadScenarioDefinition(options.scenarioFile);
  const baseScript = buildScenarioScript(options, scenarioDefinition);

  if (options.mode === "validate") {
    await runPhase({
      phaseName: "phase_validate",
      options,
      script: baseScript,
      scenarioDefinition,
      record: false
    });
    process.stdout.write("[demo] Validate phase complete.\n");
    process.stdout.write("[demo] Done.\n");
    return;
  }

  if (options.mode === "plan" || options.mode === "all") {
    const result = await runPhase({
      phaseName: "phase_plan",
      options,
      script: baseScript,
      scenarioDefinition,
      record: false
    });
    const planned = mergeObservedScript(baseScript, result.observed);
    await fsp.writeFile(options.scriptFile, `${JSON.stringify(planned, null, 2)}\n`, "utf-8");
    process.stdout.write(`[demo] Plan phase complete. Script written: ${options.scriptFile}\n`);
  }

  if (options.mode === "record" || options.mode === "all") {
    if (!fs.existsSync(options.scriptFile)) {
      throw new Error(`Script file missing for record phase: ${options.scriptFile}`);
    }
    const script = JSON.parse(await fsp.readFile(options.scriptFile, "utf-8"));
    const result = await runPhase({
      phaseName: "phase_record",
      options,
      script,
      scenarioDefinition,
      record: true
    });
    if (!result.phaseVideoFile || !fs.existsSync(result.phaseVideoFile)) {
      throw new Error("Record phase finished but output video was not produced.");
    }
    await fsp.copyFile(result.phaseVideoFile, options.videoFile);
    process.stdout.write(`[demo] Record phase complete. Video written: ${options.videoFile}\n`);
  }

  process.stdout.write("[demo] Done.\n");
}

main().catch((error) => {
  process.stderr.write(`\n[demo] ERROR: ${error.message}\n`);
  process.exit(1);
});
