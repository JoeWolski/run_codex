from __future__ import annotations

import asyncio
import base64
import codecs
import fcntl
import hashlib
import html
import hmac
import json
import logging
import mimetypes
import os
import queue
import re
import secrets
import signal
import struct
import subprocess
import shutil
import sys
import tempfile
import termios
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path, PurePosixPath
from string import Template
from threading import Lock, Thread, current_thread
from typing import Any, Callable

import click
import uvicorn
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles


STATE_FILE_NAME = "state.json"
AGENT_CAPABILITIES_CACHE_FILE_NAME = "agent_capabilities_cache.json"
SECRETS_DIR_NAME = "secrets"
OPENAI_CREDENTIALS_FILE_NAME = "openai.env"
OPENAI_CODEX_AUTH_FILE_NAME = "auth.json"
GITHUB_APP_INSTALLATION_FILE_NAME = "github_app_installation.json"
GITHUB_PERSONAL_ACCESS_TOKEN_FILE_NAME = "github_personal_access_token.json"
GITHUB_GIT_CREDENTIALS_FILE_NAME = "github_credentials"
GITHUB_APP_SETTINGS_FILE_NAME = "github_app_settings.json"
GITHUB_APP_ID_ENV = "AGENT_HUB_GITHUB_APP_ID"
GITHUB_APP_PRIVATE_KEY_ENV = "AGENT_HUB_GITHUB_APP_PRIVATE_KEY"
GITHUB_APP_PRIVATE_KEY_FILE_ENV = "AGENT_HUB_GITHUB_APP_PRIVATE_KEY_FILE"
GITHUB_APP_SLUG_ENV = "AGENT_HUB_GITHUB_APP_SLUG"
GITHUB_APP_WEB_BASE_URL_ENV = "AGENT_HUB_GITHUB_WEB_BASE_URL"
GITHUB_APP_API_BASE_URL_ENV = "AGENT_HUB_GITHUB_API_BASE_URL"
GITHUB_APP_DEFAULT_WEB_BASE_URL = "https://github.com"
GITHUB_APP_DEFAULT_API_BASE_URL = "https://api.github.com"
SYSTEM_PROMPT_FILE_NAME = "SYSTEM_PROMPT.md"
GITHUB_APP_JWT_LIFETIME_SECONDS = 9 * 60
GITHUB_APP_TOKEN_REFRESH_SKEW_SECONDS = 120
GITHUB_APP_API_TIMEOUT_SECONDS = 8.0
GITHUB_APP_PRIVATE_KEY_MAX_CHARS = 256_000
GITHUB_APP_SETUP_SESSION_LIFETIME_SECONDS = 60 * 60
GITHUB_APP_DEFAULT_NAME = "Agent Hub"
GITHUB_CONNECTION_MODE_GITHUB_APP = "github_app"
GITHUB_CONNECTION_MODE_PERSONAL_ACCESS_TOKEN = "personal_access_token"
GITHUB_PERSONAL_ACCESS_TOKEN_MIN_CHARS = 20
GITHUB_PERSONAL_ACCESS_TOKEN_ID_MAX_CHARS = 120
GITHUB_OWNER_SCOPE_MAX_ITEMS = 64
DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 8765
DEFAULT_CONTAINER_HOME = "/workspace"
ARTIFACT_PUBLISH_BASE_URL_ENV = "AGENT_HUB_ARTIFACT_BASE_URL"
DEFAULT_ARTIFACT_PUBLISH_HOST = "host.docker.internal"
TERMINAL_QUEUE_MAX = 256
HUB_EVENT_QUEUE_MAX = 512
OPENAI_ACCOUNT_LOGIN_LOG_MAX_CHARS = 16_000
OPENAI_ACCOUNT_LOGIN_DEFAULT_CALLBACK_PORT = 1455
DEFAULT_AGENT_IMAGE = "agent-ubuntu2204-codex:latest"
AGENT_TYPE_CODEX = "codex"
AGENT_TYPE_CLAUDE = "claude"
AGENT_TYPE_GEMINI = "gemini"
DEFAULT_CHAT_AGENT_TYPE = AGENT_TYPE_CODEX
SUPPORTED_CHAT_AGENT_TYPES = {AGENT_TYPE_CODEX, AGENT_TYPE_CLAUDE, AGENT_TYPE_GEMINI}
AGENT_COMMAND_BY_TYPE = {
    AGENT_TYPE_CODEX: "codex",
    AGENT_TYPE_CLAUDE: "claude",
    AGENT_TYPE_GEMINI: "gemini",
}
AGENT_LABEL_BY_TYPE = {
    AGENT_TYPE_CODEX: "Codex",
    AGENT_TYPE_CLAUDE: "Claude",
    AGENT_TYPE_GEMINI: "Gemini CLI",
}
AGENT_CAPABILITY_DEFAULT_MODELS_BY_TYPE = {
    AGENT_TYPE_CODEX: ["default", "gpt-5.3-codex", "gpt-5.3-codex-spark"],
    AGENT_TYPE_CLAUDE: ["default", "sonnet", "opus", "haiku"],
    AGENT_TYPE_GEMINI: ["default"],
}
AGENT_CAPABILITY_DEFAULT_REASONING_BY_TYPE = {
    AGENT_TYPE_CODEX: ["default", "minimal", "low", "medium", "high", "xhigh"],
    AGENT_TYPE_CLAUDE: ["default"],
    AGENT_TYPE_GEMINI: ["default"],
}
AGENT_CAPABILITY_MODEL_TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{1,120}")
AGENT_CAPABILITY_CODEX_MODEL_TOKEN_RE = re.compile(r"^(?:gpt-[a-z0-9][a-z0-9._-]*|o[0-9][a-z0-9._-]*)$")
AGENT_CAPABILITY_REASONING_LEVELS = ("minimal", "low", "medium", "high", "xhigh")
AGENT_CAPABILITY_REASONING_VALUE_RE = re.compile(r"\b(?:minimal|low|medium|high|xhigh)\b")
AGENT_CAPABILITY_REASONING_LIST_RE = re.compile(
    r"(?:\bsupported\b|\bavailable\b)?\s*\breasoning(?:\s+effort)?\s+modes?\b\s*[:=-]\s*([^\n\r]+)",
    re.IGNORECASE,
)
AGENT_CAPABILITY_DISCOVERY_TIMEOUT_SECONDS = float(
    os.environ.get("AGENT_HUB_AGENT_CAPABILITY_DISCOVERY_TIMEOUT_SECONDS", "8.0")
)
AGENT_CAPABILITY_DISCOVERY_COMMANDS_BY_TYPE = {
    AGENT_TYPE_CODEX: (
        ("codex", "models", "--json"),
        ("codex", "models"),
        ("codex", "--help"),
    ),
    AGENT_TYPE_CLAUDE: (
        ("claude", "models", "--json"),
        ("claude", "models"),
        ("claude", "--help"),
    ),
    AGENT_TYPE_GEMINI: (
        ("gemini", "models", "--json"),
        ("gemini", "models"),
        ("gemini", "--help"),
    ),
}
DEFAULT_PTY_COLS = 160
DEFAULT_PTY_ROWS = 48
CHAT_PREVIEW_LOG_MAX_BYTES = 150_000
CHAT_TITLE_MAX_CHARS = 80
CHAT_SUBTITLE_MAX_CHARS = 240
CHAT_SUBTITLE_MARKERS = (".", "•", "◦", "∙", "·", "●", "○", "▪", "▫", "‣", "⁃")
CHAT_DEFAULT_NAME = "New Chat"
CHAT_AUTOGENERATED_NAME_RE = re.compile(r"^chat-[0-9a-f]{8}$", re.IGNORECASE)
CHAT_TITLE_API_TIMEOUT_SECONDS = 8.0
CHAT_TITLE_CODEX_TIMEOUT_SECONDS = 25.0
CHAT_TITLE_OPENAI_MODEL = os.environ.get("AGENT_HUB_CHAT_TITLE_MODEL", "gpt-4.1-mini")
CHAT_TITLE_ACCOUNT_MODEL = "chatgpt-account"
CHAT_TITLE_AUTH_MODE_ACCOUNT = "chatgpt_account"
CHAT_TITLE_AUTH_MODE_API_KEY = "api_key"
CHAT_TITLE_AUTH_MODE_NONE = "none"
CHAT_TITLE_NO_CREDENTIALS_ERROR = (
    "No OpenAI credentials configured for chat title generation. Connect an OpenAI account or API key in Settings."
)
CHAT_ARTIFACTS_MAX_ITEMS = 200
CHAT_ARTIFACT_PROMPT_HISTORY_MAX_ITEMS = 64
CHAT_ARTIFACT_PROMPT_LABEL_MAX_CHARS = 2000
CHAT_ARTIFACT_NAME_MAX_CHARS = 180
CHAT_ARTIFACT_PATH_MAX_CHARS = 1024
AUTO_CONFIG_CHAT_TIMEOUT_SECONDS = float(os.environ.get("AGENT_HUB_AUTO_CONFIG_TIMEOUT_SECONDS", "240"))
AUTO_CONFIG_MODEL = "chatgpt-account-codex"
AUTO_CONFIG_NOT_CONNECTED_ERROR = (
    "Auto configure needs a connected ChatGPT account in Settings to run a temporary repository analysis chat."
)
AUTO_CONFIG_MISSING_OUTPUT_ERROR = "Temporary auto-config chat did not return a JSON recommendation."
AUTO_CONFIG_INVALID_OUTPUT_ERROR = "Temporary auto-config chat returned invalid JSON."
AUTO_CONFIG_NOTES_MAX_CHARS = 400
AUTO_CONFIG_REPO_DOCKERFILE_MIN_SCORE = 70
AUTO_CONFIG_CACHE_SIGNAL_MAX_FILES = 3000
AUTO_CONFIG_CACHE_SIGNAL_IGNORED_DIRS = {
    ".git",
    ".hg",
    ".svn",
    "__pycache__",
    ".venv",
    "venv",
    "node_modules",
    "build",
    "dist",
    "out",
    "target",
}
AUTO_CONFIG_CACHE_SIGNAL_IGNORED_PATH_PARTS = {
    "test",
    "tests",
    "__tests__",
    "testing",
    "spec",
    "specs",
    "fixture",
    "fixtures",
}
AUTO_CONFIG_CACHE_SIGNAL_DOC_DIRS = {"docs", "doc", "documentation"}
AUTO_CONFIG_CACHE_SIGNAL_FILENAMES = {
    "cmakelists.txt",
    "meson.build",
    "meson.options",
    "makefile",
    "gnu makefile",
    "build.bazel",
    "workspace",
    ".bazelrc",
    "cargo.toml",
    "cargo.config",
    "config.toml",
    "dockerfile",
    "sconstruct",
    "sconscript",
}
AUTO_CONFIG_CACHE_SIGNAL_SUFFIXES = {
    ".cmake",
    ".mk",
    ".ninja",
    ".bazel",
    ".bzl",
    ".toml",
    ".sh",
    ".bash",
    ".zsh",
    ".ps1",
    ".py",
    ".yaml",
    ".yml",
    ".json",
    ".cfg",
    ".conf",
    ".ini",
}
AUTO_CONFIG_CCACHE_SIGNAL_PATTERNS = (
    re.compile(r"\bCMAKE_[A-Z0-9_]*COMPILER_LAUNCHER\b[^\n#]*\bccache\b", re.IGNORECASE),
    re.compile(
        r"(?:^|[;&|]\s*)(?:[A-Za-z0-9_./-]+/)?ccache\s+(?:--|[A-Za-z0-9_./-])",
        re.IGNORECASE | re.MULTILINE,
    ),
    re.compile(r"\b(?:export\s+)?CCACHE_[A-Z0-9_]+\s*(?:=|:)"),
    re.compile(r"\b(?:export\s+)?(?:CC|CXX)\s*=\s*(?:\"|')?ccache\b", re.IGNORECASE),
)
AUTO_CONFIG_SCCACHE_SIGNAL_PATTERNS = (
    re.compile(r"\bCMAKE_[A-Z0-9_]*COMPILER_LAUNCHER\b[^\n#]*\bsccache\b", re.IGNORECASE),
    re.compile(
        r"(?:^|[;&|]\s*)(?:[A-Za-z0-9_./-]+/)?sccache\s+(?:--|[A-Za-z0-9_./-])",
        re.IGNORECASE | re.MULTILINE,
    ),
    re.compile(r"\b(?:export\s+)?SCCACHE_[A-Z0-9_]+\s*(?:=|:)"),
    re.compile(r"\bRUSTC_WRAPPER\s*=\s*(?:\"|')?sccache\b", re.IGNORECASE),
)
AUTO_CONFIG_SETUP_CHAIN_SPLIT_RE = re.compile(r"\s*&&\s*")
AUTO_CONFIG_SETUP_CD_RE = re.compile(r"^cd\s+([^\s;&|]+)$", re.IGNORECASE)
AUTO_CONFIG_SETUP_CWD_RE = re.compile(
    r"(?:^|\s)--cwd\s+([^\s\"']+|\"[^\"]+\"|'[^']+')",
    re.IGNORECASE,
)
AUTO_CONFIG_SETUP_PREFIX_RE = re.compile(
    r"(?:^|\s)--prefix\s+([^\s\"']+|\"[^\"]+\"|'[^']+')",
    re.IGNORECASE,
)
AUTO_CONFIG_SETUP_UV_SYNC_RE = re.compile(r"^uv\s+sync\b", re.IGNORECASE)
AUTO_CONFIG_SETUP_YARN_INSTALL_RE = re.compile(r"^(?:corepack\s+)?yarn\s+install\b", re.IGNORECASE)
AUTO_CONFIG_SETUP_NPM_CI_RE = re.compile(r"^npm\s+ci\b", re.IGNORECASE)
AUTO_CONFIG_DOCKER_SOCKET_PATHS = {"/var/run/docker.sock", "/run/docker.sock"}
PROMPTS_DIR_NAME = "prompts"
PROMPT_CHAT_TITLE_OPENAI_SYSTEM_FILE = "chat_title_openai_system.md"
PROMPT_CHAT_TITLE_OPENAI_USER_FILE = "chat_title_openai_user.md"
PROMPT_CHAT_TITLE_CODEX_REQUEST_FILE = "chat_title_codex_request.md"
PROMPT_AUTO_CONFIGURE_PROJECT_FILE = "auto_configure_project.md"
ANSI_ESCAPE_RE = re.compile(
    r"\x1B(?:"
    r"[@-Z\\-_]"
    r"|\[[0-?]*[ -/]*[@-~]"
    r"|\][^\x1B\x07]*(?:\x07|\x1B\\)"
    r"|P[^\x1B\x07]*(?:\x07|\x1B\\)"
    r")"
)
TERMINAL_CONTROL_CHAR_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
LEADING_INVISIBLE_RE = re.compile(r"^[\u200b\u200c\u200d\u2060\ufeff\u200e\u200f]+")
ANSI_CURSOR_POSITION_RE = re.compile(r"\x1b\[[0-9;?]*[Hf]")
ANSI_ERASE_IN_LINE_RE = re.compile(r"\x1b\[[0-9;?]*K")
OSC_COLOR_RESPONSE_FRAGMENT_RE = re.compile(
    r"(?:^|\s)\]?\d{1,3};(?:rgb|rgba):[0-9a-f]{2,4}/[0-9a-f]{2,4}/[0-9a-f]{2,4}",
    re.IGNORECASE,
)
RESERVED_ENV_VAR_KEYS = {
    "OPENAI_API_KEY",
    "AGENT_HUB_GIT_USER_NAME",
    "AGENT_HUB_GIT_USER_EMAIL",
}
HUB_LOG_LEVEL_CHOICES = ("critical", "error", "warning", "info", "debug")
GITHUB_APP_PRIVATE_KEY_BEGIN_MARKERS = {
    "-----BEGIN RSA PRIVATE KEY-----",
    "-----BEGIN PRIVATE KEY-----",
}
GITHUB_APP_PRIVATE_KEY_END_MARKERS = {
    "-----END RSA PRIVATE KEY-----",
    "-----END PRIVATE KEY-----",
}

EVENT_TYPE_SNAPSHOT = "snapshot"
EVENT_TYPE_STATE_CHANGED = "state_changed"
EVENT_TYPE_AUTH_CHANGED = "auth_changed"
EVENT_TYPE_OPENAI_ACCOUNT_SESSION = "openai_account_session"
EVENT_TYPE_PROJECT_BUILD_LOG = "project_build_log"
EVENT_TYPE_AGENT_CAPABILITIES_CHANGED = "agent_capabilities_changed"

LOGGER = logging.getLogger("agent_hub")
LOGGER.addHandler(logging.NullHandler())


@dataclass
class ChatRuntime:
    process: subprocess.Popen
    master_fd: int
    listeners: set[queue.Queue[str | None]] = field(default_factory=set)


@dataclass
class OpenAIAccountLoginSession:
    id: str
    process: subprocess.Popen[str]
    container_name: str
    started_at: str
    method: str = "browser_callback"
    status: str = "starting"
    login_url: str = ""
    device_code: str = ""
    local_callback_url: str = ""
    callback_port: int = OPENAI_ACCOUNT_LOGIN_DEFAULT_CALLBACK_PORT
    callback_path: str = "/auth/callback"
    log_tail: str = ""
    exit_code: int | None = None
    completed_at: str = ""
    error: str = ""


@dataclass(frozen=True)
class GithubAppSettings:
    app_id: str
    app_slug: str
    private_key: str
    web_base_url: str
    api_base_url: str


@dataclass
class GithubAppSetupSession:
    id: str
    state: str
    status: str
    form_action: str
    manifest: dict[str, Any]
    callback_url: str
    web_base_url: str
    api_base_url: str
    started_at: str
    expires_at: str
    completed_at: str = ""
    error: str = ""
    app_id: str = ""
    app_slug: str = ""


def _repo_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / "pyproject.toml").exists():
            return parent
    return Path(__file__).resolve().parents[3]


def _prompts_dir() -> Path:
    return Path(__file__).resolve().parent / PROMPTS_DIR_NAME


@lru_cache(maxsize=16)
def _load_prompt_template(prompt_file_name: str) -> str:
    file_name = str(prompt_file_name or "").strip()
    if not file_name:
        raise RuntimeError("Prompt template filename is required.")
    path = _prompts_dir() / file_name
    try:
        template_text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise RuntimeError(f"Prompt template not found: {path}") from exc
    normalized = template_text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not normalized:
        raise RuntimeError(f"Prompt template is empty: {path}")
    return normalized


def _render_prompt_template(prompt_file_name: str, **values: Any) -> str:
    template = Template(_load_prompt_template(prompt_file_name))
    try:
        return template.substitute({key: str(value) for key, value in values.items()})
    except KeyError as exc:
        placeholder = str(exc.args[0] if exc.args else "")
        raise RuntimeError(
            f"Prompt template '{prompt_file_name}' is missing value for placeholder '{placeholder}'."
        ) from exc


def _default_data_dir() -> Path:
    return Path.home() / ".local" / "share" / "agent-hub"


def _default_config_file() -> Path:
    config_file = _repo_root() / "config" / "agent.config.toml"
    if config_file.exists():
        return config_file

    fallback = Path.cwd() / "config" / "agent.config.toml"
    if fallback.exists():
        return fallback

    return config_file


def _default_system_prompt_file() -> Path:
    system_prompt_file = _repo_root() / SYSTEM_PROMPT_FILE_NAME
    if system_prompt_file.exists():
        return system_prompt_file

    fallback = Path.cwd() / SYSTEM_PROMPT_FILE_NAME
    if fallback.exists():
        return fallback

    return system_prompt_file


def _frontend_dist_dir() -> Path:
    return _repo_root() / "web" / "dist"


def _frontend_index_file() -> Path:
    return _frontend_dist_dir() / "index.html"


def _normalize_log_level(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in HUB_LOG_LEVEL_CHOICES:
        return normalized
    return "info"


def _normalize_chat_agent_type(raw_value: Any, *, strict: bool = False) -> str:
    value = str(raw_value or "").strip().lower()
    if value in SUPPORTED_CHAT_AGENT_TYPES:
        return value
    if strict:
        supported = ", ".join(sorted(SUPPORTED_CHAT_AGENT_TYPES))
        raise HTTPException(status_code=400, detail=f"agent_type must be one of: {supported}.")
    return DEFAULT_CHAT_AGENT_TYPE


def _configure_hub_logging(level: str) -> None:
    normalized = _normalize_log_level(level)
    handler = logging.StreamHandler(sys.__stderr__)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    LOGGER.handlers.clear()
    LOGGER.addHandler(handler)
    LOGGER.setLevel(getattr(logging, normalized.upper(), logging.INFO))
    LOGGER.propagate = False


def _uvicorn_log_level(hub_level: str) -> str:
    normalized = _normalize_log_level(hub_level)
    if normalized == "debug":
        return "info"
    return normalized


def _default_artifact_publish_base_url(hub_port: int) -> str:
    return f"http://{DEFAULT_ARTIFACT_PUBLISH_HOST}:{int(hub_port or DEFAULT_PORT)}"


def _resolve_artifact_publish_base_url(value: Any, hub_port: int) -> str:
    raw_value = str(value or os.environ.get(ARTIFACT_PUBLISH_BASE_URL_ENV, "")).strip()
    if not raw_value:
        return _default_artifact_publish_base_url(hub_port)

    parsed = urllib.parse.urlsplit(raw_value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(
            "Invalid artifact publish base URL. "
            "Expected an absolute http(s) URL reachable from agent_cli containers."
        )
    normalized_path = parsed.path.rstrip("/")
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, normalized_path, "", ""))


def _run_cli_command(cmd: list[str], cwd: Path | None = None, env: dict[str, str] | None = None) -> None:
    result = subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        check=False,
        text=True,
        capture_output=True,
        env=env,
    )
    if result.returncode == 0:
        return
    message = ((result.stdout or "") + (result.stderr or "")).strip()
    if not message:
        message = f"Command failed ({cmd[0]}) with exit code {result.returncode}"
    raise click.ClickException(message)


def _latest_mtime(path: Path) -> float:
    if not path.exists():
        return 0.0
    if path.is_file():
        return path.stat().st_mtime
    newest = 0.0
    for file_path in path.rglob("*"):
        if file_path.is_file():
            newest = max(newest, file_path.stat().st_mtime)
    return newest


def _frontend_needs_build(frontend_dir: Path, dist_dir: Path) -> bool:
    index_file = dist_dir / "index.html"
    if not index_file.is_file():
        return True

    dist_mtime = _latest_mtime(dist_dir)
    tracked_sources = [
        frontend_dir / "index.html",
        frontend_dir / "package.json",
        frontend_dir / "yarn.lock",
        frontend_dir / "vite.config.js",
    ]
    for file_path in tracked_sources:
        if file_path.exists() and file_path.stat().st_mtime > dist_mtime:
            return True

    src_dir = frontend_dir / "src"
    if src_dir.exists() and _latest_mtime(src_dir) > dist_mtime:
        return True

    return False


def _ensure_frontend_built(data_dir: Path) -> None:
    frontend_dir = _repo_root() / "web"
    dist_dir = frontend_dir / "dist"

    if not frontend_dir.is_dir():
        raise click.ClickException(f"Missing frontend directory: {frontend_dir}")

    if not _frontend_needs_build(frontend_dir, dist_dir):
        return

    if shutil.which("node") is None:
        raise click.ClickException("node is required to build the frontend, but was not found in PATH.")
    if shutil.which("corepack") is None:
        raise click.ClickException("corepack is required to run Yarn, but was not found in PATH.")

    env = dict(os.environ)
    env.setdefault("COREPACK_HOME", str(data_dir / ".corepack"))

    _run_cli_command(["corepack", "yarn", "install"], cwd=frontend_dir, env=env)
    _run_cli_command(["corepack", "yarn", "build"], cwd=frontend_dir, env=env)


def _frontend_not_built_page() -> str:
    return """
<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Agent Hub Frontend Missing</title>
  <style>
    body { font-family: ui-sans-serif, system-ui, sans-serif; margin: 2rem; color: #111827; }
    pre { padding: 0.75rem; border: 1px solid #d1d5db; border-radius: 8px; background: #f9fafb; }
  </style>
</head>
<body>
  <h1>Agent Hub frontend is not built</h1>
  <p>Build the React frontend using Yarn, then restart the backend.</p>
  <pre>cd web
yarn install
yarn build</pre>
</body>
</html>
    """


def _github_app_setup_callback_page(success: bool, message: str, app_slug: str = "") -> str:
    status_text = "connected" if success else "failed"
    status_class = "ok" if success else "error"
    title_text = "GitHub Connected" if success else "GitHub Connection Failed"
    escaped_message = html.escape(message or "")
    escaped_slug = html.escape(app_slug or "")
    slug_line = f"<p class=\"meta\">App slug: <code>{escaped_slug}</code></p>" if escaped_slug else ""
    return f"""
<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{title_text}</title>
  <style>
    body {{
      font-family: ui-sans-serif, system-ui, sans-serif;
      margin: 0;
      background: #0f172a;
      color: #e2e8f0;
      min-height: 100vh;
      display: flex;
      align-items: center;
      justify-content: center;
      padding: 1rem;
    }}
    .panel {{
      width: min(560px, 100%);
      border: 1px solid #1e293b;
      border-radius: 12px;
      background: #111827;
      padding: 1.25rem;
      box-shadow: 0 12px 30px rgba(15, 23, 42, 0.4);
    }}
    .status {{
      display: inline-block;
      font-size: 0.82rem;
      text-transform: uppercase;
      letter-spacing: 0.08em;
      padding: 0.2rem 0.5rem;
      border-radius: 999px;
      margin-bottom: 0.75rem;
    }}
    .status.ok {{
      background: rgba(34, 197, 94, 0.2);
      color: #86efac;
    }}
    .status.error {{
      background: rgba(239, 68, 68, 0.2);
      color: #fca5a5;
    }}
    p {{
      margin: 0.5rem 0 0;
      line-height: 1.45;
    }}
    .meta {{
      color: #94a3b8;
      font-size: 0.95rem;
    }}
    .actions {{
      margin-top: 1rem;
      display: flex;
      gap: 0.5rem;
      flex-wrap: wrap;
    }}
    a, button {{
      border: 1px solid #334155;
      border-radius: 8px;
      background: #1e293b;
      color: #e2e8f0;
      padding: 0.5rem 0.9rem;
      text-decoration: none;
      cursor: pointer;
      font: inherit;
    }}
    a:hover, button:hover {{
      border-color: #475569;
    }}
  </style>
</head>
<body>
  <section class="panel">
    <div class="status {status_class}">{status_text}</div>
    <h1>{title_text}</h1>
    <p>{escaped_message}</p>
    {slug_line}
    <div class="actions">
      <a href="/">Return to Agent Hub</a>
      <button type="button" onclick="window.close()">Close window</button>
    </div>
  </section>
</body>
</html>
    """


def _run(
    cmd: list[str],
    cwd: Path | None = None,
    capture: bool = False,
    check: bool = True,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess:
    resolved_env: dict[str, str] | None = None
    if env:
        resolved_env = dict(os.environ)
        for key, value in env.items():
            resolved_env[str(key)] = str(value)
    result = subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        check=False,
        text=True,
        capture_output=capture,
        env=resolved_env,
    )
    if check and result.returncode != 0:
        message = (result.stdout or "") + (result.stderr or "")
        raise HTTPException(status_code=400, detail=f"Command failed ({cmd[0]}): {message.strip()}")
    return result


def _run_logged(
    cmd: list[str],
    log_path: Path,
    cwd: Path | None = None,
    check: bool = True,
    on_output: Callable[[str], None] | None = None,
) -> subprocess.CompletedProcess:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8", errors="ignore") as log_file:
        start_line = f"$ {' '.join(cmd)}\n"
        log_file.write(start_line)
        log_file.flush()
        if on_output is not None:
            on_output(start_line)
        process = subprocess.Popen(
            cmd,
            cwd=str(cwd) if cwd else None,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            bufsize=1,
        )
        stdout = process.stdout
        if stdout is not None:
            for line in iter(stdout.readline, ""):
                if line == "":
                    break
                log_file.write(line)
                log_file.flush()
                if on_output is not None:
                    on_output(line)
            stdout.close()
        result = process.wait()
        log_file.write("\n")
        log_file.flush()
        if on_output is not None:
            on_output("\n")
    completed = subprocess.CompletedProcess(cmd, result, "", "")
    if check and completed.returncode != 0:
        raise HTTPException(status_code=400, detail=f"Command failed ({cmd[0]}) with exit code {completed.returncode}")
    return completed


def _run_for_repo(
    cmd: list[str],
    repo_dir: Path,
    capture: bool = False,
    check: bool = True,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess:
    return _run(["git", "-C", str(repo_dir), *cmd], capture=capture, check=check, env=env)


def _iso_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _iso_from_timestamp(timestamp: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(timestamp))


def _new_state() -> dict[str, Any]:
    return {"version": 1, "projects": {}, "chats": {}}


def _ordered_supported_agent_types() -> tuple[str, ...]:
    return (
        AGENT_TYPE_CODEX,
        AGENT_TYPE_CLAUDE,
        AGENT_TYPE_GEMINI,
    )


def _normalize_mode_options(raw_values: Any, fallback: list[str]) -> list[str]:
    values = list(fallback)
    if isinstance(raw_values, list):
        values = [str(item or "").strip() for item in raw_values]
    normalized: list[str] = []
    seen: set[str] = set()
    for raw_value in values:
        value = str(raw_value or "").strip().lower()
        if not value or value in seen:
            continue
        normalized.append(value)
        seen.add(value)
    if "default" in seen:
        normalized = ["default", *[item for item in normalized if item != "default"]]
    else:
        normalized = ["default", *normalized]
    if not normalized:
        return ["default"]
    return normalized


def _normalize_model_options_for_agent(agent_type: str, raw_values: Any, fallback: list[str]) -> list[str]:
    fallback_normalized = _normalize_mode_options(fallback, ["default"])
    candidate_values = _normalize_mode_options(raw_values, fallback_normalized)
    filtered = ["default"]
    seen = {"default"}
    for value in candidate_values:
        if value in seen:
            continue
        if value == "default":
            continue
        if not _token_is_model_candidate(agent_type, value):
            continue
        filtered.append(value)
        seen.add(value)
    if len(filtered) > 1:
        return filtered
    fallback_filtered = ["default"]
    seen = {"default"}
    for value in fallback_normalized:
        if value in seen:
            continue
        if value == "default":
            continue
        if not _token_is_model_candidate(agent_type, value):
            continue
        fallback_filtered.append(value)
        seen.add(value)
    if fallback_filtered:
        return fallback_filtered
    return ["default"]


def _normalize_reasoning_mode_options_for_agent(agent_type: str, raw_values: Any, fallback: list[str]) -> list[str]:
    fallback_normalized = _normalize_mode_options(fallback, ["default"])
    candidate_values = _normalize_mode_options(raw_values, fallback_normalized)
    candidate_levels = [value for value in candidate_values if value in AGENT_CAPABILITY_REASONING_LEVELS]

    # A single Codex reasoning level is usually parsed from default/help text, not a supported-modes list.
    if agent_type == AGENT_TYPE_CODEX and len(candidate_levels) == 1:
        fallback_levels = [value for value in fallback_normalized if value in AGENT_CAPABILITY_REASONING_LEVELS]
        if len(fallback_levels) >= 2:
            return ["default", *fallback_levels]

    if candidate_levels:
        return ["default", *candidate_levels]

    fallback_levels = [value for value in fallback_normalized if value in AGENT_CAPABILITY_REASONING_LEVELS]
    if fallback_levels:
        return ["default", *fallback_levels]
    return ["default"]


def _agent_capability_defaults_for_type(agent_type: str) -> dict[str, Any]:
    resolved_type = _normalize_chat_agent_type(agent_type)
    default_models = AGENT_CAPABILITY_DEFAULT_MODELS_BY_TYPE.get(
        resolved_type,
        AGENT_CAPABILITY_DEFAULT_MODELS_BY_TYPE[DEFAULT_CHAT_AGENT_TYPE],
    )
    default_reasoning = AGENT_CAPABILITY_DEFAULT_REASONING_BY_TYPE.get(
        resolved_type,
        AGENT_CAPABILITY_DEFAULT_REASONING_BY_TYPE[DEFAULT_CHAT_AGENT_TYPE],
    )
    return {
        "agent_type": resolved_type,
        "label": AGENT_LABEL_BY_TYPE.get(resolved_type, resolved_type.title()),
        "models": _normalize_model_options_for_agent(resolved_type, default_models, ["default"]),
        "reasoning_modes": _normalize_reasoning_mode_options_for_agent(resolved_type, default_reasoning, ["default"]),
        "updated_at": "",
        "last_error": "",
    }


def _default_agent_capabilities_cache_payload() -> dict[str, Any]:
    agents = [_agent_capability_defaults_for_type(agent_type) for agent_type in _ordered_supported_agent_types()]
    return {
        "version": 1,
        "updated_at": "",
        "discovery_in_progress": False,
        "discovery_started_at": "",
        "discovery_finished_at": "",
        "agents": agents,
    }


def _normalize_agent_capabilities_payload(raw_payload: Any) -> dict[str, Any]:
    defaults = _default_agent_capabilities_cache_payload()
    if not isinstance(raw_payload, dict):
        return defaults

    raw_agents = raw_payload.get("agents")
    raw_agent_map: dict[str, dict[str, Any]] = {}
    if isinstance(raw_agents, list):
        for raw_agent in raw_agents:
            if not isinstance(raw_agent, dict):
                continue
            resolved_type = _normalize_chat_agent_type(raw_agent.get("agent_type"))
            raw_agent_map[resolved_type] = raw_agent

    normalized_agents: list[dict[str, Any]] = []
    for agent_type in _ordered_supported_agent_types():
        defaults_for_type = _agent_capability_defaults_for_type(agent_type)
        raw_agent = raw_agent_map.get(agent_type, {})
        label = str(raw_agent.get("label") or defaults_for_type["label"]).strip() or defaults_for_type["label"]
        models = _normalize_model_options_for_agent(agent_type, raw_agent.get("models"), defaults_for_type["models"])
        reasoning_modes = _normalize_reasoning_mode_options_for_agent(
            agent_type,
            raw_agent.get("reasoning_modes"),
            defaults_for_type["reasoning_modes"],
        )
        updated_at = str(raw_agent.get("updated_at") or raw_payload.get("updated_at") or "").strip()
        last_error = str(raw_agent.get("last_error") or "").strip()
        normalized_agents.append(
            {
                "agent_type": agent_type,
                "label": label,
                "models": models,
                "reasoning_modes": reasoning_modes,
                "updated_at": updated_at,
                "last_error": last_error,
            }
        )

    return {
        "version": 1,
        "updated_at": str(raw_payload.get("updated_at") or "").strip(),
        "discovery_in_progress": bool(raw_payload.get("discovery_in_progress")),
        "discovery_started_at": str(raw_payload.get("discovery_started_at") or "").strip(),
        "discovery_finished_at": str(raw_payload.get("discovery_finished_at") or "").strip(),
        "agents": normalized_agents,
    }


def _token_is_model_candidate(agent_type: str, token: str) -> bool:
    value = str(token or "").strip().lower()
    if not value or value == "default":
        return False
    if agent_type == AGENT_TYPE_CODEX:
        if value in {"codex", "codex-provided"}:
            return False
        return AGENT_CAPABILITY_CODEX_MODEL_TOKEN_RE.match(value) is not None
    if agent_type == AGENT_TYPE_CLAUDE:
        return (
            value.startswith("claude")
            or value.startswith("sonnet")
            or value.startswith("opus")
            or value.startswith("haiku")
            or value in {"sonnet", "opus", "haiku"}
        )
    if agent_type == AGENT_TYPE_GEMINI:
        return value.startswith("gemini")
    return False


def _extract_models_from_json_payload(payload: Any, agent_type: str) -> list[str]:
    discovered: list[str] = []
    seen: set[str] = set()

    def add(value: Any) -> None:
        token = str(value or "").strip().lower()
        if not _token_is_model_candidate(agent_type, token) or token in seen:
            return
        seen.add(token)
        discovered.append(token)

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for key in ("model", "name", "id"):
                if key in node:
                    add(node.get(key))
            if "models" in node:
                walk(node.get("models"))
            for value in node.values():
                if isinstance(value, (dict, list)):
                    walk(value)
            return
        if isinstance(node, list):
            for item in node:
                walk(item)
            return
        if isinstance(node, str):
            add(node)

    walk(payload)
    return discovered


def _extract_model_candidates_from_output(output_text: str, agent_type: str) -> list[str]:
    text = str(output_text or "").strip()
    if not text:
        return []
    try:
        payload = json.loads(text)
        if isinstance(payload, (dict, list)):
            parsed = _extract_models_from_json_payload(payload, agent_type)
            if parsed:
                return parsed
    except json.JSONDecodeError:
        pass

    seen: set[str] = set()
    discovered: list[str] = []
    for match in AGENT_CAPABILITY_MODEL_TOKEN_RE.findall(text):
        token = match.strip().lower()
        if not _token_is_model_candidate(agent_type, token):
            continue
        if token in seen:
            continue
        discovered.append(token)
        seen.add(token)
    return discovered


def _extract_reasoning_candidates_from_output(output_text: str) -> list[str]:
    text = str(output_text or "")
    if not text:
        return []
    discovered: list[str] = []
    seen: set[str] = set()

    def add_from_text(value: str) -> None:
        for token in AGENT_CAPABILITY_REASONING_VALUE_RE.findall(str(value or "").lower()):
            if token in seen:
                continue
            seen.add(token)
            discovered.append(token)

    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        payload = None
    if isinstance(payload, (dict, list)):
        keys_with_mode_lists = {
            "reasoning_modes",
            "supported_reasoning_modes",
            "supported_reasoning",
            "reasoning_mode_options",
        }

        def walk(node: Any) -> None:
            if isinstance(node, dict):
                for key, value in node.items():
                    normalized_key = str(key or "").strip().lower().replace("-", "_")
                    if normalized_key in keys_with_mode_lists:
                        if isinstance(value, list):
                            for item in value:
                                add_from_text(str(item or ""))
                        elif isinstance(value, str):
                            add_from_text(value)
                    if isinstance(value, (dict, list)):
                        walk(value)
                return
            if isinstance(node, list):
                for item in node:
                    if isinstance(item, (dict, list)):
                        walk(item)

        walk(payload)
        if discovered:
            return _normalize_mode_options(discovered, ["default"])

    for match in AGENT_CAPABILITY_REASONING_LIST_RE.finditer(text):
        add_from_text(match.group(1))
    if discovered:
        return _normalize_mode_options(discovered, ["default"])
    return []


def _run_agent_capability_probe(cmd: list[str], timeout_seconds: float) -> tuple[int, str]:
    try:
        result = subprocess.run(
            cmd,
            check=False,
            text=True,
            capture_output=True,
            timeout=max(1.0, float(timeout_seconds)),
        )
        output_text = f"{result.stdout or ''}\n{result.stderr or ''}".strip()
        return result.returncode, output_text
    except subprocess.TimeoutExpired as exc:
        output_text = f"{exc.stdout or ''}\n{exc.stderr or ''}".strip()
        return 124, output_text
    except FileNotFoundError:
        return 127, ""


def _read_openai_api_key(path: Path) -> str | None:
    if not path.exists():
        return None

    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None

    for line in text.splitlines():
        match = re.match(r"^\s*OPENAI_API_KEY\s*=\s*(.+?)\s*$", line)
        if not match:
            continue
        value = match.group(1).strip().strip('"').strip("'")
        if value:
            return value
    return None


def _mask_secret(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:6]}...{value[-4:]}"


def _normalize_openai_api_key(raw_value: Any) -> str:
    value = str(raw_value or "").strip()
    if not value:
        raise HTTPException(status_code=400, detail="api_key is required.")
    if any(ch.isspace() for ch in value):
        raise HTTPException(status_code=400, detail="OpenAI API key must not contain whitespace.")
    if len(value) < 20:
        raise HTTPException(status_code=400, detail="OpenAI API key appears too short.")
    return value


def _normalize_github_app_id(raw_value: Any) -> str:
    value = str(raw_value or "").strip()
    if not value:
        raise ValueError(f"{GITHUB_APP_ID_ENV} is required.")
    if not value.isdigit():
        raise ValueError(f"{GITHUB_APP_ID_ENV} must be numeric.")
    return value


def _normalize_github_app_slug(raw_value: Any) -> str:
    value = str(raw_value or "").strip().lower()
    if not value:
        raise ValueError(f"{GITHUB_APP_SLUG_ENV} is required.")
    if not re.fullmatch(r"[a-z0-9-]+", value):
        raise ValueError(f"{GITHUB_APP_SLUG_ENV} must contain only lowercase letters, numbers, and hyphens.")
    return value


def _normalize_github_app_private_key(raw_value: Any) -> str:
    value = str(raw_value or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not value:
        raise ValueError("GitHub App private key is required.")
    if "\x00" in value:
        raise ValueError("GitHub App private key contains invalid binary data.")
    if len(value) > GITHUB_APP_PRIVATE_KEY_MAX_CHARS:
        raise ValueError("GitHub App private key is too large.")

    lines = [line.rstrip() for line in value.split("\n")]
    if not lines:
        raise ValueError("GitHub App private key is required.")
    begin_marker = lines[0].strip()
    end_marker = lines[-1].strip()
    if begin_marker not in GITHUB_APP_PRIVATE_KEY_BEGIN_MARKERS or end_marker not in GITHUB_APP_PRIVATE_KEY_END_MARKERS:
        raise ValueError("GitHub App private key must be a PEM key (BEGIN/END PRIVATE KEY).")
    if begin_marker.replace("BEGIN", "END") != end_marker:
        raise ValueError("GitHub App private key BEGIN/END markers do not match.")
    if len(lines) < 3:
        raise ValueError("GitHub App private key appears incomplete.")

    return "\n".join(lines) + "\n"


def _normalize_absolute_http_base_url(raw_value: Any, field_name: str) -> str:
    value = str(raw_value or "").strip()
    parsed = urllib.parse.urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"{field_name} must be an absolute http(s) URL.")
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", ""))


def _github_app_env_config_present() -> bool:
    return any(
        str(os.environ.get(name, "")).strip()
        for name in (
            GITHUB_APP_ID_ENV,
            GITHUB_APP_SLUG_ENV,
            GITHUB_APP_PRIVATE_KEY_ENV,
            GITHUB_APP_PRIVATE_KEY_FILE_ENV,
        )
    )


def _normalize_github_app_settings_payload(payload: dict[str, Any], source_name: str) -> GithubAppSettings:
    if not isinstance(payload, dict):
        raise ValueError(f"{source_name} must be a JSON object.")

    app_id_raw = payload.get("app_id")
    if app_id_raw is None:
        app_id_raw = payload.get("id")

    slug_raw = payload.get("app_slug")
    if slug_raw is None:
        slug_raw = payload.get("slug")

    key_raw = payload.get("private_key")
    if key_raw is None:
        key_raw = payload.get("pem")

    web_base_raw = payload.get("web_base_url")
    if web_base_raw is None or not str(web_base_raw).strip():
        web_base_raw = GITHUB_APP_DEFAULT_WEB_BASE_URL

    api_base_raw = payload.get("api_base_url")
    if api_base_raw is None or not str(api_base_raw).strip():
        api_base_raw = GITHUB_APP_DEFAULT_API_BASE_URL

    try:
        app_id = _normalize_github_app_id(app_id_raw)
        app_slug = _normalize_github_app_slug(slug_raw)
        private_key = _normalize_github_app_private_key(key_raw)
        web_base = _normalize_absolute_http_base_url(web_base_raw, "web_base_url")
        api_base = _normalize_absolute_http_base_url(api_base_raw, "api_base_url")
    except ValueError as exc:
        raise ValueError(f"{source_name}: {exc}") from exc

    return GithubAppSettings(
        app_id=app_id,
        app_slug=app_slug,
        private_key=private_key,
        web_base_url=web_base,
        api_base_url=api_base,
    )


def _load_github_app_settings_from_env() -> tuple[GithubAppSettings | None, str]:
    app_id_raw = str(os.environ.get(GITHUB_APP_ID_ENV, "")).strip()
    slug_raw = str(os.environ.get(GITHUB_APP_SLUG_ENV, "")).strip()
    key_raw = str(os.environ.get(GITHUB_APP_PRIVATE_KEY_ENV, "")).strip()
    key_file_raw = str(os.environ.get(GITHUB_APP_PRIVATE_KEY_FILE_ENV, "")).strip()

    if not app_id_raw and not slug_raw and not key_raw and not key_file_raw:
        return None, ""
    if bool(key_raw) and bool(key_file_raw):
        return None, (
            f"Set only one of {GITHUB_APP_PRIVATE_KEY_ENV} or {GITHUB_APP_PRIVATE_KEY_FILE_ENV}, not both."
        )

    if key_file_raw and not key_raw:
        key_path = Path(key_file_raw).expanduser()
        if not key_path.is_file():
            return None, f"{GITHUB_APP_PRIVATE_KEY_FILE_ENV} does not point to a readable file."
        try:
            key_raw = key_path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            return None, f"Failed to read {GITHUB_APP_PRIVATE_KEY_FILE_ENV}."

    try:
        settings = _normalize_github_app_settings_payload(
            {
                "app_id": app_id_raw,
                "app_slug": slug_raw,
                "private_key": key_raw,
                "web_base_url": str(
                    os.environ.get(GITHUB_APP_WEB_BASE_URL_ENV, GITHUB_APP_DEFAULT_WEB_BASE_URL)
                ).strip(),
                "api_base_url": str(
                    os.environ.get(GITHUB_APP_API_BASE_URL_ENV, GITHUB_APP_DEFAULT_API_BASE_URL)
                ).strip(),
            },
            "GitHub App environment variables",
        )
    except ValueError as exc:
        return None, str(exc)
    return settings, ""


def _load_github_app_settings_from_file(path: Path) -> tuple[GithubAppSettings | None, str]:
    if not path.exists():
        return None, ""
    payload = _read_json_if_exists(path)
    if payload is None:
        return None, f"Stored GitHub App settings file is invalid: {path}"
    try:
        settings = _normalize_github_app_settings_payload(payload, "Stored GitHub App settings")
    except ValueError as exc:
        return None, str(exc)
    return settings, ""


def _normalize_github_installation_id(raw_value: Any) -> int:
    value = str(raw_value or "").strip()
    if not value:
        raise HTTPException(status_code=400, detail="installation_id is required.")
    if not value.isdigit():
        raise HTTPException(status_code=400, detail="installation_id must be a positive integer.")
    installation_id = int(value)
    if installation_id <= 0:
        raise HTTPException(status_code=400, detail="installation_id must be a positive integer.")
    return installation_id


def _normalize_github_credential_host(raw_value: Any, field_name: str = "host") -> str:
    host = str(raw_value or "").strip().lower()
    if not host:
        raise HTTPException(status_code=400, detail=f"{field_name} is required.")
    if not re.fullmatch(r"[a-z0-9.-]+", host):
        raise HTTPException(status_code=400, detail=f"Invalid {field_name}: {raw_value}")
    return host


def _normalize_github_personal_access_token(raw_value: Any) -> str:
    token = str(raw_value or "").strip()
    if not token:
        raise HTTPException(status_code=400, detail="personal_access_token is required.")
    if any(ch.isspace() for ch in token):
        raise HTTPException(status_code=400, detail="personal_access_token must not contain whitespace.")
    if len(token) < GITHUB_PERSONAL_ACCESS_TOKEN_MIN_CHARS:
        raise HTTPException(status_code=400, detail="personal_access_token appears too short.")
    return token


def _normalize_github_owner_scope(raw_value: Any, field_name: str = "owner_scope") -> str:
    value = str(raw_value or "").strip().lower()
    if value.startswith("@"):
        value = value[1:].strip().lower()
    if not value:
        raise HTTPException(status_code=400, detail=f"{field_name} must not be empty.")
    if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,99}", value):
        raise HTTPException(status_code=400, detail=f"Invalid {field_name}: {raw_value}")
    return value


def _normalize_github_owner_scopes(raw_value: Any, field_name: str = "owner_scopes") -> list[str]:
    if raw_value is None:
        return []
    if isinstance(raw_value, str):
        candidates = re.split(r"[\s,]+", raw_value.strip())
    elif isinstance(raw_value, list):
        candidates = [str(item or "").strip() for item in raw_value]
    else:
        raise HTTPException(status_code=400, detail=f"{field_name} must be a string or array.")

    normalized: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        if not candidate:
            continue
        owner = _normalize_github_owner_scope(candidate, field_name=field_name)
        if owner in seen:
            continue
        normalized.append(owner)
        seen.add(owner)
        if len(normalized) >= GITHUB_OWNER_SCOPE_MAX_ITEMS:
            break
    return normalized


def _base64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _github_sign_rs256(private_key_pem: str, message: bytes) -> bytes:
    temp_key_path = Path("/tmp") / f".agent_hub_github_app_key_{uuid.uuid4().hex}.pem"
    _write_private_env_file(temp_key_path, private_key_pem)
    try:
        result = subprocess.run(
            ["openssl", "dgst", "-sha256", "-sign", str(temp_key_path)],
            input=message,
            capture_output=True,
            check=False,
        )
    finally:
        try:
            temp_key_path.unlink()
        except OSError:
            pass

    if result.returncode != 0:
        raise HTTPException(status_code=500, detail="Failed to sign GitHub App JWT with OpenSSL.")
    return result.stdout


def _github_app_jwt(settings: GithubAppSettings) -> str:
    now = int(time.time())
    payload = {
        "iat": now - 30,
        "exp": now + GITHUB_APP_JWT_LIFETIME_SECONDS,
        "iss": settings.app_id,
    }
    header_segment = _base64url_encode(json.dumps({"alg": "RS256", "typ": "JWT"}, separators=(",", ":")).encode("utf-8"))
    payload_segment = _base64url_encode(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    signing_input = f"{header_segment}.{payload_segment}".encode("ascii")
    signature_segment = _base64url_encode(_github_sign_rs256(settings.private_key, signing_input))
    return f"{header_segment}.{payload_segment}.{signature_segment}"


def _read_text_if_exists(path: Path) -> str:
    if not path.exists():
        return ""
    try:
        return path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""


def _read_json_if_exists(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8", errors="ignore"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    return payload


def _iso_to_unix_seconds(value: str) -> int:
    text = str(value or "").strip()
    if not text:
        return 0
    try:
        return int(datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp())
    except ValueError:
        return 0


def _github_api_error_message(body_text: str) -> str:
    text = str(body_text or "").strip()
    if not text:
        return ""
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return _short_summary(text, max_words=24, max_chars=200)
    if not isinstance(payload, dict):
        return ""
    message = str(payload.get("message") or "").strip()
    return _short_summary(message, max_words=24, max_chars=200) if message else ""


def _git_repo_host(repo_url: str) -> str:
    candidate = str(repo_url or "").strip()
    if not candidate:
        return ""

    parsed = urllib.parse.urlsplit(candidate)
    if parsed.hostname:
        return parsed.hostname.lower()

    scp_match = re.match(r"^[^@]+@([^:]+):", candidate)
    if scp_match:
        return scp_match.group(1).lower().strip()

    ssh_match = re.match(r"^ssh://[^@]+@([^/]+)/", candidate)
    if ssh_match:
        return ssh_match.group(1).lower().strip()

    return ""


def _git_repo_owner(repo_url: str) -> str:
    candidate = str(repo_url or "").strip()
    if not candidate:
        return ""

    parsed = urllib.parse.urlsplit(candidate)
    repo_path = ""
    if parsed.hostname:
        repo_path = str(parsed.path or "").strip()
    else:
        scp_match = re.match(r"^[^@]+@[^:]+:(.+)$", candidate)
        if scp_match:
            repo_path = str(scp_match.group(1) or "").strip()
        else:
            ssh_match = re.match(r"^ssh://[^@]+@[^/]+/(.+)$", candidate)
            if ssh_match:
                repo_path = str(ssh_match.group(1) or "").strip()
    if not repo_path:
        return ""

    parts = [part for part in repo_path.split("/") if part]
    if len(parts) < 2:
        return ""
    owner = str(parts[0] or "").strip().lower()
    if not owner:
        return ""
    return owner


def _openai_error_message(body_text: str) -> str:
    text = str(body_text or "").strip()
    if not text:
        return ""
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return _short_summary(text, max_words=20, max_chars=180)

    if not isinstance(payload, dict):
        return ""
    error = payload.get("error")
    if not isinstance(error, dict):
        return ""
    message = str(error.get("message") or "").strip()
    return _short_summary(message, max_words=30, max_chars=220) if message else ""


def _coerce_bool(value: Any, default: bool, field_name: str) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    if isinstance(value, (int, float)) and value in {0, 1}:
        return bool(value)
    raise HTTPException(status_code=400, detail=f"{field_name} must be a boolean.")


def _normalize_openai_account_login_method(raw_value: Any) -> str:
    value = str(raw_value or "").strip().lower()
    if not value:
        return "browser_callback"
    if value in {"browser_callback", "device_auth"}:
        return value
    raise HTTPException(status_code=400, detail="method must be 'browser_callback' or 'device_auth'.")


def _verify_openai_api_key(api_key: str, timeout_seconds: float = 8.0) -> None:
    request = urllib.request.Request(
        "https://api.openai.com/v1/models",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            status = int(response.getcode() or 0)
            body = response.read().decode("utf-8", errors="ignore")
    except urllib.error.HTTPError as exc:
        status = int(exc.code or 0)
        body = exc.read().decode("utf-8", errors="ignore")
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise HTTPException(
            status_code=502,
            detail="Failed to verify OpenAI API key due to a network error.",
        ) from exc

    if status == 200:
        return

    message = _openai_error_message(body)
    if status in {401, 403}:
        detail = "OpenAI rejected the API key."
        if message:
            detail = f"{detail} {message}"
        raise HTTPException(status_code=400, detail=detail)

    detail = f"OpenAI verification failed with status {status}."
    if message:
        detail = f"{detail} {message}"
    raise HTTPException(status_code=502, detail=detail)


def _write_private_env_file(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path.parent, 0o700)
    except OSError:
        pass

    tmp_path = path.parent / f".{path.name}.{uuid.uuid4().hex}.tmp"
    fd = os.open(str(tmp_path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fp:
            fp.write(content)
        os.replace(tmp_path, path)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    finally:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass


def _empty_list(v: Any) -> list[str]:
    if v is None:
        return []
    if not isinstance(v, list):
        raise HTTPException(status_code=400, detail="Expected an array.")
    parsed: list[str] = []
    for raw in v:
        if not isinstance(raw, str):
            raise HTTPException(status_code=400, detail="Entries must be strings.")
        parsed.append(raw.strip())
    return [item for item in parsed if item]


def _parse_mounts(entries: list[str], direction: str) -> list[str]:
    output: list[str] = []
    for entry in entries:
        if ":" not in entry:
            raise HTTPException(status_code=400, detail=f"Invalid {direction} mount '{entry}'.")
        host, container = entry.split(":", 1)
        host_path = Path(host).expanduser()
        if not host_path.exists():
            raise HTTPException(status_code=400, detail=f"Host path for {direction} mount does not exist: {host}")
        output.append(f"{host_path}:{container}")
    return output


def _parse_env_vars(entries: list[str]) -> list[str]:
    output: list[str] = []
    for entry in entries:
        if "=" not in entry:
            raise HTTPException(status_code=400, detail=f"Invalid environment variable '{entry}'. Expected KEY=VALUE.")
        key, value = entry.split("=", 1)
        key = key.strip()
        if not key:
            raise HTTPException(status_code=400, detail=f"Invalid environment variable '{entry}'. Empty key.")
        if any(ch.isspace() for ch in key):
            raise HTTPException(status_code=400, detail=f"Invalid environment variable key '{key}'.")
        if key.upper() in RESERVED_ENV_VAR_KEYS:
            raise HTTPException(
                status_code=400,
                detail=f"{key} is managed in Settings > Authentication and cannot be set manually.",
            )
        output.append(f"{key}={value}")
    return output


def _is_reserved_env_entry(entry: str) -> bool:
    if "=" not in entry:
        return False
    key = entry.split("=", 1)[0].strip().upper()
    return key in RESERVED_ENV_VAR_KEYS


def _docker_image_exists(tag: str) -> bool:
    result = subprocess.run(
        ["docker", "image", "inspect", tag],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return result.returncode == 0


def _normalize_base_image_mode(mode: Any) -> str:
    if mode is None:
        return "tag"
    normalized = str(mode).strip().lower()
    if normalized in {"tag", "repo_path"}:
        return normalized
    raise HTTPException(status_code=400, detail="base_image_mode must be 'tag' or 'repo_path'.")


def _extract_repo_name(repo_url: str) -> str:
    name = repo_url.rstrip("/").split(":")[-1].rsplit("/", 1)[-1]
    return name[:-4] if name.endswith(".git") else name


def _sanitize_workspace_component(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", str(value or "").strip())
    cleaned = cleaned.strip("._-")
    return cleaned or "project"


def _container_project_name(value: Any) -> str:
    return _sanitize_workspace_component(str(value or ""))


def _container_workspace_path_for_project(value: Any) -> str:
    return str(PurePosixPath(DEFAULT_CONTAINER_HOME) / _container_project_name(value))


def _short_summary(text: str, max_words: int = 10, max_chars: int = 80) -> str:
    words = [part for part in text.strip().split() if part]
    if not words:
        return ""
    summary = " ".join(words[:max_words])
    if len(summary) > max_chars:
        summary = summary[: max_chars - 1].rstrip() + "…"
    return summary


def _compact_whitespace(text: str) -> str:
    return " ".join(str(text or "").split())


def _strip_ansi_stream(carry: str, text: str) -> tuple[str, str]:
    source = f"{carry}{text}"
    if not source:
        return "", ""

    output: list[str] = []
    idx = 0
    length = len(source)
    while idx < length:
        char = source[idx]
        if char != "\x1b":
            output.append(char)
            idx += 1
            continue

        seq_start = idx
        idx += 1
        if idx >= length:
            return "".join(output), source[seq_start:]

        marker = source[idx]
        if marker == "[":
            idx += 1
            while idx < length:
                final = source[idx]
                if "@" <= final <= "~":
                    idx += 1
                    break
                idx += 1
            else:
                return "".join(output), source[seq_start:]
            continue

        if marker in {"]", "P"}:
            idx += 1
            terminated = False
            while idx < length:
                current = source[idx]
                if current == "\x07":
                    idx += 1
                    terminated = True
                    break
                if current == "\x1b":
                    if idx + 1 >= length:
                        return "".join(output), source[seq_start:]
                    if source[idx + 1] == "\\":
                        idx += 2
                        terminated = True
                        break
                idx += 1
            if not terminated:
                return "".join(output), source[seq_start:]
            continue

        idx += 1

    return "".join(output), ""


def _sanitize_submitted_prompt(prompt: Any) -> str:
    cleaned = _compact_whitespace(prompt).strip()
    if not cleaned:
        return ""
    cleaned = OSC_COLOR_RESPONSE_FRAGMENT_RE.sub(" ", cleaned)
    cleaned = _compact_whitespace(cleaned).strip(" ;")
    return cleaned


def _looks_like_terminal_control_payload(text: str) -> bool:
    value = _compact_whitespace(text).strip()
    if not value:
        return False
    lowered = value.lower()
    if re.match(r"^\]?\d{1,3};(?:rgb|rgba):[0-9a-f]{2,4}/[0-9a-f]{2,4}/[0-9a-f]{2,4}", lowered):
        return True
    if re.match(r"^\]?\d{1,3};", lowered) and "rgb:" in lowered:
        return True
    return False


def _truncate_title(text: str, max_chars: int) -> str:
    cleaned = _compact_whitespace(text).strip()
    if not cleaned or max_chars <= 0:
        return ""
    if len(cleaned) <= max_chars:
        return cleaned

    for delimiter in (" -- ", " - ", " | ", ": ", "; ", ". ", ", "):
        head = cleaned.split(delimiter, 1)[0].strip()
        if 12 <= len(head) <= max_chars:
            cleaned = head
            break
    if len(cleaned) <= max_chars:
        return cleaned

    words = cleaned.split()
    kept: list[str] = []
    for word in words:
        next_words = [*kept, word]
        joined = " ".join(next_words).strip()
        if len(joined) + 1 > max_chars:
            break
        kept.append(word)
    if kept:
        truncated = " ".join(kept).rstrip(" ,;:-")
        return f"{truncated}…" if len(truncated) < len(cleaned) else truncated

    if max_chars == 1:
        return "…"
    return cleaned[: max_chars - 1].rstrip() + "…"


def _chat_display_name(chat_name: Any) -> str:
    cleaned = _compact_whitespace(str(chat_name or "")).strip()
    if not cleaned:
        return CHAT_DEFAULT_NAME
    if CHAT_AUTOGENERATED_NAME_RE.fullmatch(cleaned):
        return CHAT_DEFAULT_NAME
    return cleaned


def _new_artifact_publish_token() -> str:
    return secrets.token_hex(24)


def _hash_artifact_publish_token(token: str) -> str:
    value = str(token or "").strip()
    if not value:
        return ""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _normalize_artifact_name(value: Any, fallback: str = "") -> str:
    candidate = _compact_whitespace(str(value or "")).strip()
    if not candidate:
        candidate = _compact_whitespace(str(fallback or "")).strip()
    if not candidate:
        candidate = "artifact"
    if len(candidate) > CHAT_ARTIFACT_NAME_MAX_CHARS:
        candidate = candidate[: CHAT_ARTIFACT_NAME_MAX_CHARS - 1].rstrip() + "…"
    return candidate


def _coerce_artifact_relative_path(value: Any) -> str:
    text = str(value or "").strip().replace("\\", "/")
    if not text or len(text) > CHAT_ARTIFACT_PATH_MAX_CHARS:
        return ""

    parts: list[str] = []
    for raw_part in text.split("/"):
        part = raw_part.strip()
        if not part or part == ".":
            continue
        if part == "..":
            return ""
        parts.append(part)
    if not parts:
        return ""
    return "/".join(parts)


def _normalize_chat_artifacts(raw_artifacts: Any) -> list[dict[str, Any]]:
    if not isinstance(raw_artifacts, list):
        return []

    entries: list[dict[str, Any]] = []
    for raw_artifact in raw_artifacts:
        if not isinstance(raw_artifact, dict):
            continue
        artifact_id = str(raw_artifact.get("id") or "").strip()
        relative_path = _coerce_artifact_relative_path(raw_artifact.get("relative_path"))
        if not artifact_id or not relative_path:
            continue
        size_raw = raw_artifact.get("size_bytes")
        try:
            size_bytes = int(size_raw)
        except (TypeError, ValueError):
            size_bytes = 0
        if size_bytes < 0:
            size_bytes = 0
        entries.append(
            {
                "id": artifact_id,
                "name": _normalize_artifact_name(raw_artifact.get("name"), fallback=Path(relative_path).name),
                "relative_path": relative_path,
                "size_bytes": size_bytes,
                "created_at": str(raw_artifact.get("created_at") or ""),
            }
        )
    return entries[-CHAT_ARTIFACTS_MAX_ITEMS:]


def _normalize_chat_current_artifact_ids(raw_ids: Any, artifacts: list[dict[str, Any]]) -> list[str]:
    if not isinstance(raw_ids, list):
        return []
    known_ids = {str(artifact.get("id") or "") for artifact in artifacts}
    normalized: list[str] = []
    for raw_id in raw_ids:
        artifact_id = str(raw_id or "").strip()
        if not artifact_id or artifact_id in normalized:
            continue
        if artifact_id not in known_ids:
            continue
        normalized.append(artifact_id)
    return normalized[-CHAT_ARTIFACTS_MAX_ITEMS:]


def _normalize_chat_artifact_prompt_history(raw_history: Any) -> list[dict[str, Any]]:
    if not isinstance(raw_history, list):
        return []
    entries: list[dict[str, Any]] = []
    for raw_entry in raw_history:
        if not isinstance(raw_entry, dict):
            continue
        prompt = _sanitize_submitted_prompt(raw_entry.get("prompt"))
        if not prompt:
            continue
        if len(prompt) > CHAT_ARTIFACT_PROMPT_LABEL_MAX_CHARS:
            prompt = prompt[:CHAT_ARTIFACT_PROMPT_LABEL_MAX_CHARS].rstrip()
        artifacts = _normalize_chat_artifacts(raw_entry.get("artifacts"))
        if not artifacts:
            continue
        entries.append(
            {
                "prompt": prompt,
                "archived_at": str(raw_entry.get("archived_at") or ""),
                "artifacts": artifacts,
            }
        )
    return entries[-CHAT_ARTIFACT_PROMPT_HISTORY_MAX_ITEMS:]


def _chat_preview_candidates_from_log(log_path: Path) -> tuple[list[str], list[str]]:
    lines = _chat_preview_lines_from_log(log_path)
    if not lines:
        return [], []

    user_candidates: list[str] = []
    assistant_candidates: list[str] = []
    for line in lines:
        line_clean = line.strip()
        if not line_clean:
            continue
        if line_clean.startswith(("›", ">", "You:")):
            normalized = line_clean.lstrip("›>").strip()
            if normalized.lower().startswith("you:"):
                normalized = normalized[4:].strip()
            if normalized:
                user_candidates.append(normalized)
            continue
        if line_clean.startswith("Tip:"):
            continue
        assistant_candidates.append(line_clean)
    return user_candidates, assistant_candidates


def _read_chat_log_preview(log_path: Path) -> str:
    if not log_path.exists():
        return ""
    with log_path.open("rb") as log_file:
        log_file.seek(0, os.SEEK_END)
        size = log_file.tell()
        start = size - CHAT_PREVIEW_LOG_MAX_BYTES if size > CHAT_PREVIEW_LOG_MAX_BYTES else 0
        log_file.seek(start)
        return log_file.read().decode("utf-8", errors="ignore")


def _sanitize_terminal_log_text(raw_text: str) -> str:
    text = str(raw_text or "")
    # Cursor jumps / erase-in-line updates are common in animated terminal output.
    # Treat them as logical line boundaries so adjacent frames do not collapse.
    text = ANSI_CURSOR_POSITION_RE.sub("\n", text)
    text = ANSI_ERASE_IN_LINE_RE.sub("\n", text)
    text, _ = _strip_ansi_stream("", text)
    # Preserve carriage-return boundaries from animated terminal updates.
    text = text.replace("\r", "\n")
    text = TERMINAL_CONTROL_CHAR_RE.sub("", text)
    text = OSC_COLOR_RESPONSE_FRAGMENT_RE.sub(" ", text)
    return text


def _chat_preview_lines_from_log(log_path: Path) -> list[str]:
    raw = _read_chat_log_preview(log_path)
    if not raw:
        return []
    text = _sanitize_terminal_log_text(raw)
    return [line.strip() for line in text.splitlines() if line.strip()]


def _openai_generate_chat_title(
    api_key: str,
    user_prompts: list[str],
    max_chars: int = CHAT_TITLE_MAX_CHARS,
    model: str = CHAT_TITLE_OPENAI_MODEL,
    timeout_seconds: float = CHAT_TITLE_API_TIMEOUT_SECONDS,
) -> str:
    prompts = _normalize_chat_prompt_history(user_prompts)
    if not api_key:
        raise RuntimeError("OpenAI API key is not configured for chat title generation.")
    if not prompts:
        raise RuntimeError("No submitted user prompts are available for chat title generation.")

    try:
        from openai import OpenAI
    except ImportError as exc:
        raise RuntimeError("OpenAI Python SDK is not installed. Add dependency 'openai>=1.0'.") from exc

    instructions = _render_prompt_template(
        PROMPT_CHAT_TITLE_OPENAI_SYSTEM_FILE,
        max_chars=max_chars,
    )
    prompt_lines = "\n".join(f"{index + 1}. {value}" for index, value in enumerate(prompts))
    user_prompt = _render_prompt_template(
        PROMPT_CHAT_TITLE_OPENAI_USER_FILE,
        prompt_lines=prompt_lines,
        max_chars=max_chars,
    )
    try:
        client = OpenAI(api_key=api_key, timeout=timeout_seconds)
        completion = client.chat.completions.create(
            model=model,
            temperature=0.2,
            max_tokens=64,
            messages=[
                {"role": "system", "content": instructions},
                {
                    "role": "user",
                    "content": user_prompt,
                },
            ],
        )
    except Exception as exc:
        raise RuntimeError(f"OpenAI chat title request failed: {exc}") from exc

    choices = getattr(completion, "choices", None)
    if not choices:
        raise RuntimeError("OpenAI returned no title choices.")
    first_choice = choices[0]
    message = getattr(first_choice, "message", None)
    content = getattr(message, "content", "") if message is not None else ""
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            text_value = getattr(item, "text", None)
            if text_value:
                parts.append(str(text_value))
        normalized = "".join(parts).strip()
    else:
        normalized = str(content or "").strip()
    if not normalized:
        raise RuntimeError("OpenAI returned an empty title.")
    first_line = normalized.splitlines()[0].strip().strip("\"'`")
    title = _truncate_title(first_line, max_chars)
    if not title:
        raise RuntimeError("OpenAI returned an invalid chat title.")
    return title


def _resolve_codex_executable(host_codex_dir: Path) -> str:
    bundled = host_codex_dir / "bin" / "codex"
    if bundled.is_file():
        return str(bundled)
    resolved = shutil.which("codex")
    if resolved:
        return resolved
    raise RuntimeError("Codex CLI is not installed. ChatGPT account title generation is unavailable.")


def _codex_exec_error_message(output_text: str) -> str:
    cleaned = ANSI_ESCAPE_RE.sub("", str(output_text or "")).replace("\r", "\n")
    lines = [line.strip() for line in cleaned.splitlines() if line.strip()]
    if not lines:
        return "Unknown error."

    for line in reversed(lines):
        if line.lower().startswith("error:"):
            detail = line.split(":", 1)[1].strip()
            if detail:
                return _short_summary(detail, max_words=30, max_chars=220)
    return _short_summary(lines[-1], max_words=30, max_chars=220)


def _codex_generate_chat_title(
    host_agent_home: Path,
    host_codex_dir: Path,
    user_prompts: list[str],
    max_chars: int = CHAT_TITLE_MAX_CHARS,
    timeout_seconds: float = CHAT_TITLE_CODEX_TIMEOUT_SECONDS,
) -> str:
    prompts = _normalize_chat_prompt_history(user_prompts)
    if not prompts:
        raise RuntimeError("No submitted user prompts are available for chat title generation.")

    codex_exec = _resolve_codex_executable(host_codex_dir)
    prompt_lines = "\n".join(f"{index + 1}. {value}" for index, value in enumerate(prompts))
    request_prompt = _render_prompt_template(
        PROMPT_CHAT_TITLE_CODEX_REQUEST_FILE,
        max_chars=max_chars,
        prompt_lines=prompt_lines,
    )
    output_file = host_codex_dir / f"title-summary-{uuid.uuid4().hex}.txt"

    env = os.environ.copy()
    env["HOME"] = str(host_agent_home)
    env["CODEX_HOME"] = str(host_codex_dir)

    cmd = [
        codex_exec,
        "exec",
        "--skip-git-repo-check",
        "--cd",
        str(_repo_root()),
        "--sandbox",
        "read-only",
        "--output-last-message",
        str(output_file),
        request_prompt,
    ]
    try:
        result = subprocess.run(
            cmd,
            check=False,
            text=True,
            capture_output=True,
            env=env,
            timeout=max(1.0, float(timeout_seconds)),
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("ChatGPT account title request timed out.") from exc

    output_text = ((result.stdout or "") + "\n" + (result.stderr or "")).strip()
    if result.returncode != 0:
        try:
            output_file.unlink()
        except OSError:
            pass
        detail = _codex_exec_error_message(output_text)
        raise RuntimeError(f"ChatGPT account title request failed: {detail}")

    try:
        raw_title = output_file.read_text(encoding="utf-8", errors="ignore").strip()
    except OSError as exc:
        raise RuntimeError("ChatGPT account title request returned no title output.") from exc
    finally:
        try:
            output_file.unlink()
        except OSError:
            pass

    if not raw_title:
        raise RuntimeError("ChatGPT account title request returned an empty title.")
    first_line = raw_title.splitlines()[0].strip().strip("\"'`")
    title = _truncate_title(first_line, max_chars)
    if not title:
        raise RuntimeError("ChatGPT account title request returned an invalid title.")
    return title


def _parse_json_object_from_text(raw_text: Any) -> dict[str, Any]:
    text = str(raw_text or "").strip()
    if not text:
        raise ValueError("empty payload")

    candidates = [text]
    if text.startswith("```"):
        without_fence = re.sub(r"^\s*```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        without_fence = re.sub(r"\s*```\s*$", "", without_fence)
        if without_fence.strip():
            candidates.append(without_fence.strip())
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        candidates.append(text[start : end + 1].strip())

    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    raise ValueError("invalid json object")


def _normalize_chat_prompt_history(user_prompts: list[str]) -> list[str]:
    normalized = [
        _compact_whitespace(prompt).strip()
        for prompt in user_prompts
        if _compact_whitespace(prompt).strip() and not _looks_like_terminal_control_payload(_compact_whitespace(prompt).strip())
    ]
    if not normalized:
        return []
    return normalized


def _chat_title_prompt_fingerprint(user_prompts: list[str], max_chars: int = CHAT_TITLE_MAX_CHARS) -> str:
    prompts = _normalize_chat_prompt_history(user_prompts)
    if not prompts:
        return ""
    fingerprint_payload = {
        "model": CHAT_TITLE_OPENAI_MODEL,
        "max_chars": max_chars,
        "prompts": prompts,
    }
    serialized = json.dumps(fingerprint_payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _append_tail(existing: str, chunk: str, max_chars: int) -> str:
    merged = (existing or "") + (chunk or "")
    if len(merged) <= max_chars:
        return merged
    return merged[-max_chars:]


def _clean_url_token(url_text: str) -> str:
    cleaned = str(url_text or "").strip()
    cleaned = cleaned.strip("<>")
    cleaned = cleaned.rstrip(".,);]}>\"'")
    return cleaned


def _first_url_in_text(text: str, starts_with: str) -> str:
    if not text:
        return ""
    pattern = rf"{re.escape(starts_with)}[^\s]+"
    match = re.search(pattern, text)
    return _clean_url_token(match.group(0)) if match else ""


def _parse_local_callback(url_text: str) -> tuple[str, int, str]:
    cleaned = _clean_url_token(url_text)
    parsed = urllib.parse.urlparse(cleaned)
    if not parsed.scheme.startswith("http"):
        return "", OPENAI_ACCOUNT_LOGIN_DEFAULT_CALLBACK_PORT, "/auth/callback"
    host = (parsed.hostname or "").lower()
    if host not in {"localhost", "127.0.0.1"}:
        return "", OPENAI_ACCOUNT_LOGIN_DEFAULT_CALLBACK_PORT, "/auth/callback"
    callback_path = parsed.path or "/auth/callback"
    callback_port = OPENAI_ACCOUNT_LOGIN_DEFAULT_CALLBACK_PORT
    try:
        parsed_port = parsed.port
    except ValueError:
        parsed_port = None
        port_match = re.search(r":(\d+)", parsed.netloc or "")
        if port_match:
            try:
                parsed_port = int(port_match.group(1))
            except ValueError:
                parsed_port = None
    if parsed_port is not None:
        callback_port = parsed_port
    if callback_port < 1 or callback_port > 65535:
        callback_port = OPENAI_ACCOUNT_LOGIN_DEFAULT_CALLBACK_PORT

    normalized_netloc = host
    if callback_port != OPENAI_ACCOUNT_LOGIN_DEFAULT_CALLBACK_PORT or ":" in (parsed.netloc or ""):
        normalized_netloc = f"{host}:{callback_port}"
    normalized_url = urllib.parse.urlunparse(
        (
            parsed.scheme or "http",
            normalized_netloc,
            callback_path,
            "",
            parsed.query,
            parsed.fragment,
        )
    )
    return normalized_url, callback_port, callback_path


def _chat_subtitle_from_log(log_path: Path) -> str:
    lines = _chat_preview_lines_from_log(log_path)
    if not lines:
        return ""

    def normalize_candidate_line(raw_line: str) -> str:
        candidate = str(raw_line or "").strip()
        if not candidate:
            return ""
        candidate = LEADING_INVISIBLE_RE.sub("", candidate).strip()
        while candidate and candidate[0] in "│┃┆┊╎╏":
            candidate = candidate[1:].lstrip()
        return candidate

    def strip_known_marker_prefix(candidate: str) -> str:
        for marker in CHAT_SUBTITLE_MARKERS:
            if candidate.startswith(marker):
                return _compact_whitespace(candidate[len(marker) :]).strip()
        return _compact_whitespace(candidate).strip()

    def strip_status_prefix(candidate: str) -> str:
        value = _compact_whitespace(candidate).strip()
        if not value:
            return ""
        index = 0
        while index < len(value):
            ch = value[index]
            if ch.isspace() or ch in "./|\\-":
                index += 1
                continue
            codepoint = ord(ch)
            if ch in CHAT_SUBTITLE_MARKERS:
                index += 1
                continue
            if (
                codepoint == 0x2219
                or 0x2022 <= codepoint <= 0x2043
                or 0x25A0 <= codepoint <= 0x25FF
            ):
                index += 1
                continue
            if 0x2800 <= codepoint <= 0x28FF:  # braille spinner glyphs
                index += 1
                continue
            break
        return _compact_whitespace(value[index:]).strip()

    def subtitle_value(line: str) -> str:
        candidate = normalize_candidate_line(line)
        if not candidate:
            return ""
        if candidate.startswith((">", "›")):
            return ""
        if candidate.lower().startswith("you:"):
            return ""
        compact = _compact_whitespace(candidate).strip()
        if not compact:
            return ""
        lowered = compact.lower()
        if "waiting for background terminal" in lowered:
            return strip_status_prefix(compact) or strip_known_marker_prefix(compact)
        if "esc to interrupt" in lowered and "working (" in lowered:
            return strip_status_prefix(compact) or compact
        for marker in CHAT_SUBTITLE_MARKERS:
            if compact.startswith(marker):
                return _compact_whitespace(compact[len(marker) :]).strip()
        candidate = compact
        first = candidate[0]
        remainder = _compact_whitespace(candidate[1:]).strip()
        if not remainder:
            return ""
        if not any(ch.isalpha() for ch in remainder):
            return ""
        marker_codepoint = ord(first)
        if (
            marker_codepoint == 0x2219  # BULLET OPERATOR
            or 0x2022 <= marker_codepoint <= 0x2043  # bullets and related punctuation
            or 0x25A0 <= marker_codepoint <= 0x25FF  # geometric shapes
            or 0x2800 <= marker_codepoint <= 0x28FF  # braille spinner glyphs
        ):
            return remainder
        return ""

    prompt_index = -1
    for index in range(len(lines) - 1, -1, -1):
        if lines[index].startswith((">", "›")):
            prompt_index = index
            break

    search_start = prompt_index - 1 if prompt_index >= 0 else len(lines) - 1
    for index in range(search_start, -1, -1):
        subtitle = subtitle_value(lines[index])
        if subtitle:
            if len(subtitle) > CHAT_SUBTITLE_MAX_CHARS:
                return subtitle[: CHAT_SUBTITLE_MAX_CHARS - 1].rstrip() + "…"
            return subtitle
    return ""


def _default_supplementary_gids() -> str:
    gids = sorted({gid for gid in os.getgroups() if gid != os.getgid()})
    return ",".join(str(gid) for gid in gids)


def _normalize_csv(value: str | None) -> str:
    if value is None:
        return ""
    values = [part.strip() for part in value.split(",") if part.strip()]
    return ",".join(values)


def _parse_gid_csv(value: str) -> list[int]:
    gids: list[int] = []
    seen: set[int] = set()
    for raw in value.split(","):
        token = raw.strip()
        if not token:
            continue
        if not token.isdigit():
            continue
        gid = int(token, 10)
        if gid in seen:
            continue
        gids.append(gid)
        seen.add(gid)
    return gids


def _read_codex_auth(path: Path) -> tuple[bool, str]:
    if not path.exists():
        return False, ""
    try:
        payload = json.loads(path.read_text(encoding="utf-8", errors="ignore"))
    except (OSError, json.JSONDecodeError):
        return False, ""
    if not isinstance(payload, dict):
        return False, ""

    auth_mode = str(payload.get("auth_mode") or "").strip().lower()
    if auth_mode != "chatgpt":
        return False, auth_mode

    tokens = payload.get("tokens")
    if not isinstance(tokens, dict):
        return False, auth_mode

    refresh_token = str(tokens.get("refresh_token") or "").strip()
    return bool(refresh_token), auth_mode


def _snapshot_schema_version() -> int:
    return 3


def _docker_remove_images(prefixes: tuple[str, ...], explicit_tags: set[str]) -> None:
    if shutil.which("docker") is None:
        return

    requested: set[str] = {tag.strip() for tag in explicit_tags if str(tag).strip()}
    list_result = subprocess.run(
        ["docker", "images", "--format", "{{.Repository}}:{{.Tag}}"],
        check=False,
        text=True,
        capture_output=True,
    )
    if list_result.returncode == 0:
        for raw in list_result.stdout.splitlines():
            tag = raw.strip()
            if not tag or tag == "<none>:<none>":
                continue
            if any(tag.startswith(prefix) for prefix in prefixes):
                requested.add(tag)

    if not requested:
        return

    subprocess.run(
        ["docker", "image", "rm", "-f", *sorted(requested)],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _docker_fix_path_ownership(path: Path, uid: int, gid: int) -> None:
    if not path.exists():
        return
    if shutil.which("docker") is None:
        raise RuntimeError("docker command not found in PATH")
    if not _docker_image_exists(DEFAULT_AGENT_IMAGE):
        raise RuntimeError(
            f"Runtime image '{DEFAULT_AGENT_IMAGE}' is not available for ownership repair."
        )
    result = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--entrypoint",
            "bash",
            "--volume",
            f"{path}:/target",
            DEFAULT_AGENT_IMAGE,
            "-lc",
            f"chown -R {uid}:{gid} /target && chmod -R u+rwX /target",
        ],
        check=False,
        text=True,
        capture_output=True,
    )
    if result.returncode != 0:
        combined = f"{result.stdout or ''}{result.stderr or ''}".strip()
        detail = combined or f"docker run exited with code {result.returncode}"
        raise RuntimeError(f"Failed to repair path ownership for {path}: {detail}")


def _detect_default_branch(repo_url: str, env: dict[str, str] | None = None) -> str:
    result = _run(["git", "ls-remote", "--symref", repo_url, "HEAD"], capture=True, check=False, env=env)
    if result.returncode != 0:
        return "master"

    for line in result.stdout.splitlines():
        if not line.startswith("ref:"):
            continue
        parts = line.replace("\t", " ").split()
        if len(parts) < 2:
            continue
        ref = parts[1]
        if ref.startswith("refs/heads/"):
            return ref.rsplit("/", 1)[-1]

    return "master"


def _git_default_remote_branch(repo_dir: Path) -> str | None:
    result = _run_for_repo(["symbolic-ref", "refs/remotes/origin/HEAD"], repo_dir, capture=True, check=False)
    if result.returncode != 0:
        return None
    ref = result.stdout.strip()
    if not ref.startswith("refs/remotes/origin/"):
        return None
    return ref.rsplit("/", 1)[-1]


def _git_has_remote_branch(repo_dir: Path, branch: str) -> bool:
    result = _run_for_repo(["show-ref", "--verify", "--quiet", f"refs/remotes/origin/{branch}"], repo_dir, check=False)
    return result.returncode == 0


def _is_process_running(pid: int | None) -> bool:
    if pid is None:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return False


def _stop_process(pid: int) -> None:
    if not _is_process_running(pid):
        return

    try:
        pgid = os.getpgid(pid)
    except (ProcessLookupError, OSError):
        pgid = None

    try:
        if pgid:
            os.killpg(pgid, signal.SIGTERM)
        else:
            os.kill(pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError, OSError):
        try:
            os.kill(pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError, OSError):
            return

    deadline = time.monotonic() + 4
    while time.monotonic() < deadline:
        if not _is_process_running(pid):
            return
        time.sleep(0.1)

    if _is_process_running(pid):
        try:
            if pgid:
                os.killpg(pgid, signal.SIGKILL)
            else:
                os.kill(pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            try:
                os.kill(pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError, OSError):
                return


def _stop_processes(pids: list[int], timeout_seconds: float = 4.0) -> int:
    active = [pid for pid in sorted({int(pid) for pid in pids}) if _is_process_running(pid)]
    if not active:
        return 0

    groups: dict[int, int] = {}
    for pid in active:
        try:
            pgid = os.getpgid(pid)
        except (ProcessLookupError, OSError):
            pgid = 0
        groups[pid] = pgid
        try:
            if pgid:
                os.killpg(pgid, signal.SIGTERM)
            else:
                os.kill(pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError, OSError):
            try:
                os.kill(pid, signal.SIGTERM)
            except (ProcessLookupError, PermissionError, OSError):
                continue

    deadline = time.monotonic() + max(0.1, float(timeout_seconds))
    alive = active
    while time.monotonic() < deadline:
        alive = [pid for pid in alive if _is_process_running(pid)]
        if not alive:
            return len(active)
        time.sleep(0.1)

    for pid in alive:
        pgid = groups.get(pid, 0)
        try:
            if pgid:
                os.killpg(pgid, signal.SIGKILL)
            else:
                os.kill(pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            try:
                os.kill(pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError, OSError):
                continue

    return len(active)


def _signal_process_group_winch(pid: int) -> None:
    try:
        pgid = os.getpgid(pid)
    except OSError:
        pgid = 0

    if pgid:
        try:
            os.killpg(pgid, signal.SIGWINCH)
            return
        except OSError:
            pass

    try:
        os.kill(pid, signal.SIGWINCH)
    except OSError:
        pass


class HubState:
    def __init__(
        self,
        data_dir: Path,
        config_file: Path,
        system_prompt_file: Path | None = None,
        hub_host: str = DEFAULT_HOST,
        hub_port: int = DEFAULT_PORT,
        artifact_publish_base_url: str | None = None,
    ):
        self.local_uid = os.getuid()
        self.local_user = f"uid-{self.local_uid}"
        self.local_gid = os.getgid()
        self.local_supp_gids = _normalize_csv(_default_supplementary_gids())
        self.local_umask = "0022"
        self.host_agent_home = (Path.home() / ".agent-home" / self.local_user).resolve()
        self.host_codex_dir = self.host_agent_home / ".codex"
        self.openai_codex_auth_file = self.host_codex_dir / OPENAI_CODEX_AUTH_FILE_NAME

        self.data_dir = data_dir
        self.config_file = config_file
        self.system_prompt_file = Path(system_prompt_file or _default_system_prompt_file())
        self.hub_host = str(hub_host or DEFAULT_HOST)
        self.hub_port = int(hub_port or DEFAULT_PORT)
        self.artifact_publish_base_url = _resolve_artifact_publish_base_url(
            artifact_publish_base_url,
            self.hub_port,
        )
        self.state_file = self.data_dir / STATE_FILE_NAME
        self.agent_capabilities_cache_file = self.data_dir / AGENT_CAPABILITIES_CACHE_FILE_NAME
        self.project_dir = self.data_dir / "projects"
        self.chat_dir = self.data_dir / "chats"
        self.log_dir = self.data_dir / "logs"
        self.secrets_dir = self.data_dir / SECRETS_DIR_NAME
        self.openai_credentials_file = self.secrets_dir / OPENAI_CREDENTIALS_FILE_NAME
        self.github_app_settings_file = self.secrets_dir / GITHUB_APP_SETTINGS_FILE_NAME
        self.github_app_installation_file = self.secrets_dir / GITHUB_APP_INSTALLATION_FILE_NAME
        self.github_personal_access_token_file = self.secrets_dir / GITHUB_PERSONAL_ACCESS_TOKEN_FILE_NAME
        self.github_git_credentials_file = self.secrets_dir / GITHUB_GIT_CREDENTIALS_FILE_NAME
        self.github_app_settings: GithubAppSettings | None = None
        self.github_app_settings_error = ""
        self._lock = Lock()
        self._runtime_lock = Lock()
        self._events_lock = Lock()
        self._project_build_lock = Lock()
        self._project_build_threads: dict[str, Thread] = {}
        self._chat_runtimes: dict[str, ChatRuntime] = {}
        self._event_listeners: set[queue.Queue[dict[str, Any] | None]] = set()
        self._openai_login_lock = Lock()
        self._openai_login_session: OpenAIAccountLoginSession | None = None
        self._chat_input_lock = Lock()
        self._chat_input_buffers: dict[str, str] = {}
        self._chat_input_ansi_carry: dict[str, str] = {}
        self._chat_title_job_lock = Lock()
        self._chat_title_jobs_inflight: set[str] = set()
        self._chat_title_jobs_pending: set[str] = set()
        self._github_token_lock = Lock()
        self._github_token_cache: dict[str, Any] = {}
        self._github_setup_lock = Lock()
        self._github_setup_session: GithubAppSetupSession | None = None
        self._agent_capabilities_lock = Lock()
        self._agent_capabilities = _default_agent_capabilities_cache_payload()
        self._agent_capabilities_discovery_thread: Thread | None = None
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.project_dir.mkdir(parents=True, exist_ok=True)
        self.chat_dir.mkdir(parents=True, exist_ok=True)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.secrets_dir.mkdir(parents=True, exist_ok=True)
        self.host_codex_dir.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.secrets_dir, 0o700)
        except OSError:
            pass
        self._reload_github_app_settings()
        self._load_agent_capabilities_cache()

    def load(self) -> dict[str, Any]:
        with self._lock:
            if not self.state_file.exists():
                return _new_state()
            try:
                loaded = json.loads(self.state_file.read_text())
            except json.JSONDecodeError:
                return _new_state()
        if not isinstance(loaded, dict):
            return _new_state()
        projects = loaded.get("projects")
        chats = loaded.get("chats")
        if not isinstance(projects, dict):
            projects = {}
        if not isinstance(chats, dict):
            chats = {}
        state = {"version": loaded.get("version", 1), "projects": projects, "chats": chats}
        for chat in state["chats"].values():
            if not isinstance(chat, dict):
                continue
            legacy_args = chat.get("codex_args")
            current_args = chat.get("agent_args")
            if isinstance(current_args, list):
                chat["agent_args"] = [str(arg) for arg in current_args]
            elif isinstance(legacy_args, list):
                chat["agent_args"] = [str(arg) for arg in legacy_args]
            else:
                chat["agent_args"] = []
            chat["agent_type"] = _normalize_chat_agent_type(chat.get("agent_type"))
            prompts = chat.get("title_user_prompts")
            if isinstance(prompts, list):
                normalized_prompts = [str(item) for item in prompts if str(item).strip()]
                chat["title_user_prompts"] = normalized_prompts
            else:
                chat["title_user_prompts"] = []
            chat["title_cached"] = _truncate_title(str(chat.get("title_cached") or ""), CHAT_TITLE_MAX_CHARS)
            chat["title_prompt_fingerprint"] = str(chat.get("title_prompt_fingerprint") or "")
            chat["title_source"] = str(chat.get("title_source") or "openai")
            chat["title_status"] = str(chat.get("title_status") or "idle")
            chat["title_error"] = str(chat.get("title_error") or "")
            artifacts = _normalize_chat_artifacts(chat.get("artifacts"))
            chat["artifacts"] = artifacts
            current_ids_raw = chat.get("artifact_current_ids")
            if isinstance(current_ids_raw, list):
                chat["artifact_current_ids"] = _normalize_chat_current_artifact_ids(current_ids_raw, artifacts)
            else:
                chat["artifact_current_ids"] = [str(artifact.get("id") or "") for artifact in artifacts if str(artifact.get("id") or "")]
            chat["artifact_prompt_history"] = _normalize_chat_artifact_prompt_history(chat.get("artifact_prompt_history"))
            chat["artifact_publish_token_hash"] = str(chat.get("artifact_publish_token_hash") or "")
            chat["artifact_publish_token_issued_at"] = str(chat.get("artifact_publish_token_issued_at") or "")
        return state

    @staticmethod
    def _event_queue_put(listener: queue.Queue[dict[str, Any] | None], value: dict[str, Any] | None) -> None:
        try:
            listener.put_nowait(value)
            return
        except queue.Full:
            pass

        try:
            listener.get_nowait()
        except queue.Empty:
            return

        try:
            listener.put_nowait(value)
        except queue.Full:
            return

    def _emit_event(self, event_type: str, payload: dict[str, Any] | None = None) -> None:
        event = {"type": str(event_type), "payload": payload or {}, "sent_at": _iso_now()}
        with self._events_lock:
            listeners = list(self._event_listeners)
        LOGGER.debug("Emitting hub event type=%s listeners=%d", event_type, len(listeners))
        for listener in listeners:
            self._event_queue_put(listener, event)

    def _emit_state_changed(self, reason: str = "") -> None:
        self._emit_event(EVENT_TYPE_STATE_CHANGED, {"reason": str(reason or "")})

    def _emit_auth_changed(self, reason: str = "") -> None:
        self._emit_event(EVENT_TYPE_AUTH_CHANGED, {"reason": str(reason or "")})

    def _emit_project_build_log(self, project_id: str, text: str, replace: bool = False) -> None:
        self._emit_event(
            EVENT_TYPE_PROJECT_BUILD_LOG,
            {
                "project_id": str(project_id),
                "text": str(text or ""),
                "replace": bool(replace),
            },
        )

    def _emit_openai_account_session_changed(self, reason: str = "") -> None:
        payload = self.openai_account_session_payload()
        payload["reason"] = str(reason or "")
        self._emit_event(EVENT_TYPE_OPENAI_ACCOUNT_SESSION, payload)

    def _emit_agent_capabilities_changed(self, reason: str = "") -> None:
        self._emit_event(EVENT_TYPE_AGENT_CAPABILITIES_CHANGED, {"reason": str(reason or "")})

    def _agent_capabilities_payload_locked(self) -> dict[str, Any]:
        return _normalize_agent_capabilities_payload(self._agent_capabilities)

    def _write_agent_capabilities_cache_locked(self) -> None:
        normalized = self._agent_capabilities_payload_locked()
        with self.agent_capabilities_cache_file.open("w", encoding="utf-8") as fp:
            json.dump(normalized, fp, indent=2)
        self._agent_capabilities = normalized

    def _load_agent_capabilities_cache(self) -> None:
        with self._agent_capabilities_lock:
            if not self.agent_capabilities_cache_file.exists():
                self._agent_capabilities = _default_agent_capabilities_cache_payload()
                return
            try:
                raw_payload = json.loads(self.agent_capabilities_cache_file.read_text(encoding="utf-8", errors="ignore"))
            except (OSError, json.JSONDecodeError):
                self._agent_capabilities = _default_agent_capabilities_cache_payload()
                return
            normalized = _normalize_agent_capabilities_payload(raw_payload)
            normalized["discovery_in_progress"] = False
            self._agent_capabilities = normalized
            try:
                self._write_agent_capabilities_cache_locked()
            except OSError:
                return

    def agent_capabilities_payload(self) -> dict[str, Any]:
        with self._agent_capabilities_lock:
            return self._agent_capabilities_payload_locked()

    def _discover_agent_capabilities_for_type(self, agent_type: str, previous: dict[str, Any]) -> dict[str, Any]:
        resolved_type = _normalize_chat_agent_type(agent_type)
        commands = AGENT_CAPABILITY_DISCOVERY_COMMANDS_BY_TYPE.get(resolved_type, ())
        discovered_models: list[str] = []
        discovered_reasoning_modes: list[str] = []
        last_error = ""
        now = _iso_now()

        for raw_cmd in commands:
            cmd = [str(token) for token in raw_cmd]
            return_code, output_text = _run_agent_capability_probe(cmd, AGENT_CAPABILITY_DISCOVERY_TIMEOUT_SECONDS)
            if return_code == 127:
                last_error = f"command not found: {cmd[0]}"
                LOGGER.info("Agent capability discovery skipped for agent=%s: %s", resolved_type, last_error)
                continue
            if return_code == 124:
                last_error = f"timeout running command: {' '.join(cmd)}"
                LOGGER.warning("Agent capability discovery timeout agent=%s cmd=%s", resolved_type, cmd)
                continue
            elif return_code != 0:
                last_error = f"command failed ({return_code}): {' '.join(cmd)}"
                LOGGER.info(
                    "Agent capability discovery command failed agent=%s cmd=%s return_code=%d output=%s",
                    resolved_type,
                    cmd,
                    return_code,
                    _short_summary(output_text, max_words=60, max_chars=600),
                )
                continue

            parsed_models = _extract_model_candidates_from_output(output_text, resolved_type)
            if parsed_models:
                discovered_models = _normalize_model_options_for_agent(
                    resolved_type,
                    parsed_models,
                    _agent_capability_defaults_for_type(resolved_type)["models"],
                )
            if resolved_type == AGENT_TYPE_CODEX:
                parsed_reasoning = _extract_reasoning_candidates_from_output(output_text)
                if parsed_reasoning:
                    discovered_reasoning_modes = _normalize_reasoning_mode_options_for_agent(
                        resolved_type,
                        parsed_reasoning,
                        _agent_capability_defaults_for_type(resolved_type)["reasoning_modes"],
                    )
            if discovered_models and (resolved_type != AGENT_TYPE_CODEX or discovered_reasoning_modes):
                break

        if not discovered_models:
            discovered_models = _normalize_model_options_for_agent(
                resolved_type,
                previous.get("models"),
                _agent_capability_defaults_for_type(resolved_type)["models"],
            )
        if resolved_type == AGENT_TYPE_CODEX:
            if not discovered_reasoning_modes:
                discovered_reasoning_modes = _normalize_reasoning_mode_options_for_agent(
                    resolved_type,
                    previous.get("reasoning_modes"),
                    _agent_capability_defaults_for_type(resolved_type)["reasoning_modes"],
                )
        else:
            discovered_reasoning_modes = _normalize_reasoning_mode_options_for_agent(
                resolved_type,
                previous.get("reasoning_modes"),
                _agent_capability_defaults_for_type(resolved_type)["reasoning_modes"],
            )

        return {
            "agent_type": resolved_type,
            "label": str(previous.get("label") or AGENT_LABEL_BY_TYPE.get(resolved_type, resolved_type.title())),
            "models": discovered_models,
            "reasoning_modes": discovered_reasoning_modes,
            "updated_at": now,
            "last_error": last_error,
        }

    def _agent_capability_discovery_worker(self) -> None:
        try:
            with self._agent_capabilities_lock:
                baseline_payload = self._agent_capabilities_payload_locked()
            baseline_agents = {
                str(agent.get("agent_type") or ""): dict(agent)
                for agent in baseline_payload.get("agents") or []
                if isinstance(agent, dict)
            }

            discovered_agents: list[dict[str, Any]] = []
            for agent_type in _ordered_supported_agent_types():
                previous = baseline_agents.get(agent_type) or _agent_capability_defaults_for_type(agent_type)
                discovered_agents.append(self._discover_agent_capabilities_for_type(agent_type, previous))

            finished_at = _iso_now()
            with self._agent_capabilities_lock:
                merged_payload = self._agent_capabilities_payload_locked()
                merged_payload["updated_at"] = finished_at
                merged_payload["discovery_in_progress"] = False
                merged_payload["discovery_finished_at"] = finished_at
                merged_payload["agents"] = discovered_agents
                self._agent_capabilities = _normalize_agent_capabilities_payload(merged_payload)
                self._write_agent_capabilities_cache_locked()
        finally:
            with self._agent_capabilities_lock:
                active = self._agent_capabilities_discovery_thread
                if active is not None and active.ident == current_thread().ident:
                    self._agent_capabilities_discovery_thread = None
                self._agent_capabilities["discovery_in_progress"] = False
                if not str(self._agent_capabilities.get("discovery_finished_at") or "").strip():
                    self._agent_capabilities["discovery_finished_at"] = _iso_now()
                self._agent_capabilities = _normalize_agent_capabilities_payload(self._agent_capabilities)
                try:
                    self._write_agent_capabilities_cache_locked()
                except OSError:
                    pass
            self._emit_agent_capabilities_changed(reason="discovery_finished")

    def start_agent_capabilities_discovery(self) -> dict[str, Any]:
        payload_to_return: dict[str, Any]
        should_emit = False
        with self._agent_capabilities_lock:
            existing = self._agent_capabilities_discovery_thread
            if existing is not None and existing.is_alive():
                return self._agent_capabilities_payload_locked()

            started_at = _iso_now()
            self._agent_capabilities["discovery_in_progress"] = True
            self._agent_capabilities["discovery_started_at"] = started_at
            self._agent_capabilities["discovery_finished_at"] = ""
            self._agent_capabilities = _normalize_agent_capabilities_payload(self._agent_capabilities)
            self._write_agent_capabilities_cache_locked()

            worker = Thread(target=self._agent_capability_discovery_worker, daemon=True)
            self._agent_capabilities_discovery_thread = worker
            worker.start()
            payload_to_return = self._agent_capabilities_payload_locked()
            should_emit = True
        if should_emit:
            self._emit_agent_capabilities_changed(reason="discovery_started")
        return payload_to_return

    def attach_events(self) -> queue.Queue[dict[str, Any] | None]:
        listener: queue.Queue[dict[str, Any] | None] = queue.Queue(maxsize=HUB_EVENT_QUEUE_MAX)
        with self._events_lock:
            self._event_listeners.add(listener)
        return listener

    def detach_events(self, listener: queue.Queue[dict[str, Any] | None]) -> None:
        with self._events_lock:
            self._event_listeners.discard(listener)

    def events_snapshot(self) -> dict[str, Any]:
        state_payload = self.state_payload()
        build_logs: dict[str, str] = {}
        for project in state_payload.get("projects") or []:
            project_id = str(project.get("id") or "")
            if not project_id:
                continue
            if str(project.get("build_status") or "") != "building":
                continue
            log_path = self.project_build_log(project_id)
            if not log_path.exists():
                build_logs[project_id] = ""
                continue
            build_logs[project_id] = log_path.read_text(encoding="utf-8", errors="ignore")
        return {
            "state": state_payload,
            "auth": self.auth_settings_payload(),
            "openai_account_session": self.openai_account_session_payload(),
            "agent_capabilities": self.agent_capabilities_payload(),
            "project_build_logs": build_logs,
        }

    def save(self, state: dict[str, Any], reason: str = "") -> None:
        with self._lock:
            with self.state_file.open("w", encoding="utf-8") as fp:
                json.dump(state, fp, indent=2)
        self._emit_state_changed(reason=reason)

    def _openai_credentials_arg(self) -> list[str]:
        return ["--credentials-file", str(self.openai_credentials_file)]

    def _reload_github_app_settings(self) -> None:
        env_settings, env_error = _load_github_app_settings_from_env()
        if env_settings is not None or env_error:
            self.github_app_settings = env_settings
            self.github_app_settings_error = env_error
            return

        file_settings, file_error = _load_github_app_settings_from_file(self.github_app_settings_file)
        self.github_app_settings = file_settings
        self.github_app_settings_error = file_error

    def _github_setup_base_urls(self) -> tuple[str, str]:
        if self.github_app_settings is not None:
            return self.github_app_settings.web_base_url, self.github_app_settings.api_base_url

        web_base_raw = str(os.environ.get(GITHUB_APP_WEB_BASE_URL_ENV, GITHUB_APP_DEFAULT_WEB_BASE_URL)).strip()
        api_base_raw = str(os.environ.get(GITHUB_APP_API_BASE_URL_ENV, GITHUB_APP_DEFAULT_API_BASE_URL)).strip()
        try:
            web_base = _normalize_absolute_http_base_url(web_base_raw, GITHUB_APP_WEB_BASE_URL_ENV)
            api_base = _normalize_absolute_http_base_url(api_base_raw, GITHUB_APP_API_BASE_URL_ENV)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return web_base, api_base

    @staticmethod
    def _github_setup_session_is_active(status: str) -> bool:
        return status in {"awaiting_user", "converting"}

    @staticmethod
    def _github_setup_session_is_expired(expires_at: str) -> bool:
        expires_unix = _iso_to_unix_seconds(expires_at)
        return expires_unix > 0 and int(time.time()) >= expires_unix

    def _github_setup_session_locked(self) -> GithubAppSetupSession | None:
        session = self._github_setup_session
        if session is None:
            return None
        if (
            self._github_setup_session_is_active(session.status)
            and self._github_setup_session_is_expired(session.expires_at)
        ):
            session.status = "expired"
            session.completed_at = session.completed_at or _iso_now()
            if not session.error:
                session.error = "GitHub setup session expired. Click Connect to GitHub and try again."
        return session

    def _github_setup_session_payload_locked(self) -> dict[str, Any]:
        session = self._github_setup_session_locked()
        if session is None:
            return {
                "active": False,
                "id": "",
                "status": "idle",
                "form_action": "",
                "manifest": {},
                "started_at": "",
                "expires_at": "",
                "completed_at": "",
                "error": "",
                "app_id": "",
                "app_slug": "",
                "callback_url": "",
            }
        return {
            "active": self._github_setup_session_is_active(session.status),
            "id": session.id,
            "status": session.status,
            "form_action": session.form_action,
            "manifest": dict(session.manifest),
            "started_at": session.started_at,
            "expires_at": session.expires_at,
            "completed_at": session.completed_at,
            "error": session.error,
            "app_id": session.app_id,
            "app_slug": session.app_slug,
            "callback_url": session.callback_url,
        }

    def github_app_setup_session_payload(self) -> dict[str, Any]:
        with self._github_setup_lock:
            return self._github_setup_session_payload_locked()

    def start_github_app_setup(self, origin: Any) -> dict[str, Any]:
        if _github_app_env_config_present():
            raise HTTPException(
                status_code=400,
                detail=(
                    "GitHub App setup from Settings is disabled while AGENT_HUB_GITHUB_APP_* environment variables are set."
                ),
            )

        origin_text = str(origin or "").strip()
        if not origin_text:
            raise HTTPException(status_code=400, detail="origin is required.")
        try:
            normalized_origin = _normalize_absolute_http_base_url(origin_text, "origin")
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        parsed_origin = urllib.parse.urlsplit(normalized_origin)
        callback_url = urllib.parse.urlunsplit(
            (parsed_origin.scheme, parsed_origin.netloc, "/api/settings/auth/github/app/setup/callback", "", "")
        )
        web_base_url, api_base_url = self._github_setup_base_urls()
        setup_state = secrets.token_urlsafe(24)
        form_action = f"{web_base_url}/settings/apps/new?state={urllib.parse.quote(setup_state, safe='')}"
        app_name = f"{GITHUB_APP_DEFAULT_NAME}-{secrets.token_hex(2)}"
        manifest = {
            "name": app_name,
            "url": normalized_origin,
            "redirect_url": callback_url,
            "callback_urls": [callback_url],
            "public": False,
            "request_oauth_on_install": False,
            "hook_attributes": {
                "url": callback_url,
                "active": False,
            },
            "default_permissions": {
                "contents": "write",
                "pull_requests": "write",
                "issues": "write",
            },
            "default_events": [],
        }
        now = time.time()
        with self._github_setup_lock:
            self._github_setup_session = GithubAppSetupSession(
                id=uuid.uuid4().hex,
                state=setup_state,
                status="awaiting_user",
                form_action=form_action,
                manifest=manifest,
                callback_url=callback_url,
                web_base_url=web_base_url,
                api_base_url=api_base_url,
                started_at=_iso_from_timestamp(now),
                expires_at=_iso_from_timestamp(now + GITHUB_APP_SETUP_SESSION_LIFETIME_SECONDS),
            )
            return self._github_setup_session_payload_locked()

    def _github_manifest_conversion_request(self, api_base_url: str, code: str) -> dict[str, Any]:
        path_code = urllib.parse.quote(str(code or "").strip(), safe="")
        request = urllib.request.Request(
            f"{api_base_url}/app-manifests/{path_code}/conversions",
            headers={
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "agent-hub",
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(request, timeout=GITHUB_APP_API_TIMEOUT_SECONDS) as response:
                status = int(response.getcode() or 0)
                body_text = response.read().decode("utf-8", errors="ignore")
        except urllib.error.HTTPError as exc:
            status = int(exc.code or 0)
            body_text = exc.read().decode("utf-8", errors="ignore")
            message = _github_api_error_message(body_text)
            detail = f"GitHub app setup conversion failed with status {status}."
            if message:
                detail = f"{detail} {message}"
            raise HTTPException(status_code=400 if status < 500 else 502, detail=detail) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise HTTPException(
                status_code=502,
                detail="GitHub app setup conversion failed due to a network error.",
            ) from exc

        if not (200 <= status < 300):
            message = _github_api_error_message(body_text)
            detail = f"GitHub app setup conversion failed with status {status}."
            if message:
                detail = f"{detail} {message}"
            raise HTTPException(status_code=400 if status < 500 else 502, detail=detail)

        try:
            payload = json.loads(body_text) if body_text else {}
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=502, detail="GitHub returned invalid app setup conversion data.") from exc
        if not isinstance(payload, dict):
            raise HTTPException(status_code=502, detail="GitHub returned invalid app setup conversion data.")
        return payload

    def _clear_github_installation_state(self, remove_credentials: bool = True) -> None:
        paths = [self.github_app_installation_file]
        if remove_credentials:
            paths.append(self.github_git_credentials_file)
        for path in paths:
            if not path.exists():
                continue
            try:
                path.unlink()
            except OSError as exc:
                raise HTTPException(status_code=500, detail="Failed to clear previous GitHub installation state.") from exc
        with self._github_token_lock:
            self._github_token_cache = {}

    def _clear_github_personal_access_token_state(self, remove_credentials: bool = True) -> None:
        paths = [self.github_personal_access_token_file]
        if remove_credentials:
            paths.append(self.github_git_credentials_file)
        for path in paths:
            if not path.exists():
                continue
            try:
                path.unlink()
            except OSError as exc:
                raise HTTPException(
                    status_code=500,
                    detail="Failed to clear stored GitHub personal access token credentials.",
                ) from exc

    def _persist_github_app_settings(self, settings: GithubAppSettings) -> None:
        payload = {
            "app_id": settings.app_id,
            "app_slug": settings.app_slug,
            "private_key": settings.private_key,
            "web_base_url": settings.web_base_url,
            "api_base_url": settings.api_base_url,
            "configured_at": _iso_now(),
        }
        _write_private_env_file(self.github_app_settings_file, json.dumps(payload, indent=2) + "\n")
        self.github_app_settings = settings
        self.github_app_settings_error = ""
        with self._github_token_lock:
            self._github_token_cache = {}

    def complete_github_app_setup(self, code: Any, state_value: Any) -> dict[str, Any]:
        code_text = str(code or "").strip()
        if not code_text:
            raise HTTPException(status_code=400, detail="Missing GitHub setup code.")
        if not re.fullmatch(r"[A-Za-z0-9._-]+", code_text):
            raise HTTPException(status_code=400, detail="Invalid GitHub setup code.")

        state_text = str(state_value or "").strip()
        if not state_text:
            raise HTTPException(status_code=400, detail="Missing GitHub setup state.")

        with self._github_setup_lock:
            session = self._github_setup_session_locked()
            if session is None:
                raise HTTPException(status_code=400, detail="No GitHub setup session is active.")
            if session.status == "completed":
                return self._github_setup_session_payload_locked()
            if session.status in {"failed", "expired"}:
                detail = session.error or "GitHub setup session is not active."
                raise HTTPException(status_code=400, detail=detail)
            if not hmac.compare_digest(session.state, state_text):
                session.status = "failed"
                session.completed_at = _iso_now()
                session.error = "GitHub setup state did not match. Start setup again from Settings."
                raise HTTPException(status_code=400, detail=session.error)
            session.status = "converting"
            session.error = ""
            api_base_url = session.api_base_url
            web_base_url = session.web_base_url

        try:
            conversion_payload = self._github_manifest_conversion_request(api_base_url, code_text)
            resolved_settings = _normalize_github_app_settings_payload(
                {
                    "app_id": conversion_payload.get("id"),
                    "app_slug": conversion_payload.get("slug"),
                    "private_key": conversion_payload.get("pem"),
                    "web_base_url": web_base_url,
                    "api_base_url": api_base_url,
                },
                "GitHub app setup conversion",
            )
            self._persist_github_app_settings(resolved_settings)
            self._clear_github_installation_state(remove_credentials=False)
        except ValueError as exc:
            with self._github_setup_lock:
                session = self._github_setup_session_locked()
                if session is not None:
                    session.status = "failed"
                    session.completed_at = _iso_now()
                    session.error = str(exc)
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        except HTTPException as exc:
            with self._github_setup_lock:
                session = self._github_setup_session_locked()
                if session is not None and session.status != "completed":
                    session.status = "failed"
                    session.completed_at = _iso_now()
                    session.error = str(exc.detail or "GitHub app setup failed.")
            raise

        with self._github_setup_lock:
            session = self._github_setup_session_locked()
            if session is not None:
                session.status = "completed"
                session.completed_at = _iso_now()
                session.error = ""
                session.app_id = resolved_settings.app_id
                session.app_slug = resolved_settings.app_slug
            payload = self._github_setup_session_payload_locked()

        self._emit_auth_changed(reason="github_app_configured")
        return payload

    def fail_github_app_setup(self, message: Any, state_value: Any = "") -> dict[str, Any]:
        detail = str(message or "").strip() or "GitHub app setup failed."
        state_text = str(state_value or "").strip()
        with self._github_setup_lock:
            session = self._github_setup_session_locked()
            if session is None:
                return self._github_setup_session_payload_locked()
            if state_text and not hmac.compare_digest(session.state, state_text):
                return self._github_setup_session_payload_locked()
            session.status = "failed"
            session.completed_at = _iso_now()
            session.error = detail
            return self._github_setup_session_payload_locked()

    def _github_provider_host(self) -> str:
        if self.github_app_settings is None:
            return "github.com"
        parsed = urllib.parse.urlsplit(self.github_app_settings.web_base_url)
        return (parsed.hostname or "github.com").lower()

    def _github_install_url(self) -> str:
        if self.github_app_settings is None:
            return ""
        return f"{self.github_app_settings.web_base_url}/apps/{self.github_app_settings.app_slug}/installations/new"

    def _github_connected_installation(self) -> dict[str, Any] | None:
        payload = _read_json_if_exists(self.github_app_installation_file)
        if payload is None:
            return None
        installation_id = payload.get("installation_id")
        if isinstance(installation_id, int) and installation_id > 0:
            payload["installation_id"] = installation_id
            return payload
        return None

    def _normalize_github_personal_access_token_record(
        self,
        raw_record: dict[str, Any],
        default_host: str,
        record_index: int,
    ) -> dict[str, Any] | None:
        token = str(raw_record.get("personal_access_token") or "").strip()
        account_login = str(raw_record.get("account_login") or "").strip()
        if not token or not account_login:
            return None

        host_value = raw_record.get("host") or default_host
        try:
            host = _normalize_github_credential_host(host_value, field_name="host")
        except HTTPException:
            return None

        account_name = str(raw_record.get("account_name") or account_login).strip() or account_login
        account_email = str(raw_record.get("account_email") or "").strip()
        if not account_email:
            account_email = f"{account_login}@users.noreply.github.com"
        git_user_name = str(raw_record.get("git_user_name") or account_name).strip() or account_name
        git_user_email = str(raw_record.get("git_user_email") or account_email).strip() or account_email
        account_id = str(raw_record.get("account_id") or "").strip()
        token_scopes = str(raw_record.get("token_scopes") or "").strip()
        verified_at = str(raw_record.get("verified_at") or "").strip()
        connected_at = str(raw_record.get("connected_at") or "").strip()

        owner_scopes_raw = raw_record.get("owner_scopes")
        if owner_scopes_raw is None:
            owner_scopes_raw = raw_record.get("owners")
        try:
            owner_scopes = _normalize_github_owner_scopes(owner_scopes_raw, field_name="owner_scopes")
        except HTTPException:
            owner_scopes = []

        token_id = str(raw_record.get("token_id") or raw_record.get("id") or "").strip()
        if token_id:
            token_id = token_id[:GITHUB_PERSONAL_ACCESS_TOKEN_ID_MAX_CHARS]
        if not token_id:
            token_seed = f"{host}|{account_login.lower()}|{','.join(owner_scopes)}|{record_index}"
            token_id = hashlib.sha256(token_seed.encode("utf-8")).hexdigest()[:32]

        return {
            "token_id": token_id,
            "host": host,
            "personal_access_token": token,
            "account_login": account_login,
            "account_name": account_name,
            "account_email": account_email,
            "account_id": account_id,
            "git_user_name": git_user_name,
            "git_user_email": git_user_email,
            "token_scopes": token_scopes,
            "verified_at": verified_at,
            "connected_at": connected_at,
            "owner_scopes": owner_scopes,
        }

    def _github_connected_personal_access_tokens(self) -> list[dict[str, Any]]:
        payload = _read_json_if_exists(self.github_personal_access_token_file)
        if payload is None:
            return []

        raw_records: list[dict[str, Any]] = []
        if isinstance(payload.get("tokens"), list):
            raw_records = [item for item in payload["tokens"] if isinstance(item, dict)]
        elif isinstance(payload, dict):
            raw_records = [payload]

        if not raw_records:
            return []

        default_host = self._github_provider_host()
        records: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        for index, raw_record in enumerate(raw_records):
            normalized = self._normalize_github_personal_access_token_record(raw_record, default_host, index)
            if normalized is None:
                continue
            token_id = str(normalized.get("token_id") or "").strip()
            if token_id in seen_ids:
                token_id = hashlib.sha256(f"{token_id}|{index}".encode("utf-8")).hexdigest()[:32]
                normalized["token_id"] = token_id
            seen_ids.add(token_id)
            records.append(normalized)
        return records

    def _persist_github_personal_access_tokens(self, records: list[dict[str, Any]]) -> None:
        if not records:
            if self.github_personal_access_token_file.exists():
                try:
                    self.github_personal_access_token_file.unlink()
                except OSError as exc:
                    raise HTTPException(
                        status_code=500,
                        detail="Failed to clear stored GitHub personal access token credentials.",
                    ) from exc
            return

        payload_records: list[dict[str, Any]] = []
        for record in records:
            payload_records.append(
                {
                    "token_id": str(record.get("token_id") or "").strip(),
                    "host": str(record.get("host") or "").strip(),
                    "personal_access_token": str(record.get("personal_access_token") or "").strip(),
                    "account_login": str(record.get("account_login") or "").strip(),
                    "account_name": str(record.get("account_name") or "").strip(),
                    "account_email": str(record.get("account_email") or "").strip(),
                    "account_id": str(record.get("account_id") or "").strip(),
                    "git_user_name": str(record.get("git_user_name") or "").strip(),
                    "git_user_email": str(record.get("git_user_email") or "").strip(),
                    "token_scopes": str(record.get("token_scopes") or "").strip(),
                    "verified_at": str(record.get("verified_at") or "").strip(),
                    "connected_at": str(record.get("connected_at") or "").strip(),
                    "owner_scopes": _normalize_github_owner_scopes(record.get("owner_scopes"), field_name="owner_scopes"),
                }
            )
        payload = {"tokens": payload_records, "updated_at": _iso_now()}
        _write_private_env_file(self.github_personal_access_token_file, json.dumps(payload, indent=2) + "\n")

    def _github_connected_personal_access_token(self) -> dict[str, Any] | None:
        tokens = self._github_connected_personal_access_tokens()
        if not tokens:
            return None
        return dict(tokens[0])

    def _github_personal_access_token_for_repo(self, repo_url: str) -> dict[str, Any] | None:
        repo_host = _git_repo_host(repo_url)
        if not repo_host:
            return None
        repo_owner = _git_repo_owner(repo_url)
        matching_host = [
            token
            for token in self._github_connected_personal_access_tokens()
            if str(token.get("host") or "").strip().lower() == repo_host
        ]
        if not matching_host:
            return None

        if repo_owner:
            for token in matching_host:
                owner_scopes = [str(item or "").strip().lower() for item in _empty_list(token.get("owner_scopes"))]
                if repo_owner in owner_scopes:
                    return token
        for token in matching_host:
            owner_scopes = [str(item or "").strip().lower() for item in _empty_list(token.get("owner_scopes"))]
            if not owner_scopes:
                return token
        return matching_host[0]

    def _github_api_base_url_for_host(self, host: str) -> str:
        normalized_host = _normalize_github_credential_host(host, field_name="host")
        if self.github_app_settings is not None and self._github_provider_host() == normalized_host:
            return self.github_app_settings.api_base_url
        if normalized_host == "github.com":
            return GITHUB_APP_DEFAULT_API_BASE_URL
        return f"https://{normalized_host}/api/v3"

    def _verify_github_personal_access_token(self, token: str, host: str) -> dict[str, str]:
        api_base_url = self._github_api_base_url_for_host(host)
        request = urllib.request.Request(
            f"{api_base_url}/user",
            headers={
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "agent-hub",
                "Authorization": f"Bearer {token}",
            },
            method="GET",
        )

        oauth_scopes = ""
        try:
            with urllib.request.urlopen(request, timeout=GITHUB_APP_API_TIMEOUT_SECONDS) as response:
                status = int(response.getcode() or 0)
                payload_text = response.read().decode("utf-8", errors="ignore")
                oauth_scopes = str(response.headers.get("X-OAuth-Scopes") or "").strip()
        except urllib.error.HTTPError as exc:
            status = int(exc.code or 0)
            payload_text = exc.read().decode("utf-8", errors="ignore")
            oauth_scopes = str(exc.headers.get("X-OAuth-Scopes") or "").strip() if exc.headers else ""
            detail = f"GitHub personal access token verification failed with status {status}."
            message = _github_api_error_message(payload_text)
            if message:
                detail = f"{detail} {message}"
            if status in {401, 403}:
                raise HTTPException(status_code=400, detail=detail) from exc
            raise HTTPException(status_code=502, detail=detail) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise HTTPException(
                status_code=502,
                detail="GitHub personal access token verification failed due to a network error.",
            ) from exc

        if not (200 <= status < 300):
            detail = f"GitHub personal access token verification failed with status {status}."
            message = _github_api_error_message(payload_text)
            if message:
                detail = f"{detail} {message}"
            if status in {401, 403}:
                raise HTTPException(status_code=400, detail=detail)
            raise HTTPException(status_code=502, detail=detail)

        try:
            payload = json.loads(payload_text) if payload_text else {}
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=502, detail="GitHub returned invalid PAT verification payload.") from exc
        if not isinstance(payload, dict):
            raise HTTPException(status_code=502, detail="GitHub returned invalid PAT verification payload.")

        account_login = str(payload.get("login") or "").strip()
        account_name = str(payload.get("name") or "").strip()
        if not account_login:
            raise HTTPException(status_code=502, detail="GitHub did not return a user login for this token.")
        raw_account_id = payload.get("id")
        account_id = 0
        if isinstance(raw_account_id, int) and raw_account_id > 0:
            account_id = raw_account_id
        elif isinstance(raw_account_id, str) and raw_account_id.isdigit():
            account_id = int(raw_account_id)

        account_email = str(payload.get("email") or "").strip()
        if not account_email:
            if account_id > 0:
                account_email = f"{account_id}+{account_login}@users.noreply.github.com"
            else:
                account_email = f"{account_login}@users.noreply.github.com"

        return {
            "account_login": account_login,
            "account_name": account_name or account_login,
            "account_email": account_email,
            "account_id": str(account_id) if account_id > 0 else "",
            "token_scopes": oauth_scopes,
        }

    def _github_api_request(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
        auth_mode: str = "app",
        token: str = "",
    ) -> tuple[int, str]:
        settings = self.github_app_settings
        if settings is None:
            raise HTTPException(status_code=400, detail="GitHub App is not configured on this server.")
        url = f"{settings.api_base_url}{path}"
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "agent-hub",
        }
        if auth_mode == "app":
            headers["Authorization"] = f"Bearer {_github_app_jwt(settings)}"
        elif auth_mode == "installation":
            resolved_token = str(token or "").strip()
            if not resolved_token:
                raise HTTPException(status_code=500, detail="Missing GitHub installation token.")
            headers["Authorization"] = f"Bearer {resolved_token}"
        else:
            raise HTTPException(status_code=500, detail=f"Unsupported GitHub auth mode: {auth_mode}")

        raw_data = None
        if body is not None:
            raw_data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"

        request = urllib.request.Request(
            url,
            data=raw_data,
            headers=headers,
            method=method,
        )
        try:
            with urllib.request.urlopen(request, timeout=GITHUB_APP_API_TIMEOUT_SECONDS) as response:
                status = int(response.getcode() or 0)
                payload_text = response.read().decode("utf-8", errors="ignore")
        except urllib.error.HTTPError as exc:
            status = int(exc.code or 0)
            payload_text = exc.read().decode("utf-8", errors="ignore")
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise HTTPException(status_code=502, detail="GitHub API request failed due to a network error.") from exc

        if 200 <= status < 300:
            return status, payload_text

        detail = f"GitHub API request failed with status {status}."
        message = _github_api_error_message(payload_text)
        if message:
            detail = f"{detail} {message}"
        raise HTTPException(status_code=502, detail=detail)

    def _github_installation_token(self, installation_id: int, force_refresh: bool = False) -> tuple[str, str]:
        now = int(time.time())
        with self._github_token_lock:
            cached_installation_id = int(self._github_token_cache.get("installation_id") or 0)
            cached_token = str(self._github_token_cache.get("token") or "")
            cached_expires_at = str(self._github_token_cache.get("expires_at") or "")
            expires_unix = _iso_to_unix_seconds(cached_expires_at)
            if (
                not force_refresh
                and cached_installation_id == installation_id
                and cached_token
                and expires_unix > now + GITHUB_APP_TOKEN_REFRESH_SKEW_SECONDS
            ):
                return cached_token, cached_expires_at

        _status, payload_text = self._github_api_request(
            "POST",
            f"/app/installations/{installation_id}/access_tokens",
            body={},
            auth_mode="app",
        )
        try:
            payload = json.loads(payload_text) if payload_text else {}
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=502, detail="GitHub API returned invalid installation token payload.") from exc
        if not isinstance(payload, dict):
            raise HTTPException(status_code=502, detail="GitHub API returned invalid installation token payload.")

        token = str(payload.get("token") or "").strip()
        expires_at = str(payload.get("expires_at") or "").strip()
        if not token or not expires_at:
            raise HTTPException(status_code=502, detail="GitHub API did not return a valid installation token.")

        with self._github_token_lock:
            self._github_token_cache = {
                "installation_id": installation_id,
                "token": token,
                "expires_at": expires_at,
            }
        return token, expires_at

    def _refresh_github_git_credentials(self, installation_id: int, host: str) -> str:
        token, _expires_at = self._github_installation_token(installation_id)
        return self._write_github_git_credentials(
            host=host,
            username="x-access-token",
            secret=token,
        )

    def _write_github_git_credentials(self, host: str, username: str, secret: str) -> str:
        normalized_host = _normalize_github_credential_host(host, field_name="host")
        resolved_username = str(username or "").strip()
        resolved_secret = str(secret or "").strip()
        if not resolved_username:
            raise HTTPException(status_code=500, detail="Missing GitHub credential username.")
        if not resolved_secret:
            raise HTTPException(status_code=500, detail="Missing GitHub credential secret.")
        encoded_username = urllib.parse.quote(resolved_username, safe="")
        encoded_secret = urllib.parse.quote(resolved_secret, safe="")
        _write_private_env_file(
            self.github_git_credentials_file,
            f"https://{encoded_username}:{encoded_secret}@{normalized_host}\n",
        )
        return str(self.github_git_credentials_file)

    def _refresh_github_git_credentials_for_personal_access_token(
        self,
        token: str,
        host: str,
        account_login: str,
    ) -> str:
        return self._write_github_git_credentials(
            host=host,
            username=account_login,
            secret=token,
        )

    @staticmethod
    def _git_env_for_credentials_file(credential_file: str, host: str) -> dict[str, str]:
        normalized_host = str(host or "github.com").strip().lower()
        https_prefix = f"https://{normalized_host}/"
        return {
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_CONFIG_COUNT": "3",
            "GIT_CONFIG_KEY_0": "credential.helper",
            "GIT_CONFIG_VALUE_0": f"store --file={credential_file}",
            "GIT_CONFIG_KEY_1": f"url.{https_prefix}.insteadOf",
            "GIT_CONFIG_VALUE_1": f"git@{normalized_host}:",
            "GIT_CONFIG_KEY_2": f"url.{https_prefix}.insteadOf",
            "GIT_CONFIG_VALUE_2": f"ssh://git@{normalized_host}/",
        }

    def _github_repo_auth_context(self, repo_url: str) -> tuple[str, str, dict[str, Any]] | None:
        repo_host = _git_repo_host(repo_url)
        if not repo_host:
            return None

        personal_access = self._github_personal_access_token_for_repo(repo_url)
        if personal_access is not None:
            pat_host = str(personal_access.get("host") or "")
            if pat_host and repo_host == pat_host:
                return (GITHUB_CONNECTION_MODE_PERSONAL_ACCESS_TOKEN, pat_host, personal_access)

        installation = self._github_connected_installation()
        provider_host = self._github_provider_host()
        if installation is not None and repo_host == provider_host:
            installation_id = int(installation.get("installation_id") or 0)
            if installation_id > 0:
                return (
                    GITHUB_CONNECTION_MODE_GITHUB_APP,
                    provider_host,
                    {"installation_id": installation_id},
                )
        return None

    def _github_git_env_for_repo(self, repo_url: str) -> dict[str, str]:
        context = self._github_repo_auth_context(repo_url)
        if context is None:
            return {}
        mode, host, auth_payload = context
        if mode == GITHUB_CONNECTION_MODE_GITHUB_APP:
            installation_id = int(auth_payload.get("installation_id") or 0)
            if installation_id <= 0:
                return {}
            credentials_file = self._refresh_github_git_credentials(installation_id, host)
        elif mode == GITHUB_CONNECTION_MODE_PERSONAL_ACCESS_TOKEN:
            token = str(auth_payload.get("personal_access_token") or "").strip()
            account_login = str(auth_payload.get("account_login") or "").strip()
            if not token or not account_login:
                return {}
            credentials_file = self._refresh_github_git_credentials_for_personal_access_token(
                token=token,
                host=host,
                account_login=account_login,
            )
        else:
            return {}
        return self._git_env_for_credentials_file(credentials_file, host)

    def _github_git_args_for_repo(self, repo_url: str) -> list[str]:
        context = self._github_repo_auth_context(repo_url)
        if context is None:
            return []
        mode, host, auth_payload = context
        if mode == GITHUB_CONNECTION_MODE_GITHUB_APP:
            installation_id = int(auth_payload.get("installation_id") or 0)
            if installation_id <= 0:
                return []
            credentials_file = self._refresh_github_git_credentials(installation_id, host)
        elif mode == GITHUB_CONNECTION_MODE_PERSONAL_ACCESS_TOKEN:
            token = str(auth_payload.get("personal_access_token") or "").strip()
            account_login = str(auth_payload.get("account_login") or "").strip()
            if not token or not account_login:
                return []
            credentials_file = self._refresh_github_git_credentials_for_personal_access_token(
                token=token,
                host=host,
                account_login=account_login,
            )
        else:
            return []
        return [
            "--git-credential-file",
            credentials_file,
            "--git-credential-host",
            host,
        ]

    def _github_git_identity_env_vars_for_repo(self, repo_url: str) -> list[str]:
        context = self._github_repo_auth_context(repo_url)
        if context is None:
            return []
        mode, _host, auth_payload = context
        if mode != GITHUB_CONNECTION_MODE_PERSONAL_ACCESS_TOKEN:
            return []

        git_user_name = str(auth_payload.get("git_user_name") or auth_payload.get("account_name") or "").strip()
        if not git_user_name:
            git_user_name = str(auth_payload.get("account_login") or "").strip()
        git_user_email = str(auth_payload.get("git_user_email") or auth_payload.get("account_email") or "").strip()
        if not git_user_name or not git_user_email:
            return []
        return [
            f"AGENT_HUB_GIT_USER_NAME={git_user_name}",
            f"AGENT_HUB_GIT_USER_EMAIL={git_user_email}",
        ]

    def _openai_account_payload(self) -> dict[str, Any]:
        account_connected, auth_mode = _read_codex_auth(self.openai_codex_auth_file)
        updated_at = ""
        if self.openai_codex_auth_file.exists():
            try:
                updated_at = _iso_from_timestamp(self.openai_codex_auth_file.stat().st_mtime)
            except OSError:
                updated_at = ""
        return {
            "account_connected": account_connected,
            "account_auth_mode": auth_mode,
            "account_updated_at": updated_at,
        }

    def openai_auth_status(self) -> dict[str, Any]:
        api_key = _read_openai_api_key(self.openai_credentials_file)
        updated_at = ""
        if self.openai_credentials_file.exists():
            try:
                updated_at = _iso_from_timestamp(self.openai_credentials_file.stat().st_mtime)
            except OSError:
                updated_at = ""
        account_payload = self._openai_account_payload()
        return {
            "provider": "openai",
            "connected": bool(api_key),
            "key_hint": _mask_secret(api_key) if api_key else "",
            "updated_at": updated_at,
            "account_connected": account_payload["account_connected"],
            "account_auth_mode": account_payload["account_auth_mode"],
            "account_updated_at": account_payload["account_updated_at"],
        }

    def github_auth_status(self) -> dict[str, Any]:
        installation = self._github_connected_installation()
        personal_access_tokens = self._github_connected_personal_access_tokens()
        personal_access = personal_access_tokens[0] if personal_access_tokens else None
        app_configured = self.github_app_settings is not None and not self.github_app_settings_error

        installation_id = int(installation.get("installation_id") or 0) if installation else 0
        account_login = str(installation.get("account_login") or "") if installation else ""
        account_type = str(installation.get("account_type") or "") if installation else ""
        repository_selection = str(installation.get("repository_selection") or "") if installation else ""
        personal_access_token = str(personal_access.get("personal_access_token") or "") if personal_access else ""
        personal_access_host = str(personal_access.get("host") or "") if personal_access else ""
        personal_access_login = str(personal_access.get("account_login") or "") if personal_access else ""
        personal_access_name = str(personal_access.get("account_name") or "") if personal_access else ""
        personal_access_email = str(personal_access.get("account_email") or "") if personal_access else ""
        personal_access_account_id = str(personal_access.get("account_id") or "") if personal_access else ""
        personal_access_git_user_name = str(personal_access.get("git_user_name") or "") if personal_access else ""
        personal_access_git_user_email = str(personal_access.get("git_user_email") or "") if personal_access else ""
        personal_access_scopes = str(personal_access.get("token_scopes") or "") if personal_access else ""
        personal_access_verified_at = str(personal_access.get("verified_at") or "") if personal_access else ""

        connected_via_app = bool(installation and installation_id > 0 and app_configured)
        connected_via_pat = bool(personal_access_tokens)
        connection_mode = ""
        connection_host = ""
        if connected_via_pat:
            connection_mode = GITHUB_CONNECTION_MODE_PERSONAL_ACCESS_TOKEN
            connection_host = personal_access_host
        elif connected_via_app:
            connection_mode = GITHUB_CONNECTION_MODE_GITHUB_APP
            connection_host = self._github_provider_host()

        updated_path: Path | None = None
        if connection_mode == GITHUB_CONNECTION_MODE_PERSONAL_ACCESS_TOKEN and self.github_personal_access_token_file.exists():
            updated_path = self.github_personal_access_token_file
        elif connection_mode == GITHUB_CONNECTION_MODE_GITHUB_APP and self.github_app_installation_file.exists():
            updated_path = self.github_app_installation_file
        elif self.github_personal_access_token_file.exists():
            updated_path = self.github_personal_access_token_file
        elif self.github_app_installation_file.exists():
            updated_path = self.github_app_installation_file

        updated_at = ""
        if updated_path is not None:
            try:
                updated_at = _iso_from_timestamp(updated_path.stat().st_mtime)
            except OSError:
                updated_at = ""

        personal_access_entries: list[dict[str, Any]] = []
        for token_record in personal_access_tokens:
            token_value = str(token_record.get("personal_access_token") or "").strip()
            personal_access_entries.append(
                {
                    "token_id": str(token_record.get("token_id") or "").strip(),
                    "token_hint": _mask_secret(token_value) if token_value else "",
                    "host": str(token_record.get("host") or "").strip(),
                    "account_login": str(token_record.get("account_login") or "").strip(),
                    "account_name": str(token_record.get("account_name") or "").strip(),
                    "account_email": str(token_record.get("account_email") or "").strip(),
                    "account_id": str(token_record.get("account_id") or "").strip(),
                    "git_user_name": str(token_record.get("git_user_name") or "").strip(),
                    "git_user_email": str(token_record.get("git_user_email") or "").strip(),
                    "token_scopes": str(token_record.get("token_scopes") or "").strip(),
                    "verified_at": str(token_record.get("verified_at") or "").strip(),
                    "connected_at": str(token_record.get("connected_at") or "").strip(),
                    "owner_scopes": _empty_list(token_record.get("owner_scopes")),
                }
            )

        return {
            "provider": "github",
            "connection_mode": connection_mode,
            "connection_host": connection_host,
            "app_configured": app_configured,
            "app_slug": self.github_app_settings.app_slug if self.github_app_settings else "",
            "install_url": self._github_install_url(),
            "connected": bool(connected_via_app or connected_via_pat),
            "installation_id": installation_id,
            "installation_account_login": account_login,
            "installation_account_type": account_type,
            "repository_selection": repository_selection,
            "personal_access_token_hint": _mask_secret(personal_access_token) if personal_access_token else "",
            "personal_access_token_host": personal_access_host,
            "personal_access_token_user_login": personal_access_login,
            "personal_access_token_user_name": personal_access_name,
            "personal_access_token_user_email": personal_access_email,
            "personal_access_token_user_id": personal_access_account_id,
            "personal_access_token_git_user_name": personal_access_git_user_name,
            "personal_access_token_git_user_email": personal_access_git_user_email,
            "personal_access_token_scopes": personal_access_scopes,
            "personal_access_token_verified_at": personal_access_verified_at,
            "personal_access_token_owner_scopes": _empty_list(personal_access.get("owner_scopes")) if personal_access else [],
            "personal_access_token_count": len(personal_access_entries),
            "personal_access_tokens": personal_access_entries,
            "updated_at": updated_at,
            "error": str(self.github_app_settings_error or ""),
        }

    def _chat_title_generation_auth(self) -> tuple[str, str]:
        account_connected, _ = _read_codex_auth(self.openai_codex_auth_file)
        if account_connected:
            return CHAT_TITLE_AUTH_MODE_ACCOUNT, ""
        api_key = _read_openai_api_key(self.openai_credentials_file) or ""
        if api_key:
            return CHAT_TITLE_AUTH_MODE_API_KEY, api_key
        return CHAT_TITLE_AUTH_MODE_NONE, ""

    def _generate_chat_title_with_resolved_auth(
        self,
        auth_mode: str,
        api_key: str,
        user_prompts: list[str],
    ) -> tuple[str, str]:
        if auth_mode == CHAT_TITLE_AUTH_MODE_ACCOUNT:
            title = _codex_generate_chat_title(
                host_agent_home=self.host_agent_home,
                host_codex_dir=self.host_codex_dir,
                user_prompts=user_prompts,
                max_chars=CHAT_TITLE_MAX_CHARS,
            )
            return title, CHAT_TITLE_ACCOUNT_MODEL
        if auth_mode == CHAT_TITLE_AUTH_MODE_API_KEY:
            title = _openai_generate_chat_title(
                api_key=api_key,
                user_prompts=user_prompts,
                max_chars=CHAT_TITLE_MAX_CHARS,
            )
            return title, CHAT_TITLE_OPENAI_MODEL
        raise RuntimeError(CHAT_TITLE_NO_CREDENTIALS_ERROR)

    def auth_settings_payload(self) -> dict[str, Any]:
        return {
            "providers": {
                "openai": self.openai_auth_status(),
                "github": self.github_auth_status(),
            }
        }

    def test_openai_chat_title_generation(self, prompt: Any) -> dict[str, Any]:
        submitted = _compact_whitespace(str(prompt or "")).strip()
        if not submitted:
            raise HTTPException(status_code=400, detail="prompt is required.")

        auth_status = self.openai_auth_status()
        auth_mode, api_key = self._chat_title_generation_auth()
        connectivity = {
            "api_key_connected": bool(auth_status.get("connected")),
            "api_key_hint": str(auth_status.get("key_hint") or ""),
            "api_key_updated_at": str(auth_status.get("updated_at") or ""),
            "account_connected": bool(auth_status.get("account_connected")),
            "account_auth_mode": str(auth_status.get("account_auth_mode") or ""),
            "account_updated_at": str(auth_status.get("account_updated_at") or ""),
            "title_generation_auth_mode": auth_mode,
        }

        issues: list[str] = []
        model = (
            CHAT_TITLE_OPENAI_MODEL
            if auth_mode == CHAT_TITLE_AUTH_MODE_API_KEY
            else CHAT_TITLE_ACCOUNT_MODEL
            if auth_mode == CHAT_TITLE_AUTH_MODE_ACCOUNT
            else ""
        )
        if auth_mode == CHAT_TITLE_AUTH_MODE_NONE:
            error = CHAT_TITLE_NO_CREDENTIALS_ERROR
            issues.append(error)
            return {
                "ok": False,
                "title": "",
                "model": model,
                "prompt": submitted,
                "error": error,
                "issues": issues,
                "connectivity": connectivity,
            }

        try:
            resolved_title, model = self._generate_chat_title_with_resolved_auth(
                auth_mode=auth_mode,
                api_key=api_key,
                user_prompts=[submitted],
            )
        except Exception as exc:
            error = str(exc)
            if error:
                issues.append(error)
            return {
                "ok": False,
                "title": "",
                "model": model,
                "prompt": submitted,
                "error": error,
                "issues": issues,
                "connectivity": connectivity,
            }

        return {
            "ok": True,
            "title": resolved_title,
            "model": model,
            "prompt": submitted,
            "error": "",
            "issues": issues,
            "connectivity": connectivity,
        }

    @staticmethod
    def _dedupe_entries(entries: list[str]) -> list[str]:
        deduped: list[str] = []
        seen: set[str] = set()
        for entry in entries:
            normalized = str(entry or "").strip()
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            deduped.append(normalized)
        return deduped

    def _auto_config_prompt(self, repo_url: str, branch: str) -> str:
        return _render_prompt_template(
            PROMPT_AUTO_CONFIGURE_PROJECT_FILE,
            repo_url=repo_url,
            branch=branch,
        )

    def _normalize_auto_config_setup_script(self, raw_script: Any) -> str:
        script = str(raw_script or "").replace("\r\n", "\n").replace("\r", "\n")
        commands = [line.strip() for line in script.split("\n") if line.strip()]
        if not commands:
            return ""
        lowered = [line.lower() for line in commands]
        has_apt_install = any(("apt-get install" in line) or ("apt install" in line) for line in lowered)
        has_apt_update = any(("apt-get update" in line) or ("apt update" in line) for line in lowered)
        if has_apt_install and not has_apt_update:
            commands.insert(0, "apt-get update")
        return "\n".join(commands)

    @staticmethod
    def _normalize_auto_config_shell_path(path_value: str) -> str:
        normalized = str(path_value or "").strip().strip("\"'").replace("\\", "/")
        while normalized.startswith("./"):
            normalized = normalized[2:]
        return normalized.rstrip("/") or "."

    @staticmethod
    def _extract_auto_config_option_path(command: str, pattern: re.Pattern[str]) -> str:
        match = pattern.search(str(command or ""))
        if not match:
            return ""
        return HubState._normalize_auto_config_shell_path(str(match.group(1) or ""))

    @staticmethod
    def _auto_config_setup_scope_matches(left_scope: str, right_scope: str) -> bool:
        left = HubState._normalize_auto_config_shell_path(left_scope)
        right = HubState._normalize_auto_config_shell_path(right_scope)
        if left == right:
            return True
        if left == "." or right == ".":
            return left == right
        return left.endswith(f"/{right}") or right.endswith(f"/{left}")

    def _auto_config_setup_signature_for_command(self, command: str, cwd: str) -> tuple[str, str] | None:
        normalized = _compact_whitespace(str(command or "")).strip()
        if not normalized:
            return None
        normalized_cwd = self._normalize_auto_config_shell_path(cwd)

        if AUTO_CONFIG_SETUP_UV_SYNC_RE.search(normalized):
            return "uv_sync", normalized_cwd

        if AUTO_CONFIG_SETUP_YARN_INSTALL_RE.search(normalized):
            cwd_path = self._extract_auto_config_option_path(normalized, AUTO_CONFIG_SETUP_CWD_RE)
            return "yarn_install", self._normalize_auto_config_shell_path(cwd_path or normalized_cwd)

        if AUTO_CONFIG_SETUP_NPM_CI_RE.search(normalized):
            prefix_path = self._extract_auto_config_option_path(normalized, AUTO_CONFIG_SETUP_PREFIX_RE)
            return "npm_ci", self._normalize_auto_config_shell_path(prefix_path or normalized_cwd)

        return None

    def _auto_config_setup_signatures_from_shell(self, shell_command: str) -> set[tuple[str, str]]:
        signatures: set[tuple[str, str]] = set()
        cwd = "."
        for segment in AUTO_CONFIG_SETUP_CHAIN_SPLIT_RE.split(str(shell_command or "").strip()):
            normalized = _compact_whitespace(segment).strip()
            if not normalized:
                continue
            cd_match = AUTO_CONFIG_SETUP_CD_RE.match(normalized)
            if cd_match:
                cwd = self._normalize_auto_config_shell_path(str(cd_match.group(1) or ""))
                continue
            signature = self._auto_config_setup_signature_for_command(normalized, cwd)
            if signature is not None:
                signatures.add(signature)
        return signatures

    @staticmethod
    def _dockerfile_run_commands(dockerfile: Path) -> list[str]:
        try:
            raw_text = dockerfile.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            return []

        instructions: list[str] = []
        current = ""
        for raw_line in raw_text.splitlines():
            stripped = raw_line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if current:
                current = f"{current} {stripped}"
            else:
                current = stripped
            if current.endswith("\\"):
                current = current[:-1].rstrip()
                continue
            instructions.append(current)
            current = ""
        if current:
            instructions.append(current)

        run_commands: list[str] = []
        for instruction in instructions:
            lowered = instruction.lower()
            if not lowered.startswith("run "):
                continue
            run_commands.append(instruction[4:].strip())
        return run_commands

    def _auto_config_setup_signatures_from_repo_dockerfile(self, dockerfile: Path) -> set[tuple[str, str]]:
        signatures: set[tuple[str, str]] = set()
        for run_command in self._dockerfile_run_commands(dockerfile):
            signatures.update(self._auto_config_setup_signatures_from_shell(run_command))
        return signatures

    def _auto_config_signature_in(self, signature: tuple[str, str], known: set[tuple[str, str]]) -> bool:
        kind, scope = signature
        for known_kind, known_scope in known:
            if kind != known_kind:
                continue
            if self._auto_config_setup_scope_matches(scope, known_scope):
                return True
        return False

    def _resolve_auto_config_repo_dockerfile(self, workspace: Path, base_image_value: str) -> Path | None:
        raw_value = str(base_image_value or "").strip()
        if not raw_value:
            return None
        candidate = (workspace / raw_value).resolve()
        workspace_root = workspace.resolve()
        try:
            candidate.relative_to(workspace_root)
        except ValueError:
            return None
        dockerfile = candidate / "Dockerfile" if candidate.is_dir() else candidate
        if not dockerfile.is_file():
            return None
        return dockerfile

    def _dedupe_setup_script_commands_present_in_repo_dockerfile(
        self,
        workspace: Path,
        base_image_mode: str,
        base_image_value: str,
        setup_script: str,
    ) -> str:
        normalized_script = str(setup_script or "").strip()
        if not normalized_script:
            return ""
        if base_image_mode != "repo_path":
            return normalized_script

        dockerfile = self._resolve_auto_config_repo_dockerfile(workspace, base_image_value)
        if dockerfile is None:
            return normalized_script
        docker_signatures = self._auto_config_setup_signatures_from_repo_dockerfile(dockerfile)
        if not docker_signatures:
            return normalized_script

        kept_lines: list[str] = []
        for raw_line in normalized_script.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            line_signatures = self._auto_config_setup_signatures_from_shell(line)
            if line_signatures and all(self._auto_config_signature_in(sig, docker_signatures) for sig in line_signatures):
                continue
            kept_lines.append(line)
        return "\n".join(kept_lines)

    @staticmethod
    def _is_auto_config_cache_signal_file(path: Path, workspace: Path) -> bool:
        try:
            relative = path.relative_to(workspace)
        except ValueError:
            return False
        parts = [part.lower() for part in relative.parts]
        if not parts:
            return False
        if any(part in AUTO_CONFIG_CACHE_SIGNAL_IGNORED_PATH_PARTS for part in parts[:-1]):
            return False
        if any(part in AUTO_CONFIG_CACHE_SIGNAL_DOC_DIRS for part in parts[:-1]):
            return False
        filename = parts[-1]
        if filename in AUTO_CONFIG_CACHE_SIGNAL_FILENAMES:
            return True
        if "dockerfile" in filename:
            return True
        return path.suffix.lower() in AUTO_CONFIG_CACHE_SIGNAL_SUFFIXES

    def _detected_auto_config_cache_backends(self, workspace: Path) -> set[str]:
        detected: set[str] = set()
        files_scanned = 0
        for root, dirs, files in os.walk(workspace):
            dirs[:] = [name for name in dirs if name not in AUTO_CONFIG_CACHE_SIGNAL_IGNORED_DIRS]
            for filename in files:
                path = Path(root) / filename
                if not self._is_auto_config_cache_signal_file(path, workspace):
                    continue
                files_scanned += 1
                if files_scanned > AUTO_CONFIG_CACHE_SIGNAL_MAX_FILES:
                    return detected
                try:
                    if path.stat().st_size > 1_500_000:
                        continue
                    text = path.read_text(encoding="utf-8", errors="ignore")
                except OSError:
                    continue
                lowered = text.lower()
                if "ccache" in lowered and any(pattern.search(text) for pattern in AUTO_CONFIG_CCACHE_SIGNAL_PATTERNS):
                    detected.add("ccache")
                if "sccache" in lowered and any(pattern.search(text) for pattern in AUTO_CONFIG_SCCACHE_SIGNAL_PATTERNS):
                    detected.add("sccache")
                if len(detected) == 2:
                    return detected
        return detected

    @staticmethod
    def _cache_mount_backend_from_container_path(container_path: str) -> str:
        normalized = str(container_path or "").strip().replace("\\", "/").rstrip("/").lower()
        if not normalized:
            return ""
        if re.search(r"(?:^|/)\.?ccache(?:$|/)", normalized):
            return "ccache"
        if re.search(r"(?:^|/)\.cache/sccache(?:$|/)", normalized):
            return "sccache"
        if re.search(r"(?:^|/)\.?sccache(?:$|/)", normalized):
            return "sccache"
        if re.search(r"(?:^|/)\.scache(?:$|/)", normalized):
            return "sccache"
        return ""

    @staticmethod
    def _cache_mount_backend_from_entry(entry: str) -> str:
        if ":" not in entry:
            return ""
        _host, container_raw = entry.split(":", 1)
        return HubState._cache_mount_backend_from_container_path(container_raw)

    def _augment_auto_config_cache_mounts(self, workspace: Path, rw_mounts: list[str]) -> list[str]:
        mounted = self._dedupe_entries(list(rw_mounts))
        detected = self._detected_auto_config_cache_backends(workspace)
        filtered: list[str] = []
        existing: set[str] = set()
        for entry in mounted:
            backend = self._cache_mount_backend_from_entry(entry)
            if backend:
                continue
            if entry in existing:
                continue
            filtered.append(entry)
            existing.add(entry)

        container_home = DEFAULT_CONTAINER_HOME
        cache_specs = [
            ("ccache", Path.home().resolve() / ".ccache", f"{container_home}/.ccache"),
            ("sccache", Path.home().resolve() / ".cache" / "sccache", f"{container_home}/.cache/sccache"),
        ]
        for token, host_path, container_path in cache_specs:
            if token not in detected:
                continue
            try:
                host_path.mkdir(parents=True, exist_ok=True)
            except OSError:
                continue
            entry = f"{host_path}:{container_path}"
            if entry in existing:
                continue
            filtered.append(entry)
            existing.add(entry)
        return filtered

    def _normalize_auto_config_repo_path(self, workspace: Path, raw_value: Any) -> str:
        value = str(raw_value or "").strip()
        if not value:
            raise HTTPException(status_code=400, detail="Auto-config recommendation requires base_image_value for repo_path mode.")
        candidate = Path(value).expanduser()
        resolved = candidate.resolve() if candidate.is_absolute() else (workspace / candidate).resolve()
        workspace_root = workspace.resolve()
        try:
            relative = resolved.relative_to(workspace_root)
        except ValueError as exc:
            raise HTTPException(
                status_code=400,
                detail=f"Auto-config base_image_value must stay inside the repository: {value}",
            ) from exc
        if not (resolved.is_file() or resolved.is_dir()):
            raise HTTPException(
                status_code=400,
                detail=f"Auto-config base_image_value does not exist in repository: {value}",
            )
        return relative.as_posix()

    @staticmethod
    def _normalize_auto_config_mount_path(path_value: str) -> str:
        normalized = str(path_value or "").strip().strip("\"'").replace("\\", "/")
        if normalized.startswith("/"):
            normalized = normalized.split(":", 1)[0]
        while "//" in normalized:
            normalized = normalized.replace("//", "/")
        if len(normalized) > 1:
            normalized = normalized.rstrip("/")
        return normalized.lower()

    @classmethod
    def _is_auto_config_docker_socket_path(cls, path_value: str) -> bool:
        normalized = cls._normalize_auto_config_mount_path(path_value)
        if not normalized:
            return False
        if normalized in AUTO_CONFIG_DOCKER_SOCKET_PATHS:
            return True
        return normalized.endswith("/docker.sock")

    def _normalize_auto_config_mounts(self, entries: list[str], direction: str) -> list[str]:
        normalized_entries: list[str] = []
        home_root = Path.home().resolve()
        for raw_entry in entries:
            if ":" not in raw_entry:
                raise HTTPException(status_code=400, detail=f"Invalid auto-config {direction} mount '{raw_entry}'.")
            host_raw, container_raw = raw_entry.split(":", 1)
            if self._is_auto_config_docker_socket_path(host_raw) or self._is_auto_config_docker_socket_path(
                container_raw
            ):
                continue
            container = container_raw.strip()
            if not container.startswith("/"):
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid auto-config container path for {direction} mount '{raw_entry}'.",
                )
            host_path = Path(host_raw).expanduser()
            if not host_path.exists():
                try:
                    host_path_resolved = host_path.resolve()
                except OSError:
                    host_path_resolved = host_path
                should_create = False
                try:
                    host_path_resolved.relative_to(home_root)
                    should_create = True
                except ValueError:
                    should_create = False
                if should_create:
                    try:
                        host_path_resolved.mkdir(parents=True, exist_ok=True)
                        host_path = host_path_resolved
                    except OSError:
                        pass
            normalized_entries.append(f"{host_path}:{container}")
        return _parse_mounts(normalized_entries, direction)

    def _normalize_auto_config_recommendation(self, raw_payload: dict[str, Any], workspace: Path) -> dict[str, Any]:
        if not isinstance(raw_payload, dict):
            raise HTTPException(status_code=400, detail="Auto-config output must be a JSON object.")

        base_image_mode = _normalize_base_image_mode(raw_payload.get("base_image_mode"))
        base_image_value = str(raw_payload.get("base_image_value") or "").strip()
        if base_image_mode == "repo_path":
            base_image_value = self._normalize_auto_config_repo_path(workspace, base_image_value)

        setup_script = self._normalize_auto_config_setup_script(raw_payload.get("setup_script"))
        setup_script = self._dedupe_setup_script_commands_present_in_repo_dockerfile(
            workspace=workspace,
            base_image_mode=base_image_mode,
            base_image_value=base_image_value,
            setup_script=setup_script,
        )
        default_ro_mounts = self._normalize_auto_config_mounts(
            _empty_list(raw_payload.get("default_ro_mounts")),
            "default read-only mount",
        )
        default_rw_mounts = self._normalize_auto_config_mounts(
            _empty_list(raw_payload.get("default_rw_mounts")),
            "default read-write mount",
        )
        default_rw_mounts = self._augment_auto_config_cache_mounts(workspace, default_rw_mounts)
        default_env_vars = _parse_env_vars(_empty_list(raw_payload.get("default_env_vars")))

        notes_raw = _compact_whitespace(str(raw_payload.get("notes") or "")).strip()
        if len(notes_raw) > AUTO_CONFIG_NOTES_MAX_CHARS:
            notes = notes_raw[: AUTO_CONFIG_NOTES_MAX_CHARS - 1].rstrip() + "…"
        else:
            notes = notes_raw

        return {
            "base_image_mode": base_image_mode,
            "base_image_value": base_image_value,
            "setup_script": setup_script,
            "default_ro_mounts": self._dedupe_entries(default_ro_mounts),
            "default_rw_mounts": self._dedupe_entries(default_rw_mounts),
            "default_env_vars": self._dedupe_entries(default_env_vars),
            "notes": notes,
        }

    @staticmethod
    def _dockerfile_path_score(relative_path: str) -> tuple[int, int, str]:
        normalized = str(relative_path or "").strip().replace("\\", "/")
        lowered = normalized.lower()
        parts = [part for part in lowered.split("/") if part]
        score = 0
        filename = parts[-1] if parts else lowered
        if filename == "dockerfile":
            score += 40
        elif "dockerfile" in filename:
            score += 20
        if "ci" in parts:
            score += 80
        if "docker" in lowered:
            score += 40
        if "devcontainer" in parts:
            score += 60
        if any(part in {"x86", "amd64"} for part in parts):
            score += 15
        if any(part in {"test", "tests", "example", "examples"} for part in parts):
            score -= 20
        return score, -len(parts), normalized

    def _infer_repo_dockerfile_path(self, workspace: Path) -> str:
        candidates: list[tuple[int, int, str]] = []
        ignored_dirs = {
            ".git",
            ".hg",
            ".svn",
            ".venv",
            "venv",
            "node_modules",
            "build",
            "dist",
            "out",
            "target",
        }
        for root, dirs, files in os.walk(workspace):
            dirs[:] = [name for name in dirs if name not in ignored_dirs]
            for filename in files:
                lowered = filename.lower()
                if lowered != "dockerfile" and "dockerfile" not in lowered:
                    continue
                absolute_path = Path(root) / filename
                try:
                    relative_path = absolute_path.resolve().relative_to(workspace.resolve()).as_posix()
                except ValueError:
                    continue
                candidates.append(self._dockerfile_path_score(relative_path))
        if not candidates:
            return ""
        candidates.sort(reverse=True)
        return candidates[0][2]

    @staticmethod
    def _iter_text_files_for_make_targets(workspace: Path) -> list[Path]:
        preferred_roots = [
            workspace / ".github" / "workflows",
            workspace / "ci",
            workspace / "docker",
            workspace / "scripts",
        ]
        output: list[Path] = []
        seen: set[Path] = set()
        for filename in ("AGENTS.md", "README.md", "README", "Makefile", "makefile"):
            candidate = workspace / filename
            if candidate.is_file() and candidate not in seen:
                seen.add(candidate)
                output.append(candidate)
        for root in preferred_roots:
            if not root.exists():
                continue
            for path in root.rglob("*"):
                if not path.is_file():
                    continue
                if path in seen:
                    continue
                seen.add(path)
                output.append(path)
        return output

    @staticmethod
    def _make_target_context_weight(target: str, context: str) -> int:
        score = 3
        lowered_target = str(target or "").strip().lower()
        lowered_context = str(context or "").strip().lower()
        if not lowered_target:
            return 0

        if any(token in lowered_context for token in ("at a minimum", "minimum", "first", "before you can", "bootstrap")):
            score += 6
        if any(token in lowered_context for token in ("cross build", "cross-build", "host tools", "toolchain")):
            score += 3
        if any(token in lowered_context for token in ("run:", "steps:", "workflow", "pipeline", "ci")):
            score += 2
        if lowered_target in {"check", "test", "tests", "lint", "format", "clean"}:
            score -= 3
        if any(token in lowered_target for token in ("test", "lint", "format", "clean")):
            score -= 2
        return score

    def _infer_make_sh_target(self, workspace: Path) -> str:
        make_script = workspace / "make.sh"
        if not make_script.is_file():
            return ""

        pattern = re.compile(r"(?:^|[\s\"'`])(?:\./)?make\.sh\s+([A-Za-z0-9_.:-]+)")
        counts: dict[str, int] = {}
        for path in self._iter_text_files_for_make_targets(workspace):
            try:
                if path.stat().st_size > 1_000_000:
                    continue
                text = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            for match in pattern.finditer(text):
                target = str(match.group(1) or "").strip()
                if not target or target.startswith("-"):
                    continue
                context_start = max(0, match.start() - 120)
                context_end = min(len(text), match.end() + 120)
                context = text[context_start:context_end]
                counts[target] = counts.get(target, 0) + self._make_target_context_weight(target, context)

        if counts:
            ranked = sorted(counts.items(), key=lambda item: (-item[1], len(item[0]), item[0]))
            return ranked[0][0]
        return ""

    def _suggest_make_sh_command(self, workspace: Path) -> str:
        make_script = workspace / "make.sh"
        if not make_script.is_file():
            return ""
        prefix = "./make.sh" if os.access(make_script, os.X_OK) else "bash make.sh"
        target = self._infer_make_sh_target(workspace)
        if target:
            return f"{prefix} {target}"
        return prefix

    def _apply_auto_config_repository_hints(
        self,
        recommendation: dict[str, Any],
        workspace: Path,
    ) -> dict[str, Any]:
        next_recommendation = dict(recommendation)
        dockerfile_path = self._infer_repo_dockerfile_path(workspace)
        current_mode = _normalize_base_image_mode(next_recommendation.get("base_image_mode"))
        current_value = str(next_recommendation.get("base_image_value") or "").strip()

        if dockerfile_path:
            dockerfile_score, _depth_score, _path = self._dockerfile_path_score(dockerfile_path)
            should_use_repo_dockerfile = (
                current_mode != "repo_path" or not current_value
            ) and (dockerfile_score >= AUTO_CONFIG_REPO_DOCKERFILE_MIN_SCORE or not current_value)
            if should_use_repo_dockerfile:
                next_recommendation["base_image_mode"] = "repo_path"
                next_recommendation["base_image_value"] = dockerfile_path

        make_command = self._suggest_make_sh_command(workspace)
        if make_command:
            setup_script = str(next_recommendation.get("setup_script") or "").strip()
            inferred_mode = _normalize_base_image_mode(next_recommendation.get("base_image_mode"))
            if not setup_script:
                next_recommendation["setup_script"] = make_command
            elif inferred_mode == "repo_path" and " " in make_command:
                next_recommendation["setup_script"] = make_command
            elif "make.sh" not in setup_script:
                next_recommendation["setup_script"] = f"{setup_script}\n{make_command}"

        notes = _compact_whitespace(str(next_recommendation.get("notes") or "")).strip()
        if dockerfile_path:
            note_addition = f"selected repository Dockerfile: {dockerfile_path}"
            notes = f"{notes}; {note_addition}" if notes else note_addition
        next_recommendation["notes"] = notes
        return next_recommendation

    def _run_temporary_auto_config_chat(self, workspace: Path, repo_url: str, branch: str) -> dict[str, Any]:
        account_connected, _ = _read_codex_auth(self.openai_codex_auth_file)
        if not account_connected:
            raise HTTPException(status_code=409, detail=AUTO_CONFIG_NOT_CONNECTED_ERROR)
        try:
            codex_exec = _resolve_codex_executable(self.host_codex_dir)
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

        prompt = self._auto_config_prompt(repo_url, branch)
        output_file = self.host_codex_dir / f"auto-config-{uuid.uuid4().hex}.json"
        env = os.environ.copy()
        env["HOME"] = str(self.host_agent_home)
        env["CODEX_HOME"] = str(self.host_codex_dir)
        cmd = [
            codex_exec,
            "exec",
            "--skip-git-repo-check",
            "--cd",
            str(workspace),
            "--sandbox",
            "workspace-write",
            "--output-last-message",
            str(output_file),
            prompt,
        ]
        try:
            result = subprocess.run(
                cmd,
                check=False,
                text=True,
                capture_output=True,
                env=env,
                timeout=max(20.0, float(AUTO_CONFIG_CHAT_TIMEOUT_SECONDS)),
            )
        except subprocess.TimeoutExpired as exc:
            raise HTTPException(status_code=504, detail="Temporary auto-config chat timed out.") from exc

        output_text = ((result.stdout or "") + "\n" + (result.stderr or "")).strip()
        if result.returncode != 0:
            detail = _codex_exec_error_message(output_text)
            raise HTTPException(status_code=502, detail=f"Temporary auto-config chat failed: {detail}")

        try:
            raw_payload_text = output_file.read_text(encoding="utf-8", errors="ignore").strip()
        except OSError as exc:
            raise HTTPException(status_code=502, detail=AUTO_CONFIG_MISSING_OUTPUT_ERROR) from exc
        finally:
            try:
                output_file.unlink()
            except OSError:
                pass
        if not raw_payload_text:
            raise HTTPException(status_code=502, detail=AUTO_CONFIG_MISSING_OUTPUT_ERROR)

        try:
            parsed_payload = _parse_json_object_from_text(raw_payload_text)
        except ValueError as exc:
            raise HTTPException(status_code=502, detail=AUTO_CONFIG_INVALID_OUTPUT_ERROR) from exc
        return {"payload": parsed_payload, "model": AUTO_CONFIG_MODEL}

    def auto_configure_project(self, repo_url: Any, default_branch: Any = None) -> dict[str, Any]:
        normalized_repo_url = str(repo_url or "").strip()
        if not normalized_repo_url:
            raise HTTPException(status_code=400, detail="repo_url is required.")

        requested_branch = str(default_branch or "").strip()
        git_env = self._github_git_env_for_repo(normalized_repo_url)
        sanitized_git_env = {
            "GIT_CONFIG_COUNT": "0",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_TERMINAL_PROMPT": "0",
        }
        authenticated_git_env = dict(sanitized_git_env)
        authenticated_git_env.update(git_env)
        resolved_branch = requested_branch or _detect_default_branch(
            normalized_repo_url,
            env=authenticated_git_env,
        )
        if not requested_branch and git_env and resolved_branch == "master":
            public_branch = _detect_default_branch(normalized_repo_url, env=sanitized_git_env)
            if public_branch:
                resolved_branch = public_branch

        with tempfile.TemporaryDirectory(prefix="agent-hub-auto-config-", dir=str(self.data_dir)) as temp_dir:
            workspace = Path(temp_dir) / "repo"
            env_candidates: list[dict[str, str]] = [authenticated_git_env]
            if git_env:
                env_candidates.append(sanitized_git_env)

            def run_clone(cmd: list[str]) -> subprocess.CompletedProcess:
                last_result = subprocess.CompletedProcess(cmd, 1, "", "")
                for env_candidate in env_candidates:
                    if workspace.exists():
                        self._delete_path(workspace)
                    result = _run(cmd, capture=True, check=False, env=env_candidate)
                    if result.returncode == 0:
                        return result
                    last_result = result
                return last_result

            clone_cmd_with_branch = [
                "git",
                "clone",
                "--depth",
                "1",
                "--branch",
                resolved_branch,
                normalized_repo_url,
                str(workspace),
            ]
            clone_result = run_clone(clone_cmd_with_branch)
            if clone_result.returncode != 0:
                if requested_branch:
                    detail = ((clone_result.stdout or "") + (clone_result.stderr or "")).strip()
                    raise HTTPException(
                        status_code=400,
                        detail=(
                            f"Unable to clone repository branch '{requested_branch}'. "
                            f"{detail or 'git clone failed.'}"
                        ),
                    )

                clone_cmd_default = ["git", "clone", "--depth", "1", normalized_repo_url, str(workspace)]
                clone_result = run_clone(clone_cmd_default)
                if clone_result.returncode != 0:
                    detail = ((clone_result.stdout or "") + (clone_result.stderr or "")).strip()
                    raise HTTPException(
                        status_code=400,
                        detail=f"Unable to clone repository for auto-configure. {detail or 'git clone failed.'}",
                    )

                head_result = _run_for_repo(
                    ["rev-parse", "--abbrev-ref", "HEAD"],
                    workspace,
                    capture=True,
                    check=False,
                    env=sanitized_git_env,
                )
                if head_result.returncode == 0 and head_result.stdout.strip():
                    resolved_branch = head_result.stdout.strip()

            chat_result = self._run_temporary_auto_config_chat(workspace, normalized_repo_url, resolved_branch)
            recommendation = self._normalize_auto_config_recommendation(chat_result.get("payload") or {}, workspace)
            recommendation = self._apply_auto_config_repository_hints(recommendation, workspace)
            recommendation = self._normalize_auto_config_recommendation(recommendation, workspace)

        recommendation["default_branch"] = resolved_branch
        recommendation["analysis_model"] = str(chat_result.get("model") or "")
        recommendation["analysis_auth_mode"] = CHAT_TITLE_AUTH_MODE_ACCOUNT
        recommendation["analyzed_repo_url"] = normalized_repo_url
        return recommendation

    def connect_openai(self, api_key: Any, verify: bool = True) -> dict[str, Any]:
        normalized = _normalize_openai_api_key(api_key)
        if verify:
            _verify_openai_api_key(normalized)
        _write_private_env_file(
            self.openai_credentials_file,
            f"OPENAI_API_KEY={json.dumps(normalized)}\n",
        )
        status = self.openai_auth_status()
        self._emit_auth_changed(reason="openai_api_key_connected")
        LOGGER.debug("OpenAI API key connected.")
        return status

    def disconnect_openai(self) -> dict[str, Any]:
        if self.openai_credentials_file.exists():
            try:
                self.openai_credentials_file.unlink()
            except OSError as exc:
                raise HTTPException(status_code=500, detail="Failed to remove stored OpenAI credentials.") from exc
        status = self.openai_auth_status()
        self._emit_auth_changed(reason="openai_api_key_disconnected")
        LOGGER.debug("OpenAI API key disconnected.")
        return status

    def list_github_app_installations(self) -> dict[str, Any]:
        status = self.github_auth_status()
        if not status.get("app_configured"):
            return {
                "app_configured": False,
                "app_slug": status.get("app_slug") or "",
                "install_url": status.get("install_url") or "",
                "installations": [],
                "connected_installation_id": int(status.get("installation_id") or 0),
                "error": str(status.get("error") or ""),
            }

        _response_status, payload_text = self._github_api_request(
            "GET",
            "/app/installations?per_page=100",
            auth_mode="app",
        )
        try:
            raw_payload = json.loads(payload_text) if payload_text else []
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=502, detail="GitHub API returned invalid installation list payload.") from exc
        if not isinstance(raw_payload, list):
            raise HTTPException(status_code=502, detail="GitHub API returned invalid installation list payload.")

        installations: list[dict[str, Any]] = []
        for item in raw_payload:
            if not isinstance(item, dict):
                continue
            installation_id = item.get("id")
            if not isinstance(installation_id, int) or installation_id <= 0:
                continue
            account = item.get("account")
            account_login = ""
            account_type = ""
            if isinstance(account, dict):
                account_login = str(account.get("login") or "")
                account_type = str(account.get("type") or "")
            installations.append(
                {
                    "id": installation_id,
                    "account_login": account_login,
                    "account_type": account_type,
                    "repository_selection": str(item.get("repository_selection") or ""),
                    "updated_at": str(item.get("updated_at") or ""),
                    "suspended_at": str(item.get("suspended_at") or ""),
                }
            )

        return {
            "app_configured": True,
            "app_slug": status.get("app_slug") or "",
            "install_url": status.get("install_url") or "",
            "installations": installations,
            "connected_installation_id": int(status.get("installation_id") or 0),
            "error": "",
        }

    def connect_github_app(self, installation_id: Any) -> dict[str, Any]:
        status = self.github_auth_status()
        if not status.get("app_configured"):
            detail = str(status.get("error") or "GitHub App is not configured on this server.")
            raise HTTPException(status_code=400, detail=detail)

        normalized_id = _normalize_github_installation_id(installation_id)
        _response_status, installation_payload_text = self._github_api_request(
            "GET",
            f"/app/installations/{normalized_id}",
            auth_mode="app",
        )
        try:
            installation_payload = json.loads(installation_payload_text) if installation_payload_text else {}
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=502, detail="GitHub API returned invalid installation payload.") from exc
        if not isinstance(installation_payload, dict):
            raise HTTPException(status_code=502, detail="GitHub API returned invalid installation payload.")

        account = installation_payload.get("account")
        account_login = ""
        account_type = ""
        if isinstance(account, dict):
            account_login = str(account.get("login") or "")
            account_type = str(account.get("type") or "")
        repository_selection = str(installation_payload.get("repository_selection") or "")

        self._clear_github_personal_access_token_state(remove_credentials=True)
        self._refresh_github_git_credentials(normalized_id, self._github_provider_host())
        record = {
            "installation_id": normalized_id,
            "account_login": account_login,
            "account_type": account_type,
            "repository_selection": repository_selection,
            "connected_at": _iso_now(),
        }
        _write_private_env_file(self.github_app_installation_file, json.dumps(record, indent=2) + "\n")
        status = self.github_auth_status()
        self._emit_auth_changed(reason="github_app_connected")
        LOGGER.debug("GitHub App installation connected: id=%s account=%s", normalized_id, account_login)
        return status

    def connect_github_personal_access_token(
        self,
        personal_access_token: Any,
        host: Any = "",
        owner_scopes: Any = None,
    ) -> dict[str, Any]:
        normalized_token = _normalize_github_personal_access_token(personal_access_token)
        host_candidate = str(host or "").strip()
        if not host_candidate:
            host_candidate = self._github_provider_host()
        normalized_host = _normalize_github_credential_host(host_candidate, field_name="host")
        normalized_owner_scopes = _normalize_github_owner_scopes(owner_scopes, field_name="owner_scopes")
        verification = self._verify_github_personal_access_token(normalized_token, normalized_host)
        account_login = verification["account_login"]

        self._clear_github_installation_state(remove_credentials=True)
        self._refresh_github_git_credentials_for_personal_access_token(
            token=normalized_token,
            host=normalized_host,
            account_login=account_login,
        )
        account_name = str(verification.get("account_name") or account_login).strip() or account_login
        account_email = str(verification.get("account_email") or "").strip()
        account_id = str(verification.get("account_id") or "").strip()
        connected_at = _iso_now()
        record = {
            "token_id": uuid.uuid4().hex,
            "host": normalized_host,
            "personal_access_token": normalized_token,
            "account_login": account_login,
            "account_name": account_name,
            "account_email": account_email,
            "account_id": account_id,
            "git_user_name": account_name,
            "git_user_email": account_email,
            "token_scopes": verification.get("token_scopes") or "",
            "verified_at": connected_at,
            "connected_at": connected_at,
            "owner_scopes": normalized_owner_scopes,
        }

        existing = self._github_connected_personal_access_tokens()
        filtered_existing: list[dict[str, Any]] = []
        for existing_record in existing:
            existing_host = str(existing_record.get("host") or "").strip().lower()
            existing_login = str(existing_record.get("account_login") or "").strip().lower()
            existing_owner_scopes = _normalize_github_owner_scopes(
                existing_record.get("owner_scopes"),
                field_name="owner_scopes",
            )
            if (
                existing_host == normalized_host
                and existing_login == account_login.lower()
                and existing_owner_scopes == normalized_owner_scopes
            ):
                continue
            filtered_existing.append(existing_record)

        self._persist_github_personal_access_tokens([record, *filtered_existing])
        status = self.github_auth_status()
        self._emit_auth_changed(reason="github_personal_access_token_connected")
        LOGGER.debug(
            "GitHub personal access token connected: host=%s account=%s owner_scopes=%s",
            normalized_host,
            account_login,
            ",".join(normalized_owner_scopes),
        )
        return status

    def disconnect_github_personal_access_token(self, token_id: Any) -> dict[str, Any]:
        normalized_token_id = str(token_id or "").strip()
        if not normalized_token_id:
            raise HTTPException(status_code=400, detail="token_id is required.")
        if len(normalized_token_id) > GITHUB_PERSONAL_ACCESS_TOKEN_ID_MAX_CHARS:
            raise HTTPException(status_code=400, detail="token_id is invalid.")

        existing = self._github_connected_personal_access_tokens()
        remaining = [record for record in existing if str(record.get("token_id") or "").strip() != normalized_token_id]
        if len(remaining) == len(existing):
            raise HTTPException(status_code=404, detail="GitHub personal access token not found.")

        if remaining:
            self._persist_github_personal_access_tokens(remaining)
            primary = remaining[0]
            token = str(primary.get("personal_access_token") or "").strip()
            host = str(primary.get("host") or "").strip()
            account_login = str(primary.get("account_login") or "").strip()
            if token and host and account_login:
                self._refresh_github_git_credentials_for_personal_access_token(
                    token=token,
                    host=host,
                    account_login=account_login,
                )
            else:
                self._clear_github_personal_access_token_state(remove_credentials=True)
        else:
            self._clear_github_personal_access_token_state(remove_credentials=True)

        status = self.github_auth_status()
        self._emit_auth_changed(reason="github_personal_access_token_disconnected")
        LOGGER.debug(
            "GitHub personal access token disconnected: token_id=%s remaining=%s",
            normalized_token_id,
            len(remaining),
        )
        return status

    def disconnect_github_app(self) -> dict[str, Any]:
        for path in [
            self.github_app_installation_file,
            self.github_personal_access_token_file,
            self.github_git_credentials_file,
        ]:
            if not path.exists():
                continue
            try:
                path.unlink()
            except OSError as exc:
                raise HTTPException(status_code=500, detail="Failed to remove stored GitHub credentials.") from exc
        with self._github_token_lock:
            self._github_token_cache = {}
        status = self.github_auth_status()
        self._emit_auth_changed(reason="github_disconnected")
        LOGGER.debug("GitHub credentials disconnected.")
        return status

    def disconnect_openai_account(self) -> dict[str, Any]:
        self.cancel_openai_account_login()
        if self.openai_codex_auth_file.exists():
            try:
                self.openai_codex_auth_file.unlink()
            except OSError as exc:
                raise HTTPException(status_code=500, detail="Failed to remove stored OpenAI account credentials.") from exc
        status = self.openai_auth_status()
        self._emit_auth_changed(reason="openai_account_disconnected")
        self._emit_openai_account_session_changed(reason="openai_account_disconnected")
        LOGGER.debug("OpenAI account disconnected.")
        return status

    def _openai_login_session_payload(self, session: OpenAIAccountLoginSession | None) -> dict[str, Any] | None:
        if session is None:
            return None
        running = _is_process_running(session.process.pid) and session.exit_code is None
        return {
            "id": session.id,
            "method": session.method,
            "status": session.status,
            "started_at": session.started_at,
            "completed_at": session.completed_at,
            "exit_code": session.exit_code,
            "error": session.error,
            "running": running,
            "login_url": session.login_url,
            "device_code": session.device_code,
            "local_callback_url": session.local_callback_url,
            "callback_port": session.callback_port,
            "callback_path": session.callback_path,
            "log_tail": session.log_tail,
        }

    def openai_account_session_payload(self) -> dict[str, Any]:
        with self._openai_login_lock:
            session_payload = self._openai_login_session_payload(self._openai_login_session)
        account_payload = self._openai_account_payload()
        return {
            "session": session_payload,
            "account_connected": account_payload["account_connected"],
            "account_auth_mode": account_payload["account_auth_mode"],
            "account_updated_at": account_payload["account_updated_at"],
        }

    def _openai_login_container_cmd(self, container_name: str, method: str) -> list[str]:
        container_home = DEFAULT_CONTAINER_HOME
        cmd = [
            "docker",
            "run",
            "--rm",
            "--name",
            container_name,
            "--init",
            "--user",
            f"{self.local_uid}:{self.local_gid}",
            "--network",
            "host",
            "--workdir",
            container_home,
            "--volume",
            f"{self.host_codex_dir}:{container_home}/.codex",
            "--volume",
            f"{self.config_file}:{container_home}/.codex/config.toml:ro",
            "--env",
            f"LOCAL_UMASK={self.local_umask}",
            "--env",
            f"HOME={container_home}",
            "--env",
            f"CONTAINER_HOME={container_home}",
            "--env",
            f"PATH={container_home}/.codex/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
        ]
        for supp_gid in _parse_gid_csv(self.local_supp_gids):
            if supp_gid == self.local_gid:
                continue
            cmd.extend(["--group-add", str(supp_gid)])
        cmd.extend(
            [
                DEFAULT_AGENT_IMAGE,
                "codex",
                "login",
            ]
        )
        if method == "device_auth":
            cmd.append("--device-auth")
        return cmd

    def _start_openai_login_reader(self, session_id: str) -> None:
        thread = Thread(target=self._openai_login_reader_loop, args=(session_id,), daemon=True)
        thread.start()

    def _openai_login_reader_loop(self, session_id: str) -> None:
        with self._openai_login_lock:
            session = self._openai_login_session
            if session is None or session.id != session_id:
                return
            process = session.process

        stdout = process.stdout
        if stdout is not None:
            for raw_line in iter(stdout.readline, ""):
                if raw_line == "":
                    break
                clean_line = ANSI_ESCAPE_RE.sub("", raw_line).replace("\r", "")
                should_emit_session = False
                with self._openai_login_lock:
                    current = self._openai_login_session
                    if current is None or current.id != session_id:
                        break
                    current.log_tail = _append_tail(
                        current.log_tail,
                        clean_line,
                        OPENAI_ACCOUNT_LOGIN_LOG_MAX_CHARS,
                    )

                    callback_candidate = _first_url_in_text(clean_line, "http://localhost")
                    if callback_candidate:
                        local_url, callback_port, callback_path = _parse_local_callback(callback_candidate)
                        if local_url:
                            current.local_callback_url = local_url
                            current.callback_port = callback_port
                            current.callback_path = callback_path

                    login_url = _first_url_in_text(clean_line, "https://auth.openai.com/")
                    if login_url:
                        current.login_url = login_url
                        if current.method == "browser_callback" and current.status in {"starting", "running"}:
                            current.status = "waiting_for_browser"
                        parsed_login = urllib.parse.urlparse(login_url)
                        query = urllib.parse.parse_qs(parsed_login.query)
                        redirect_values = query.get("redirect_uri") or []
                        if redirect_values:
                            local_url, callback_port, callback_path = _parse_local_callback(redirect_values[0])
                            if local_url:
                                current.local_callback_url = local_url
                                current.callback_port = callback_port
                                current.callback_path = callback_path

                    device_code_match = re.search(r"\b[A-Z0-9]{4}-[A-Z0-9]{5}\b", clean_line)
                    if device_code_match:
                        current.device_code = device_code_match.group(0)
                        if current.method == "device_auth" and current.status in {"starting", "running", "waiting_for_browser"}:
                            current.status = "waiting_for_device_code"
                    should_emit_session = True
                if should_emit_session:
                    self._emit_openai_account_session_changed(reason="login_output")

        exit_code = process.wait()
        should_emit_auth = False
        with self._openai_login_lock:
            current = self._openai_login_session
            if current is None or current.id != session_id:
                return
            current.exit_code = exit_code
            if not current.completed_at:
                current.completed_at = _iso_now()
            if current.status == "cancelled":
                return

            account_connected, _ = _read_codex_auth(self.openai_codex_auth_file)
            if exit_code == 0 and account_connected:
                current.status = "connected"
                current.error = ""
                should_emit_auth = True
            else:
                current.status = "failed"
                if not current.error:
                    if exit_code == 0:
                        current.error = "Login exited without saving ChatGPT account credentials."
                    else:
                        current.error = f"Login process exited with code {exit_code}."
        self._emit_openai_account_session_changed(reason="login_process_exit")
        if should_emit_auth:
            self._emit_auth_changed(reason="openai_account_connected")

    def _stop_openai_login_process(self, session: OpenAIAccountLoginSession) -> None:
        if _is_process_running(session.process.pid):
            _stop_process(session.process.pid)
        try:
            subprocess.run(
                ["docker", "rm", "-f", session.container_name],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError:
            return

    def start_openai_account_login(self, method: str = "browser_callback") -> dict[str, Any]:
        normalized_method = _normalize_openai_account_login_method(method)
        LOGGER.debug("Starting OpenAI account login flow method=%s.", normalized_method)
        if shutil.which("docker") is None:
            raise HTTPException(status_code=400, detail="docker command not found in PATH.")
        if not _docker_image_exists(DEFAULT_AGENT_IMAGE):
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Runtime image '{DEFAULT_AGENT_IMAGE}' is not available. "
                    "Start a chat once to build it, then retry account login."
                ),
            )

        with self._openai_login_lock:
            existing = self._openai_login_session
            existing_running = bool(existing and _is_process_running(existing.process.pid))
            should_cancel_existing = bool(existing_running and existing and existing.method != normalized_method)
        if should_cancel_existing:
            self.cancel_openai_account_login()

        existing_payload: dict[str, Any] | None = None
        with self._openai_login_lock:
            existing = self._openai_login_session
            if existing is not None and _is_process_running(existing.process.pid):
                existing_payload = self._openai_login_session_payload(existing)
            else:
                container_name = f"agent-hub-openai-login-{uuid.uuid4().hex[:12]}"
                cmd = self._openai_login_container_cmd(container_name, normalized_method)
                try:
                    process = subprocess.Popen(
                        cmd,
                        text=True,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        bufsize=1,
                        start_new_session=True,
                    )
                except OSError as exc:
                    raise HTTPException(status_code=500, detail=f"Failed to start account login container: {exc}") from exc

                session = OpenAIAccountLoginSession(
                    id=uuid.uuid4().hex,
                    process=process,
                    container_name=container_name,
                    started_at=_iso_now(),
                    method=normalized_method,
                    status="running",
                )
                self._openai_login_session = session

        if existing_payload is not None:
            self._emit_openai_account_session_changed(reason="login_already_running")
            return {"session": existing_payload}

        self._start_openai_login_reader(session.id)
        self._emit_openai_account_session_changed(reason="login_started")
        return {"session": self._openai_login_session_payload(session)}

    def cancel_openai_account_login(self) -> dict[str, Any]:
        not_running_payload: dict[str, Any] | None = None
        with self._openai_login_lock:
            session = self._openai_login_session
            if session is None:
                return {"session": None}
            if not _is_process_running(session.process.pid):
                not_running_payload = self._openai_login_session_payload(session)
            else:
                session.status = "cancelled"
                session.error = "Cancelled by user."
                session.completed_at = _iso_now()
        if not_running_payload is not None:
            self._emit_openai_account_session_changed(reason="login_not_running")
            return {"session": not_running_payload}

        self._stop_openai_login_process(session)

        cancelled_payload: dict[str, Any] | None = None
        with self._openai_login_lock:
            current = self._openai_login_session
            if current is not None and current.id == session.id:
                current.exit_code = current.process.poll()
                cancelled_payload = self._openai_login_session_payload(current)
        if cancelled_payload is not None:
            self._emit_openai_account_session_changed(reason="login_cancelled")
            return {"session": cancelled_payload}
        return {"session": None}

    def forward_openai_account_callback(self, query: str, path: str = "/auth/callback") -> dict[str, Any]:
        with self._openai_login_lock:
            session = self._openai_login_session
            if session is None:
                raise HTTPException(status_code=409, detail="No active OpenAI account login session.")
            if session.method != "browser_callback":
                raise HTTPException(status_code=409, detail="Callback forwarding is only available for browser callback login.")
            callback_port = int(session.callback_port or OPENAI_ACCOUNT_LOGIN_DEFAULT_CALLBACK_PORT)
            callback_path = str(path or session.callback_path or "/auth/callback").strip() or "/auth/callback"
            if not callback_path.startswith("/"):
                callback_path = f"/{callback_path}"
            target_origin = f"http://127.0.0.1:{callback_port}"

        if not query:
            raise HTTPException(status_code=400, detail="Missing callback query parameters.")

        target_url = urllib.parse.urlunparse(("http", f"127.0.0.1:{callback_port}", callback_path, "", query, ""))
        request = urllib.request.Request(target_url, method="GET")
        try:
            with urllib.request.urlopen(request, timeout=8.0) as response:
                status_code = int(response.getcode() or 0)
                response_body = response.read().decode("utf-8", errors="ignore")
        except urllib.error.HTTPError as exc:
            status_code = int(exc.code or 0)
            response_body = exc.read().decode("utf-8", errors="ignore")
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise HTTPException(status_code=502, detail="Failed to forward OAuth callback to login container.") from exc

        with self._openai_login_lock:
            current = self._openai_login_session
            if current is not None and current.id == session.id:
                current.log_tail = _append_tail(
                    current.log_tail,
                    "\n[hub] OAuth callback forwarded to local login server.\n",
                    OPENAI_ACCOUNT_LOGIN_LOG_MAX_CHARS,
                )
                if current.status in {"running", "waiting_for_browser"}:
                    current.status = "callback_received"
        self._emit_openai_account_session_changed(reason="oauth_callback_forwarded")

        return {
            "forwarded": True,
            "status_code": status_code,
            "target_origin": target_origin,
            "target_path": callback_path,
            "response_summary": _short_summary(ANSI_ESCAPE_RE.sub("", response_body), max_words=28, max_chars=220),
        }

    def chat_workdir(self, chat_id: str) -> Path:
        chat = self.chat(chat_id)
        if chat is not None and chat.get("workspace"):
            return Path(str(chat["workspace"]))
        return self.chat_dir / chat_id

    def project_workdir(self, project_id: str) -> Path:
        return self.project_dir / project_id

    def chat_log(self, chat_id: str) -> Path:
        return self.log_dir / f"{chat_id}.log"

    def project_build_log(self, project_id: str) -> Path:
        return self.log_dir / f"project-{project_id}.log"

    def _chat_artifact_publish_url(self, chat_id: str) -> str:
        return f"{self.artifact_publish_base_url}/api/chats/{chat_id}/artifacts/publish"

    @staticmethod
    def _chat_artifact_download_url(chat_id: str, artifact_id: str) -> str:
        return f"/api/chats/{chat_id}/artifacts/{artifact_id}/download"

    @staticmethod
    def _chat_artifact_preview_url(chat_id: str, artifact_id: str) -> str:
        return f"/api/chats/{chat_id}/artifacts/{artifact_id}/preview"

    def _chat_artifact_public_payload(self, chat_id: str, artifact: dict[str, Any]) -> dict[str, Any]:
        artifact_id = str(artifact.get("id") or "")
        return {
            "id": artifact_id,
            "name": _normalize_artifact_name(artifact.get("name"), fallback=Path(str(artifact.get("relative_path") or "")).name),
            "relative_path": str(artifact.get("relative_path") or ""),
            "size_bytes": int(artifact.get("size_bytes") or 0),
            "created_at": str(artifact.get("created_at") or ""),
            "preview_url": self._chat_artifact_preview_url(chat_id, artifact_id),
            "download_url": self._chat_artifact_download_url(chat_id, artifact_id),
        }

    def _chat_artifact_history_public_payload(self, chat_id: str, history_entry: dict[str, Any]) -> dict[str, Any]:
        return {
            "prompt": _sanitize_submitted_prompt(history_entry.get("prompt"))[:CHAT_ARTIFACT_PROMPT_LABEL_MAX_CHARS],
            "archived_at": str(history_entry.get("archived_at") or ""),
            "artifacts": [
                self._chat_artifact_public_payload(chat_id, artifact)
                for artifact in _normalize_chat_artifacts(history_entry.get("artifacts"))
            ],
        }

    def _resolve_chat_artifact_file(self, chat_id: str, submitted_path: Any) -> tuple[Path, str]:
        raw_path = str(submitted_path or "").strip()
        if not raw_path:
            raise HTTPException(status_code=400, detail="path is required.")
        if len(raw_path) > CHAT_ARTIFACT_PATH_MAX_CHARS * 2:
            raise HTTPException(status_code=400, detail="path is too long.")

        workspace = self.chat_workdir(chat_id).resolve()
        candidate = Path(raw_path).expanduser()
        resolved = candidate.resolve() if candidate.is_absolute() else (workspace / candidate).resolve()
        try:
            relative = resolved.relative_to(workspace)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Artifact path must be inside the chat workspace.") from exc
        if not resolved.exists():
            raise HTTPException(status_code=404, detail=f"Artifact file not found: {raw_path}")
        if not resolved.is_file():
            raise HTTPException(status_code=400, detail=f"Artifact path is not a file: {raw_path}")
        relative_path = _coerce_artifact_relative_path(relative.as_posix())
        if not relative_path:
            raise HTTPException(status_code=400, detail="Artifact path is invalid.")
        return resolved, relative_path

    @staticmethod
    def _require_artifact_publish_token(chat: dict[str, Any], token: Any) -> None:
        expected_hash = str(chat.get("artifact_publish_token_hash") or "")
        if not expected_hash:
            raise HTTPException(status_code=409, detail="Artifact publishing is unavailable until the chat is started.")

        submitted_token = str(token or "").strip()
        if not submitted_token:
            raise HTTPException(status_code=401, detail="Missing artifact publish token.")
        submitted_hash = _hash_artifact_publish_token(submitted_token)
        if not submitted_hash or not hmac.compare_digest(submitted_hash, expected_hash):
            raise HTTPException(status_code=403, detail="Invalid artifact publish token.")

    def list_chat_artifacts(self, chat_id: str) -> list[dict[str, Any]]:
        chat = self.chat(chat_id)
        if chat is None:
            raise HTTPException(status_code=404, detail="Chat not found.")
        artifacts = _normalize_chat_artifacts(chat.get("artifacts"))
        return [self._chat_artifact_public_payload(chat_id, artifact) for artifact in reversed(artifacts)]

    def publish_chat_artifact(
        self,
        chat_id: str,
        token: Any,
        submitted_path: Any,
        name: Any = None,
    ) -> dict[str, Any]:
        state = self.load()
        chat = state["chats"].get(chat_id)
        if chat is None:
            raise HTTPException(status_code=404, detail="Chat not found.")
        self._require_artifact_publish_token(chat, token)

        file_path, relative_path = self._resolve_chat_artifact_file(chat_id, submitted_path)
        file_stat = file_path.stat()
        now = _iso_now()
        artifacts = _normalize_chat_artifacts(chat.get("artifacts"))
        normalized_name = _normalize_artifact_name(name, fallback=file_path.name)

        existing_index = -1
        for index, artifact in enumerate(artifacts):
            if str(artifact.get("relative_path") or "") == relative_path:
                existing_index = index
                break

        if existing_index >= 0:
            artifact_id = str(artifacts[existing_index].get("id") or "") or uuid.uuid4().hex
            artifacts[existing_index] = {
                "id": artifact_id,
                "name": normalized_name,
                "relative_path": relative_path,
                "size_bytes": int(file_stat.st_size),
                "created_at": now,
            }
            stored_artifact = artifacts[existing_index]
        else:
            stored_artifact = {
                "id": uuid.uuid4().hex,
                "name": normalized_name,
                "relative_path": relative_path,
                "size_bytes": int(file_stat.st_size),
                "created_at": now,
            }
            artifacts.append(stored_artifact)
            if len(artifacts) > CHAT_ARTIFACTS_MAX_ITEMS:
                artifacts = artifacts[-CHAT_ARTIFACTS_MAX_ITEMS:]

        current_ids = _normalize_chat_current_artifact_ids(chat.get("artifact_current_ids"), artifacts)
        stored_artifact_id = str(stored_artifact.get("id") or "")
        if stored_artifact_id and stored_artifact_id not in current_ids:
            current_ids.append(stored_artifact_id)
        if len(current_ids) > CHAT_ARTIFACTS_MAX_ITEMS:
            current_ids = current_ids[-CHAT_ARTIFACTS_MAX_ITEMS:]

        chat["artifacts"] = artifacts
        chat["artifact_current_ids"] = current_ids
        chat["artifact_prompt_history"] = _normalize_chat_artifact_prompt_history(chat.get("artifact_prompt_history"))
        chat["updated_at"] = now
        state["chats"][chat_id] = chat
        self.save(state, reason="chat_artifact_published")
        return self._chat_artifact_public_payload(chat_id, stored_artifact)

    def resolve_chat_artifact_download(self, chat_id: str, artifact_id: str) -> tuple[Path, str, str]:
        state = self.load()
        chat = state["chats"].get(chat_id)
        if chat is None:
            raise HTTPException(status_code=404, detail="Chat not found.")

        normalized_artifact_id = str(artifact_id or "").strip()
        if not normalized_artifact_id:
            raise HTTPException(status_code=400, detail="artifact_id is required.")

        artifacts = _normalize_chat_artifacts(chat.get("artifacts"))
        match = next((entry for entry in artifacts if str(entry.get("id") or "") == normalized_artifact_id), None)
        if match is None:
            raise HTTPException(status_code=404, detail="Artifact not found.")

        workspace = self.chat_workdir(chat_id).resolve()
        resolved = (workspace / str(match.get("relative_path") or "")).resolve()
        try:
            resolved.relative_to(workspace)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Artifact path is invalid.") from exc
        if not resolved.exists() or not resolved.is_file():
            raise HTTPException(status_code=404, detail="Artifact file is no longer available.")

        filename = _normalize_artifact_name(match.get("name"), fallback=resolved.name)
        media_type = (
            mimetypes.guess_type(filename)[0]
            or mimetypes.guess_type(resolved.name)[0]
            or "application/octet-stream"
        )
        return resolved, filename, media_type

    def resolve_chat_artifact_preview(self, chat_id: str, artifact_id: str) -> tuple[Path, str]:
        artifact_path, _filename, media_type = self.resolve_chat_artifact_download(chat_id, artifact_id)
        return artifact_path, media_type

    def project(self, project_id: str) -> dict[str, Any] | None:
        return self.load()["projects"].get(project_id)

    def chat(self, chat_id: str) -> dict[str, Any] | None:
        return self.load()["chats"].get(chat_id)

    def list_projects(self) -> list[dict[str, Any]]:
        return list(self.load()["projects"].values())

    def list_chats(self) -> list[dict[str, Any]]:
        return list(self.load()["chats"].values())

    def add_project(
        self,
        repo_url: str,
        name: str | None = None,
        default_branch: str | None = None,
        setup_script: str | None = None,
        base_image_mode: str | None = None,
        base_image_value: str | None = None,
        default_ro_mounts: list[str] | None = None,
        default_rw_mounts: list[str] | None = None,
        default_env_vars: list[str] | None = None,
        ) -> dict[str, Any]:
        if not repo_url:
            raise HTTPException(status_code=400, detail="repo_url is required.")

        state = self.load()
        project_id = uuid.uuid4().hex
        project_name = name or _extract_repo_name(repo_url)
        resolved_default_branch = str(default_branch or "").strip()
        if not resolved_default_branch:
            git_env = self._github_git_env_for_repo(repo_url)
            resolved_default_branch = _detect_default_branch(repo_url, env=git_env)
        project = {
            "id": project_id,
            "name": project_name,
            "repo_url": repo_url,
            "setup_script": setup_script or "",
            "base_image_mode": _normalize_base_image_mode(base_image_mode),
            "base_image_value": (base_image_value or "").strip(),
            "default_ro_mounts": default_ro_mounts or [],
            "default_rw_mounts": default_rw_mounts or [],
            "default_env_vars": default_env_vars or [],
            "default_branch": resolved_default_branch,
            "created_at": _iso_now(),
            "updated_at": _iso_now(),
            "setup_snapshot_image": "",
            "build_status": "pending",
            "build_error": "",
            "build_started_at": "",
            "build_finished_at": "",
        }
        state["projects"][project_id] = project
        self.save(state)
        self._schedule_project_build(project_id)
        return self.load()["projects"][project_id]

    def update_project(self, project_id: str, update: dict[str, Any]) -> dict[str, Any]:
        state = self.load()
        project = state["projects"].get(project_id)
        if project is None:
            raise HTTPException(status_code=404, detail="Project not found.")

        for field in [
            "setup_script",
            "default_branch",
            "name",
            "base_image_mode",
            "base_image_value",
            "default_ro_mounts",
            "default_rw_mounts",
            "default_env_vars",
        ]:
            if field in update:
                project[field] = update[field]

        snapshot_fields = {
            "setup_script",
            "default_branch",
            "base_image_mode",
            "base_image_value",
            "default_ro_mounts",
            "default_rw_mounts",
            "default_env_vars",
        }
        requires_rebuild = any(field in update for field in snapshot_fields)
        if requires_rebuild:
            project["setup_snapshot_image"] = ""
            project.pop("snapshot_updated_at", None)
            project["build_status"] = "pending"
            project["build_error"] = ""
            project["build_started_at"] = ""
            project["build_finished_at"] = ""

        project["updated_at"] = _iso_now()
        state["projects"][project_id] = project
        self.save(state)
        if requires_rebuild:
            self._schedule_project_build(project_id)
            return self.load()["projects"][project_id]
        return self.load()["projects"][project_id]

    def _schedule_project_build(self, project_id: str) -> None:
        with self._project_build_lock:
            thread = self._project_build_threads.get(project_id)
            if thread and thread.is_alive():
                return
            thread = Thread(target=self._project_build_worker, args=(project_id,), daemon=True)
            self._project_build_threads[project_id] = thread
            thread.start()

    def _project_build_worker(self, project_id: str) -> None:
        try:
            while True:
                state = self.load()
                project = state["projects"].get(project_id)
                if project is None:
                    return
                build_status = str(project.get("build_status") or "")
                if build_status not in {"pending", "building"}:
                    return
                self._build_project_snapshot(project_id)
                state = self.load()
                project = state["projects"].get(project_id)
                if project is None:
                    return
                expected = self._project_setup_snapshot_tag(project)
                snapshot = str(project.get("setup_snapshot_image") or "").strip()
                status = str(project.get("build_status") or "")
                if status == "ready" and snapshot == expected and _docker_image_exists(snapshot):
                    return
                if status == "pending":
                    continue
                if status == "ready" and snapshot != expected:
                    project["build_status"] = "pending"
                    project["updated_at"] = _iso_now()
                    state["projects"][project_id] = project
                    self.save(state)
                    continue
                return
        finally:
            with self._project_build_lock:
                existing = self._project_build_threads.get(project_id)
                if existing is not None and existing.ident == current_thread().ident:
                    self._project_build_threads.pop(project_id, None)

    def _build_project_snapshot(self, project_id: str) -> dict[str, Any]:
        state = self.load()
        project = state["projects"].get(project_id)
        if project is None:
            raise HTTPException(status_code=404, detail="Project not found.")

        started_at = _iso_now()
        project["build_status"] = "building"
        project["build_error"] = ""
        project["build_started_at"] = started_at
        project["build_finished_at"] = ""
        project["updated_at"] = started_at
        state["projects"][project_id] = project
        self.save(state, reason="project_build_started")

        project_copy = dict(project)
        log_path = self.project_build_log(project_id)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text("", encoding="utf-8")
        self._emit_project_build_log(project_id, "", replace=True)

        try:
            snapshot_tag = self._prepare_project_snapshot_for_project(project_copy, log_path=log_path)
        except Exception as exc:
            state = self.load()
            current = state["projects"].get(project_id)
            if current is None:
                raise
            detail = exc.detail if isinstance(exc, HTTPException) else str(exc)
            current["build_status"] = "failed"
            current["build_error"] = str(detail)
            current["build_finished_at"] = _iso_now()
            current["updated_at"] = _iso_now()
            state["projects"][project_id] = current
            self.save(state, reason="project_build_failed")
            LOGGER.warning("Project build failed for project=%s: %s", project_id, detail)
            return current

        state = self.load()
        current = state["projects"].get(project_id)
        if current is None:
            raise HTTPException(status_code=404, detail="Project not found.")
        current["setup_snapshot_image"] = snapshot_tag
        current["snapshot_updated_at"] = _iso_now()
        current["build_status"] = "ready"
        current["build_error"] = ""
        current["build_finished_at"] = _iso_now()
        current["updated_at"] = _iso_now()
        state["projects"][project_id] = current
        self.save(state, reason="project_build_ready")
        LOGGER.debug("Project build completed for project=%s snapshot=%s", project_id, snapshot_tag)
        return current

    def delete_project(self, project_id: str) -> None:
        state = self.load()
        if project_id not in state["projects"]:
            raise HTTPException(status_code=404, detail="Project not found.")

        project_chats = [chat for chat in self.list_chats() if chat["project_id"] == project_id]
        for chat in project_chats:
            self.delete_chat(chat["id"], state=state)

        project_workspace = self.project_workdir(project_id)
        if project_workspace.exists():
            self._delete_path(project_workspace)
        project_log = self.project_build_log(project_id)
        if project_log.exists():
            project_log.unlink()

        del state["projects"][project_id]
        self.save(state)

    def create_chat(
        self,
        project_id: str,
        profile: str | None,
        ro_mounts: list[str],
        rw_mounts: list[str],
        env_vars: list[str],
        agent_args: list[str] | None = None,
        agent_type: str | None = None,
    ) -> dict[str, Any]:
        state = self.load()
        project = state["projects"].get(project_id)
        if project is None:
            raise HTTPException(status_code=404, detail="Project not found.")

        chat_id = uuid.uuid4().hex
        now = _iso_now()
        sanitized_project_name = _sanitize_workspace_component(project.get("name") or project_id)
        workspace_path = self.chat_dir / f"{sanitized_project_name}_{chat_id}"
        container_workspace = _container_workspace_path_for_project(project.get("name") or project_id)
        chat = {
            "id": chat_id,
            "project_id": project_id,
            "name": CHAT_DEFAULT_NAME,
            "profile": profile or "",
            "ro_mounts": ro_mounts,
            "rw_mounts": rw_mounts,
            "env_vars": env_vars,
            "agent_args": agent_args or [],
            "agent_type": _normalize_chat_agent_type(agent_type),
            "status": "stopped",
            "pid": None,
            "workspace": str(workspace_path),
            "container_workspace": container_workspace,
            "title_user_prompts": [],
            "title_cached": "",
            "title_prompt_fingerprint": "",
            "title_source": "openai",
            "title_status": "idle",
            "title_error": "",
            "artifacts": [],
            "artifact_current_ids": [],
            "artifact_prompt_history": [],
            "artifact_publish_token_hash": "",
            "artifact_publish_token_issued_at": "",
            "created_at": now,
            "updated_at": now,
        }
        state["chats"][chat_id] = chat
        self.save(state)
        return chat

    def create_and_start_chat(
        self,
        project_id: str,
        agent_args: list[str] | None = None,
        agent_type: str | None = None,
    ) -> dict[str, Any]:
        state = self.load()
        project = state["projects"].get(project_id)
        if project is None:
            raise HTTPException(status_code=404, detail="Project not found.")
        build_status = str(project.get("build_status") or "")
        if build_status != "ready":
            raise HTTPException(status_code=409, detail="Project image is still being built. Save settings and wait.")
        normalized_agent_args = [str(arg) for arg in (agent_args or []) if str(arg).strip()]
        chat = self.create_chat(
            project_id,
            profile="",
            ro_mounts=list(project.get("default_ro_mounts") or []),
            rw_mounts=list(project.get("default_rw_mounts") or []),
            env_vars=list(project.get("default_env_vars") or []),
            agent_args=normalized_agent_args,
            agent_type=_normalize_chat_agent_type(agent_type),
        )
        return self.start_chat(chat["id"])

    def update_chat(self, chat_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        state = self.load()
        chat = state["chats"].get(chat_id)
        if chat is None:
            raise HTTPException(status_code=404, detail="Chat not found.")

        for field in ["name", "profile", "ro_mounts", "rw_mounts", "env_vars", "agent_args", "agent_type"]:
            if field not in patch:
                continue
            if field == "agent_type":
                chat[field] = _normalize_chat_agent_type(patch[field])
                continue
            chat[field] = patch[field]

        chat["updated_at"] = _iso_now()
        state["chats"][chat_id] = chat
        self.save(state)
        return chat

    def delete_chat(self, chat_id: str, state: dict[str, Any] | None = None) -> None:
        local_state = state or self.load()
        chat = local_state["chats"].get(chat_id)
        if chat is None:
            raise HTTPException(status_code=404, detail="Chat not found.")

        pid = chat.get("pid")
        if isinstance(pid, int):
            _stop_process(pid)
        self._close_runtime(chat_id)

        workspace = Path(str(chat.get("workspace") or self.chat_dir / chat_id))
        if workspace.exists():
            self._delete_path(workspace)

        with self._chat_input_lock:
            self._chat_input_buffers.pop(chat_id, None)
            self._chat_input_ansi_carry.pop(chat_id, None)
        with self._chat_title_job_lock:
            self._chat_title_jobs_inflight.discard(chat_id)
            self._chat_title_jobs_pending.discard(chat_id)

        local_state["chats"].pop(chat_id, None)
        if state is None:
            self.save(local_state)
        else:
            state["chats"] = local_state["chats"]

    def _delete_path(self, path: Path) -> None:
        if not path.exists():
            return
        try:
            shutil.rmtree(path)
            return
        except PermissionError:
            try:
                _docker_fix_path_ownership(path, self.local_uid, self.local_gid)
            except Exception as repair_exc:  # pragma: no cover - exercised in tests via patched helper
                raise HTTPException(
                    status_code=500,
                    detail=(
                        f"Failed to delete path {path}: permission denied and ownership repair failed: "
                        f"{repair_exc}"
                    ),
                ) from repair_exc
            try:
                shutil.rmtree(path)
                return
            except Exception as retry_exc:
                raise HTTPException(
                    status_code=500,
                    detail=f"Failed to delete path {path} after ownership repair: {retry_exc}",
                ) from retry_exc
        except OSError as exc:
            raise HTTPException(status_code=500, detail=f"Failed to delete path {path}: {exc}") from exc

    @staticmethod
    def _queue_put(listener: queue.Queue[str | None], value: str | None) -> None:
        try:
            listener.put_nowait(value)
            return
        except queue.Full:
            pass

        try:
            listener.get_nowait()
        except queue.Empty:
            return

        try:
            listener.put_nowait(value)
        except queue.Full:
            return

    def _pop_runtime(self, chat_id: str) -> ChatRuntime | None:
        with self._runtime_lock:
            return self._chat_runtimes.pop(chat_id, None)

    def _close_runtime(self, chat_id: str) -> None:
        runtime = self._pop_runtime(chat_id)
        if runtime is None:
            return
        listeners = list(runtime.listeners)
        runtime.listeners.clear()
        try:
            os.close(runtime.master_fd)
        except OSError:
            pass
        for listener in listeners:
            self._queue_put(listener, None)

    def _runtime_for_chat(self, chat_id: str) -> ChatRuntime | None:
        with self._runtime_lock:
            runtime = self._chat_runtimes.get(chat_id)
        if runtime is None:
            return None
        if _is_process_running(runtime.process.pid):
            return runtime
        self._close_runtime(chat_id)
        return None

    def _broadcast_runtime_output(self, chat_id: str, text: str) -> None:
        if not text:
            return
        with self._runtime_lock:
            runtime = self._chat_runtimes.get(chat_id)
            listeners = list(runtime.listeners) if runtime else []
        for listener in listeners:
            self._queue_put(listener, text)

    def _runtime_reader_loop(self, chat_id: str, master_fd: int, log_path: Path) -> None:
        decoder = codecs.getincrementaldecoder("utf-8")("replace")
        try:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            with log_path.open("ab") as log_file:
                while True:
                    try:
                        chunk = os.read(master_fd, 4096)
                    except OSError:
                        break
                    if not chunk:
                        break
                    log_file.write(chunk)
                    log_file.flush()
                    decoded = decoder.decode(chunk)
                    if decoded:
                        self._broadcast_runtime_output(chat_id, decoded)
                tail = decoder.decode(b"", final=True)
                if tail:
                    self._broadcast_runtime_output(chat_id, tail)
        finally:
            runtime = self._pop_runtime(chat_id)
            listeners = list(runtime.listeners) if runtime else []
            if runtime:
                runtime.listeners.clear()
            try:
                os.close(master_fd)
            except OSError:
                pass
            for listener in listeners:
                self._queue_put(listener, None)

    def _register_runtime(self, chat_id: str, process: subprocess.Popen, master_fd: int) -> None:
        previous = self._pop_runtime(chat_id)
        if previous is not None:
            try:
                os.close(previous.master_fd)
            except OSError:
                pass
            for listener in list(previous.listeners):
                self._queue_put(listener, None)

        with self._runtime_lock:
            self._chat_runtimes[chat_id] = ChatRuntime(process=process, master_fd=master_fd)

        reader_thread = Thread(
            target=self._runtime_reader_loop,
            args=(chat_id, master_fd, self.chat_log(chat_id)),
            daemon=True,
        )
        reader_thread.start()

    def _spawn_chat_process(self, chat_id: str, cmd: list[str]) -> subprocess.Popen:
        master_fd, slave_fd = os.openpty()
        try:
            self._set_terminal_size(slave_fd, DEFAULT_PTY_COLS, DEFAULT_PTY_ROWS)
            proc = subprocess.Popen(
                cmd,
                stdin=slave_fd,
                stdout=slave_fd,
                stderr=slave_fd,
                close_fds=True,
                start_new_session=True,
            )
        except Exception:
            try:
                os.close(master_fd)
            except OSError:
                pass
            try:
                os.close(slave_fd)
            except OSError:
                pass
            raise

        try:
            os.close(slave_fd)
        except OSError:
            pass

        self._register_runtime(chat_id, proc, master_fd)
        return proc

    @staticmethod
    def _set_terminal_size(fd: int, cols: int, rows: int) -> None:
        safe_cols = max(1, int(cols))
        safe_rows = max(1, int(rows))
        fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", safe_rows, safe_cols, 0, 0))

    def _chat_log_history(self, chat_id: str) -> str:
        log_path = self.chat_log(chat_id)
        if not log_path.exists():
            return ""
        return log_path.read_text(encoding="utf-8", errors="ignore")

    def attach_terminal(self, chat_id: str) -> tuple[queue.Queue[str | None], str]:
        runtime = self._runtime_for_chat(chat_id)
        if runtime is None:
            raise HTTPException(status_code=409, detail="Chat is not running.")
        listener: queue.Queue[str | None] = queue.Queue(maxsize=TERMINAL_QUEUE_MAX)
        with self._runtime_lock:
            active_runtime = self._chat_runtimes.get(chat_id)
            if active_runtime is None:
                raise HTTPException(status_code=409, detail="Chat is not running.")
            active_runtime.listeners.add(listener)
        return listener, self._chat_log_history(chat_id)

    def detach_terminal(self, chat_id: str, listener: queue.Queue[str | None]) -> None:
        with self._runtime_lock:
            runtime = self._chat_runtimes.get(chat_id)
            if runtime is None:
                return
            runtime.listeners.discard(listener)

    def _collect_submitted_prompts_from_input(self, chat_id: str, data: str) -> list[str]:
        # Some terminal modes emit Enter as escape sequences (for example "\x1bOM").
        # Normalize known submit controls before ANSI stripping so we keep submit intent.
        normalized = (
            str(data or "")
            .replace("\x1bOM", "\r")
            .replace("\x1b[13~", "\r")
        )
        if not normalized:
            return []

        submissions: list[str] = []
        with self._chat_input_lock:
            current = str(self._chat_input_buffers.get(chat_id) or "")
            ansi_carry = str(self._chat_input_ansi_carry.get(chat_id) or "")
            sanitized, next_carry = _strip_ansi_stream(ansi_carry, normalized)
            sanitized = sanitized.replace("\x1b", "")
            for char in sanitized:
                if char in {"\r", "\n"}:
                    submitted = _compact_whitespace(current).strip()
                    if submitted:
                        submissions.append(submitted)
                    current = ""
                    continue
                if char in {"\b", "\x7f"}:
                    current = current[:-1]
                    continue
                if char == "\x15":  # Ctrl+U clears the current line.
                    current = ""
                    continue
                if ord(char) < 32:
                    continue
                current += char
                if len(current) > 2000:
                    current = current[-2000:]
            self._chat_input_buffers[chat_id] = current
            self._chat_input_ansi_carry[chat_id] = next_carry
        return submissions

    def _record_submitted_prompt(self, chat_id: str, prompt: Any) -> bool:
        submitted = _sanitize_submitted_prompt(prompt)
        if not submitted:
            LOGGER.debug("Title prompt ignored for chat=%s: empty submission.", chat_id)
            return False
        if _looks_like_terminal_control_payload(submitted):
            LOGGER.debug("Title prompt ignored for chat=%s: terminal control payload.", chat_id)
            return False

        state = self.load()
        chat = state["chats"].get(chat_id)
        if chat is None:
            LOGGER.debug("Title prompt ignored for chat=%s: chat not found.", chat_id)
            return False

        history_raw = chat.get("title_user_prompts")
        history: list[str] = []
        if isinstance(history_raw, list):
            history = [str(item) for item in history_raw if str(item).strip()]

        artifacts = _normalize_chat_artifacts(chat.get("artifacts"))
        current_ids_raw = chat.get("artifact_current_ids")
        if isinstance(current_ids_raw, list):
            current_ids = _normalize_chat_current_artifact_ids(current_ids_raw, artifacts)
        else:
            current_ids = [str(artifact.get("id") or "") for artifact in artifacts if str(artifact.get("id") or "")]
        artifact_map = {str(artifact.get("id") or ""): artifact for artifact in artifacts}
        current_artifacts = [dict(artifact_map[artifact_id]) for artifact_id in current_ids if artifact_id in artifact_map]
        if current_artifacts:
            source_prompt = _sanitize_submitted_prompt(history[-1]) if history else ""
            if not source_prompt:
                source_prompt = "Earlier prompt"
            if len(source_prompt) > CHAT_ARTIFACT_PROMPT_LABEL_MAX_CHARS:
                source_prompt = source_prompt[:CHAT_ARTIFACT_PROMPT_LABEL_MAX_CHARS].rstrip()
            artifact_prompt_history = _normalize_chat_artifact_prompt_history(chat.get("artifact_prompt_history"))
            archive_time = _iso_now()
            artifact_prompt_history.append(
                {
                    "prompt": source_prompt,
                    "archived_at": archive_time,
                    "artifacts": current_artifacts,
                }
            )
            if len(artifact_prompt_history) > CHAT_ARTIFACT_PROMPT_HISTORY_MAX_ITEMS:
                artifact_prompt_history = artifact_prompt_history[-CHAT_ARTIFACT_PROMPT_HISTORY_MAX_ITEMS:]
            chat["artifact_prompt_history"] = artifact_prompt_history
        else:
            chat["artifact_prompt_history"] = _normalize_chat_artifact_prompt_history(chat.get("artifact_prompt_history"))
        chat["artifact_current_ids"] = []

        if history and _compact_whitespace(str(history[-1])).strip() == submitted:
            chat["updated_at"] = _iso_now()
            state["chats"][chat_id] = chat
            self.save(state, reason="title_prompt_recorded")
            LOGGER.debug("Title prompt duplicate for chat=%s; preserved title state and archived current artifacts.", chat_id)
            return False

        history.append(submitted)

        now = _iso_now()
        chat["title_user_prompts"] = history
        chat["title_user_prompts_updated_at"] = now
        chat["title_status"] = "pending"
        chat["title_error"] = ""
        chat["updated_at"] = now
        state["chats"][chat_id] = chat
        self.save(state, reason="title_prompt_recorded")
        LOGGER.debug("Title prompt recorded for chat=%s prompts=%d", chat_id, len(history))
        self._schedule_chat_title_generation(chat_id)
        return True

    def submit_chat_input_buffer(self, chat_id: str) -> None:
        with self._chat_input_lock:
            buffered = _compact_whitespace(str(self._chat_input_buffers.get(chat_id) or "")).strip()
            self._chat_input_buffers[chat_id] = ""
            self._chat_input_ansi_carry[chat_id] = ""
        if not buffered:
            LOGGER.debug("Buffered terminal input submit ignored for chat=%s: buffer empty.", chat_id)
            return
        LOGGER.debug("Submitting buffered terminal input for chat=%s.", chat_id)
        self._record_submitted_prompt(chat_id, buffered)

    def record_chat_title_prompt(self, chat_id: str, prompt: Any) -> dict[str, Any]:
        state = self.load()
        if chat_id not in state["chats"]:
            raise HTTPException(status_code=404, detail="Chat not found.")
        LOGGER.debug("Direct title prompt submission for chat=%s.", chat_id)
        recorded = self._record_submitted_prompt(chat_id, prompt)
        return {"chat_id": chat_id, "recorded": recorded}

    def _schedule_chat_title_generation(self, chat_id: str) -> None:
        with self._chat_title_job_lock:
            if chat_id in self._chat_title_jobs_inflight:
                self._chat_title_jobs_pending.add(chat_id)
                LOGGER.debug("Title generation already inflight for chat=%s, queued follow-up run.", chat_id)
                return
            self._chat_title_jobs_inflight.add(chat_id)
        LOGGER.debug("Scheduling title generation for chat=%s.", chat_id)

        thread = Thread(target=self._chat_title_generation_loop, args=(chat_id,), daemon=True)
        thread.start()

    def _chat_title_generation_loop(self, chat_id: str) -> None:
        LOGGER.debug("Title generation loop started for chat=%s.", chat_id)
        try:
            while True:
                self._generate_and_store_chat_title(chat_id)
                with self._chat_title_job_lock:
                    if chat_id in self._chat_title_jobs_pending:
                        self._chat_title_jobs_pending.discard(chat_id)
                        LOGGER.debug("Title generation loop continuing for chat=%s (pending rerun).", chat_id)
                        continue
                    self._chat_title_jobs_inflight.discard(chat_id)
                    break
        finally:
            with self._chat_title_job_lock:
                self._chat_title_jobs_inflight.discard(chat_id)
                self._chat_title_jobs_pending.discard(chat_id)
        LOGGER.debug("Title generation loop finished for chat=%s.", chat_id)

    def _generate_and_store_chat_title(self, chat_id: str) -> None:
        state = self.load()
        chat = state["chats"].get(chat_id)
        if chat is None:
            LOGGER.debug("Title generation skipped for chat=%s: chat missing.", chat_id)
            return

        history_raw = chat.get("title_user_prompts")
        if not isinstance(history_raw, list):
            LOGGER.debug("Title generation skipped for chat=%s: title history missing.", chat_id)
            return
        history = [str(item) for item in history_raw if str(item).strip()]
        prompts = _normalize_chat_prompt_history(history)
        if not prompts:
            LOGGER.debug("Title generation skipped for chat=%s: no normalized prompts.", chat_id)
            return

        prompt_fingerprint = _chat_title_prompt_fingerprint(prompts, max_chars=CHAT_TITLE_MAX_CHARS)
        cached_fingerprint = str(chat.get("title_prompt_fingerprint") or "")
        cached_title = _truncate_title(str(chat.get("title_cached") or ""), CHAT_TITLE_MAX_CHARS)
        if cached_title and prompt_fingerprint and cached_fingerprint == prompt_fingerprint:
            LOGGER.debug(
                "Title generation skipped for chat=%s: fingerprint unchanged (%s).",
                chat_id,
                prompt_fingerprint[:12],
            )
            return

        auth_mode, api_key = self._chat_title_generation_auth()
        LOGGER.debug(
            "Title generation started for chat=%s prompts=%d auth_mode=%s fingerprint=%s",
            chat_id,
            len(prompts),
            auth_mode,
            prompt_fingerprint[:12],
        )
        if auth_mode == CHAT_TITLE_AUTH_MODE_NONE:
            chat["title_status"] = "error"
            chat["title_error"] = CHAT_TITLE_NO_CREDENTIALS_ERROR
            chat["title_prompt_fingerprint"] = prompt_fingerprint
            chat["title_source"] = "openai"
            chat["title_updated_at"] = _iso_now()
            state["chats"][chat_id] = chat
            self.save(state, reason="title_generation_missing_credentials")
            LOGGER.debug("Title generation failed for chat=%s: no credentials.", chat_id)
            return

        try:
            resolved_title, _ = self._generate_chat_title_with_resolved_auth(
                auth_mode=auth_mode,
                api_key=api_key,
                user_prompts=prompts,
            )
        except Exception as exc:
            chat["title_status"] = "error"
            chat["title_error"] = str(exc)
            chat["title_prompt_fingerprint"] = prompt_fingerprint
            chat["title_source"] = "openai"
            chat["title_updated_at"] = _iso_now()
            state["chats"][chat_id] = chat
            self.save(state, reason="title_generation_error")
            LOGGER.warning("Title generation failed for chat=%s: %s", chat_id, exc)
            return

        chat["title_cached"] = resolved_title
        chat["title_prompt_fingerprint"] = prompt_fingerprint
        chat["title_source"] = "openai"
        chat["title_status"] = "ready"
        chat["title_error"] = ""
        chat["title_updated_at"] = _iso_now()
        state["chats"][chat_id] = chat
        self.save(state, reason="title_generation_ready")
        LOGGER.debug("Title generation succeeded for chat=%s.", chat_id)

    def write_terminal_input(self, chat_id: str, data: str) -> None:
        runtime = self._runtime_for_chat(chat_id)
        if runtime is None:
            raise HTTPException(status_code=409, detail="Chat is not running.")
        if not data:
            return
        try:
            os.write(runtime.master_fd, data.encode("utf-8", errors="ignore"))
        except OSError as exc:
            raise HTTPException(status_code=409, detail="Failed to write to chat terminal.") from exc
        submissions = self._collect_submitted_prompts_from_input(chat_id, data)
        for prompt in submissions:
            self._record_submitted_prompt(chat_id, prompt)

    def resize_terminal(self, chat_id: str, cols: int, rows: int) -> None:
        runtime = self._runtime_for_chat(chat_id)
        if runtime is None:
            raise HTTPException(status_code=409, detail="Chat is not running.")
        try:
            self._set_terminal_size(runtime.master_fd, cols, rows)
        except (OSError, TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail="Invalid terminal resize request.") from exc
        _signal_process_group_winch(int(runtime.process.pid))

    def clean_start(self) -> dict[str, int]:
        self.cancel_openai_account_login()
        state = self.load()

        with self._runtime_lock:
            runtime_ids = list(self._chat_runtimes.keys())
        for chat_id in runtime_ids:
            self._close_runtime(chat_id)

        stopped_chats = 0
        image_tags: set[str] = set()
        for chat in state["chats"].values():
            pid = chat.get("pid")
            if isinstance(pid, int) and _is_process_running(pid):
                _stop_process(pid)
                stopped_chats += 1
            snapshot_tag = str(chat.get("setup_snapshot_image") or "").strip()
            if snapshot_tag:
                image_tags.add(snapshot_tag)

        projects_reset = 0
        for project in state["projects"].values():
            snapshot_tag = str(project.get("setup_snapshot_image") or "").strip()
            if snapshot_tag:
                image_tags.add(snapshot_tag)
            if project.get("setup_snapshot_image"):
                projects_reset += 1
            project["setup_snapshot_image"] = ""
            project.pop("snapshot_updated_at", None)
            project["build_status"] = "pending"
            project["build_error"] = ""
            project["build_started_at"] = ""
            project["build_finished_at"] = ""
            project["updated_at"] = _iso_now()

        cleared_chats = len(state["chats"])
        state["chats"] = {}

        for path in [self.chat_dir, self.project_dir, self.log_dir]:
            if path.exists():
                self._delete_path(path)
            path.mkdir(parents=True, exist_ok=True)

        self.save(state)
        _docker_remove_images(("agent-hub-setup-", "agent-base-"), image_tags)

        return {
            "stopped_chats": stopped_chats,
            "cleared_chats": cleared_chats,
            "projects_reset": projects_reset,
            "docker_images_requested": len(image_tags),
        }

    def shutdown(self) -> dict[str, int]:
        self.cancel_openai_account_login()
        with self._runtime_lock:
            runtime_ids = list(self._chat_runtimes.keys())
        for chat_id in runtime_ids:
            self._close_runtime(chat_id)

        state = self.load()
        running_chat_ids: list[str] = []
        running_pids: list[int] = []
        for chat_id, chat in state["chats"].items():
            pid = chat.get("pid")
            if isinstance(pid, int) and _is_process_running(pid):
                running_chat_ids.append(chat_id)
                running_pids.append(pid)

        stopped = _stop_processes(running_pids, timeout_seconds=4.0)
        if running_chat_ids:
            for chat_id in running_chat_ids:
                state["chats"].pop(chat_id, None)
            self.save(state)
        return {"stopped_chats": stopped, "closed_chats": len(running_chat_ids)}

    def _ensure_chat_clone(self, chat: dict[str, Any], project: dict[str, Any]) -> Path:
        workspace = Path(str(chat.get("workspace") or self.chat_dir / chat["id"]))
        if workspace.exists():
            git_dir = workspace / ".git"
            if git_dir.is_dir():
                return workspace
            self._delete_path(workspace)

            workspace = Path(str(chat.get("workspace") or self.chat_dir / chat["id"]))

        workspace.mkdir(parents=True, exist_ok=True)
        git_env = self._github_git_env_for_repo(str(project.get("repo_url") or ""))
        _run(["git", "clone", project["repo_url"], str(workspace)], check=True, env=git_env)
        return workspace

    def _ensure_project_clone(self, project: dict[str, Any]) -> Path:
        workspace = self.project_workdir(project["id"])
        if workspace.exists():
            git_dir = workspace / ".git"
            if git_dir.is_dir():
                return workspace
            self._delete_path(workspace)
        workspace.parent.mkdir(parents=True, exist_ok=True)
        git_env = self._github_git_env_for_repo(str(project.get("repo_url") or ""))
        _run(["git", "clone", project["repo_url"], str(workspace)], check=True, env=git_env)
        return workspace

    def _sync_checkout_to_remote(self, workspace: Path, project: dict[str, Any]) -> None:
        git_env = self._github_git_env_for_repo(str(project.get("repo_url") or ""))
        _run_for_repo(["fetch", "--all", "--prune"], workspace, check=True, env=git_env)
        branch = project.get("default_branch") or "master"
        remote_default = _git_default_remote_branch(workspace)
        if remote_default:
            branch = remote_default

        if not _git_has_remote_branch(workspace, branch):
            branch = "main" if _git_has_remote_branch(workspace, "main") else "master"

        if not _git_has_remote_branch(workspace, branch):
            raise HTTPException(status_code=400, detail="Unable to determine remote branch for sync.")

        _run_for_repo(["checkout", branch], workspace, check=True)
        _run_for_repo(["reset", "--hard", f"origin/{branch}"], workspace, check=True)
        _run_for_repo(["clean", "-fd"], workspace, check=True)

    def _resolve_project_base_value(self, workspace: Path, project: dict[str, Any]) -> tuple[str, str] | None:
        base_mode = _normalize_base_image_mode(project.get("base_image_mode"))
        base_value = str(project.get("base_image_value") or "").strip()
        if not base_value:
            return None

        if base_mode == "tag":
            return "base-image", base_value

        workspace_root = workspace.resolve()
        base_candidate = Path(base_value)
        if base_candidate.is_absolute():
            resolved_base = base_candidate.resolve()
        else:
            resolved_base = (workspace / base_candidate).resolve()
        try:
            resolved_base.relative_to(workspace_root)
        except ValueError as exc:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Base path must be inside the checked-out project. "
                    f"Got: {base_value}"
                ),
            ) from exc
        if not resolved_base.exists():
            raise HTTPException(
                status_code=400,
                detail=f"Base path does not exist in project workspace: {base_value}",
            )
        if not (resolved_base.is_file() or resolved_base.is_dir()):
            raise HTTPException(
                status_code=400,
                detail=f"Base path must be a file or directory: {base_value}",
            )
        return "base", str(resolved_base)

    def _append_project_base_args(self, cmd: list[str], workspace: Path, project: dict[str, Any]) -> None:
        resolved = self._resolve_project_base_value(workspace, project)
        if not resolved:
            return
        flag, value = resolved
        if flag == "base":
            base_path = Path(value)
            if base_path.is_file():
                # Dockerfiles stored under subdirectories commonly still need the repository
                # root as build context for COPY paths (for example COPY src ./src).
                cmd.extend(["--base-docker-context", str(workspace.resolve())])
                cmd.extend(["--base-dockerfile", str(base_path)])
                return
        cmd.extend([f"--{flag}", value])

    def _project_setup_snapshot_tag(self, project: dict[str, Any]) -> str:
        project_id = str(project.get("id") or "")[:12] or "project"
        payload = json.dumps(
            {
                "snapshot_schema_version": _snapshot_schema_version(),
                "project_id": project.get("id"),
                "setup_script": str(project.get("setup_script") or ""),
                "base_mode": _normalize_base_image_mode(project.get("base_image_mode")),
                "base_value": str(project.get("base_image_value") or ""),
                "default_ro_mounts": list(project.get("default_ro_mounts") or []),
                "default_rw_mounts": list(project.get("default_rw_mounts") or []),
                "default_env_vars": list(project.get("default_env_vars") or []),
            },
            sort_keys=True,
        )
        digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
        return f"agent-hub-setup-{project_id}-{digest}"

    def _ensure_project_setup_snapshot(
        self,
        workspace: Path,
        project: dict[str, Any],
        log_path: Path | None = None,
        project_id: str | None = None,
    ) -> str:
        setup_script = str(project.get("setup_script") or "").strip()
        snapshot_tag = self._project_setup_snapshot_tag(project)
        resolved_project_id = str(project_id or project.get("id") or "").strip()
        if _docker_image_exists(snapshot_tag):
            if log_path is not None:
                line = f"Using cached setup snapshot image '{snapshot_tag}'\n"
                with log_path.open("a", encoding="utf-8", errors="ignore") as log_file:
                    log_file.write(line)
                if resolved_project_id:
                    self._emit_project_build_log(resolved_project_id, line)
            return snapshot_tag

        cmd = [
            "uv",
            "run",
            "--project",
            str(_repo_root()),
            "agent_cli",
            "--project",
            str(workspace),
            "--container-project-name",
            _container_project_name(project.get("name") or project.get("id")),
            "--agent-home-path",
            str(self.host_agent_home),
            "--config-file",
            str(self.config_file),
            "--system-prompt-file",
            str(self.system_prompt_file),
            "--no-alt-screen",
        ]
        repo_url = str(project.get("repo_url") or "")
        cmd.extend(self._openai_credentials_arg())
        cmd.extend(self._github_git_args_for_repo(repo_url))
        for git_identity_env in self._github_git_identity_env_vars_for_repo(repo_url):
            cmd.extend(["--env-var", git_identity_env])
        self._append_project_base_args(cmd, workspace, project)
        for mount in project.get("default_ro_mounts") or []:
            cmd.extend(["--ro-mount", mount])
        for mount in project.get("default_rw_mounts") or []:
            cmd.extend(["--rw-mount", mount])
        for env_entry in project.get("default_env_vars") or []:
            if _is_reserved_env_entry(str(env_entry)):
                continue
            cmd.extend(["--env-var", env_entry])
        cmd.extend(
            [
                "--snapshot-image-tag",
                snapshot_tag,
                "--setup-script",
                setup_script,
                "--prepare-snapshot-only",
            ]
        )
        if log_path is None:
            _run(cmd, check=True)
        else:
            _run_logged(
                cmd,
                log_path=log_path,
                check=True,
                on_output=(
                    (lambda chunk: self._emit_project_build_log(resolved_project_id, chunk))
                    if resolved_project_id
                    else None
                ),
            )
        return snapshot_tag

    def _prepare_project_snapshot_for_project(
        self,
        project: dict[str, Any],
        log_path: Path | None = None,
    ) -> str:
        workspace = self._ensure_project_clone(project)
        self._sync_checkout_to_remote(workspace, project)
        return self._ensure_project_setup_snapshot(
            workspace,
            project,
            log_path=log_path,
            project_id=str(project.get("id") or ""),
        )

    def state_payload(self) -> dict[str, Any]:
        state = self.load()
        project_map: dict[str, dict[str, Any]] = {}
        for pid, project in state["projects"].items():
            project_copy = dict(project)
            project_copy["base_image_mode"] = _normalize_base_image_mode(project_copy.get("base_image_mode"))
            project_copy["base_image_value"] = str(project_copy.get("base_image_value") or "")
            project_copy["default_ro_mounts"] = list(project_copy.get("default_ro_mounts") or [])
            project_copy["default_rw_mounts"] = list(project_copy.get("default_rw_mounts") or [])
            project_copy["default_env_vars"] = list(project_copy.get("default_env_vars") or [])
            project_copy["setup_snapshot_image"] = str(project_copy.get("setup_snapshot_image") or "")
            project_copy["build_status"] = str(project_copy.get("build_status") or "pending")
            project_copy["build_error"] = str(project_copy.get("build_error") or "")
            project_copy["build_started_at"] = str(project_copy.get("build_started_at") or "")
            project_copy["build_finished_at"] = str(project_copy.get("build_finished_at") or "")
            project_map[pid] = project_copy
        chats = []
        dead_chat_ids: list[str] = []
        should_save = False
        for chat_id, chat in list(state["chats"].items()):
            chat_copy = dict(chat)
            pid = chat_copy.get("pid")
            chat_copy["ro_mounts"] = list(chat_copy.get("ro_mounts") or [])
            chat_copy["rw_mounts"] = list(chat_copy.get("rw_mounts") or [])
            chat_copy["env_vars"] = list(chat_copy.get("env_vars") or [])
            chat_copy["agent_type"] = _normalize_chat_agent_type(chat_copy.get("agent_type"))
            chat_copy["setup_snapshot_image"] = str(chat_copy.get("setup_snapshot_image") or "")
            cleaned_artifacts = _normalize_chat_artifacts(chat_copy.get("artifacts"))
            if chat_id in state["chats"] and cleaned_artifacts != _normalize_chat_artifacts(state["chats"][chat_id].get("artifacts")):
                state["chats"][chat_id]["artifacts"] = cleaned_artifacts
                should_save = True
            current_ids_raw = chat_copy.get("artifact_current_ids")
            if isinstance(current_ids_raw, list):
                cleaned_current_artifact_ids = _normalize_chat_current_artifact_ids(current_ids_raw, cleaned_artifacts)
            else:
                cleaned_current_artifact_ids = [
                    str(artifact.get("id") or "")
                    for artifact in cleaned_artifacts
                    if str(artifact.get("id") or "")
                ]
            if chat_id in state["chats"]:
                state_current_ids_raw = state["chats"][chat_id].get("artifact_current_ids")
                if isinstance(state_current_ids_raw, list):
                    state_current_artifact_ids = _normalize_chat_current_artifact_ids(state_current_ids_raw, cleaned_artifacts)
                else:
                    state_current_artifact_ids = [
                        str(artifact.get("id") or "")
                        for artifact in cleaned_artifacts
                        if str(artifact.get("id") or "")
                    ]
                if cleaned_current_artifact_ids != state_current_artifact_ids:
                    state["chats"][chat_id]["artifact_current_ids"] = cleaned_current_artifact_ids
                    should_save = True
            cleaned_artifact_prompt_history = _normalize_chat_artifact_prompt_history(chat_copy.get("artifact_prompt_history"))
            if chat_id in state["chats"] and cleaned_artifact_prompt_history != _normalize_chat_artifact_prompt_history(
                state["chats"][chat_id].get("artifact_prompt_history")
            ):
                state["chats"][chat_id]["artifact_prompt_history"] = cleaned_artifact_prompt_history
                should_save = True
            project_for_chat = project_map.get(chat_copy["project_id"], {})
            project_name = str(project_for_chat.get("name") or chat_copy["project_id"] or "project")
            chat_copy["artifacts"] = [self._chat_artifact_public_payload(chat_id, artifact) for artifact in reversed(cleaned_artifacts)]
            chat_copy["artifact_current_ids"] = cleaned_current_artifact_ids
            chat_copy["artifact_prompt_history"] = [
                self._chat_artifact_history_public_payload(chat_id, entry)
                for entry in reversed(cleaned_artifact_prompt_history)
            ]
            chat_copy.pop("artifact_publish_token_hash", None)
            chat_copy.pop("artifact_publish_token_issued_at", None)
            running = _is_process_running(pid)
            if running:
                chat_copy["status"] = "running"
            else:
                dead_chat_ids.append(chat_id)
                self._close_runtime(chat_id)
                was_running = str(chat_copy.get("status") or "") == "running" or isinstance(pid, int)
                if was_running:
                    continue
                dead_chat_ids.pop()
                chat_copy["status"] = "stopped"
                if chat_copy.get("pid") is not None:
                    chat_copy["pid"] = None
                    if chat_id in state["chats"]:
                        state["chats"][chat_id]["pid"] = None
                        state["chats"][chat_id]["status"] = "stopped"
                        should_save = True
            chat_copy["is_running"] = running
            chat_copy["container_workspace"] = str(chat_copy.get("container_workspace") or "") or _container_workspace_path_for_project(
                project_name
            )
            chat_copy["project_name"] = project_name
            subtitle = _chat_subtitle_from_log(self.chat_log(chat_id))
            cached_title = _truncate_title(str(chat_copy.get("title_cached") or ""), CHAT_TITLE_MAX_CHARS)
            if cached_title and _looks_like_terminal_control_payload(cached_title):
                cached_title = ""
                if chat_id in state["chats"]:
                    state["chats"][chat_id]["title_cached"] = ""
                    state["chats"][chat_id]["title_source"] = ""
                    state["chats"][chat_id]["title_prompt_fingerprint"] = ""
                    should_save = True
            history_raw = chat_copy.get("title_user_prompts")
            if isinstance(history_raw, list):
                cleaned_history = [
                    str(item)
                    for item in history_raw
                    if str(item).strip() and not _looks_like_terminal_control_payload(str(item))
                ]
                if chat_id in state["chats"] and cleaned_history != list(history_raw):
                    state["chats"][chat_id]["title_user_prompts"] = cleaned_history
                    should_save = True
            title_status = str(chat_copy.get("title_status") or "idle").lower()
            if title_status == "pending":
                pending_history = chat_copy.get("title_user_prompts")
                if isinstance(pending_history, list):
                    normalized_prompts = _normalize_chat_prompt_history([str(item) for item in pending_history if str(item).strip()])
                    if normalized_prompts:
                        self._schedule_chat_title_generation(chat_id)
            chat_copy["display_name"] = cached_title or _chat_display_name(chat_copy.get("name"))
            title_error = _compact_whitespace(str(chat_copy.get("title_error") or ""))
            if not subtitle and title_error:
                subtitle = _short_summary(f"Title generation error: {title_error}", max_words=20, max_chars=CHAT_SUBTITLE_MAX_CHARS)
            chat_copy["display_subtitle"] = subtitle
            chats.append(chat_copy)

        if dead_chat_ids:
            for chat_id in dead_chat_ids:
                self.delete_chat(chat_id, state=state)
            should_save = True
        if should_save:
            self.save(state)

        state["chats"] = chats
        state["projects"] = list(project_map.values())
        return state

    def start_chat(self, chat_id: str) -> dict[str, Any]:
        state = self.load()
        chat = state["chats"].get(chat_id)
        if chat is None:
            raise HTTPException(status_code=404, detail="Chat not found.")
        project = state["projects"].get(chat["project_id"])
        if project is None:
            raise HTTPException(status_code=404, detail="Parent project missing.")

        if chat.get("status") == "running" and _is_process_running(chat.get("pid")):
            raise HTTPException(status_code=409, detail="Chat is already running.")

        build_status = str(project.get("build_status") or "")
        snapshot_tag = str(project.get("setup_snapshot_image") or "").strip()
        expected_snapshot_tag = self._project_setup_snapshot_tag(project)
        snapshot_ready = (
            build_status == "ready"
            and snapshot_tag
            and snapshot_tag == expected_snapshot_tag
            and _docker_image_exists(snapshot_tag)
        )
        if not snapshot_ready:
            raise HTTPException(status_code=409, detail="Project image is not ready yet. Wait for setup build to finish.")

        workspace = self._ensure_chat_clone(chat, project)
        self._sync_checkout_to_remote(workspace, project)
        with self._chat_input_lock:
            self._chat_input_buffers[chat_id] = ""
            self._chat_input_ansi_carry[chat_id] = ""
        artifact_publish_token = _new_artifact_publish_token()
        agent_type = _normalize_chat_agent_type(chat.get("agent_type"))
        agent_command = AGENT_COMMAND_BY_TYPE.get(agent_type, AGENT_COMMAND_BY_TYPE[DEFAULT_CHAT_AGENT_TYPE])
        chat["agent_type"] = agent_type
        container_workspace = _container_workspace_path_for_project(project.get("name") or project.get("id"))

        cmd = [
            "uv",
            "run",
            "--project",
            str(_repo_root()),
            "agent_cli",
            "--agent-command",
            agent_command,
            "--project",
            str(workspace),
            "--container-project-name",
            _container_project_name(project.get("name") or project.get("id")),
            "--agent-home-path",
            str(self.host_agent_home),
            "--config-file",
            str(self.config_file),
            "--system-prompt-file",
            str(self.system_prompt_file),
            "--no-alt-screen",
        ]
        repo_url = str(project.get("repo_url") or "")
        cmd.extend(self._openai_credentials_arg())
        cmd.extend(self._github_git_args_for_repo(repo_url))
        for git_identity_env in self._github_git_identity_env_vars_for_repo(repo_url):
            cmd.extend(["--env-var", git_identity_env])
        self._append_project_base_args(cmd, workspace, project)
        cmd.extend(["--snapshot-image-tag", snapshot_tag])
        for mount in chat.get("ro_mounts") or []:
            cmd.extend(["--ro-mount", mount])
        for mount in chat.get("rw_mounts") or []:
            cmd.extend(["--rw-mount", mount])
        cmd.extend(["--env-var", f"AGENT_HUB_ARTIFACTS_URL={self._chat_artifact_publish_url(chat_id)}"])
        cmd.extend(["--env-var", f"AGENT_HUB_ARTIFACT_TOKEN={artifact_publish_token}"])
        for env_entry in chat.get("env_vars") or []:
            if _is_reserved_env_entry(str(env_entry)):
                continue
            cmd.extend(["--env-var", env_entry])
        agent_args = [str(arg) for arg in (chat.get("agent_args") or []) if str(arg).strip()]
        if agent_args:
            cmd.append("--")
            cmd.extend(agent_args)

        proc = self._spawn_chat_process(chat_id, cmd)
        chat["status"] = "running"
        chat["pid"] = proc.pid
        chat["setup_snapshot_image"] = snapshot_tag or ""
        chat["container_workspace"] = container_workspace
        chat["artifact_publish_token_hash"] = _hash_artifact_publish_token(artifact_publish_token)
        chat["artifact_publish_token_issued_at"] = _iso_now()
        chat["last_started_at"] = _iso_now()
        chat["updated_at"] = _iso_now()
        state["chats"][chat_id] = chat
        self.save(state)
        return chat

    def close_chat(self, chat_id: str) -> dict[str, Any]:
        state = self.load()
        chat = state["chats"].get(chat_id)
        if chat is None:
            raise HTTPException(status_code=404, detail="Chat not found.")

        pid = chat.get("pid")
        if isinstance(pid, int):
            _stop_process(pid)
        self._close_runtime(chat_id)
        with self._chat_input_lock:
            self._chat_input_buffers.pop(chat_id, None)
            self._chat_input_ansi_carry.pop(chat_id, None)

        chat["status"] = "stopped"
        chat["pid"] = None
        chat["artifact_publish_token_hash"] = ""
        chat["artifact_publish_token_issued_at"] = ""
        chat["updated_at"] = _iso_now()
        state["chats"][chat_id] = chat
        self.save(state)
        return chat


def _html_page() -> str:
    return """
<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <meta name="color-scheme" content="light dark" />
  <title>Agent Hub</title>
  <style>
    :root {
      --bg: #f4f6f8;
      --panel: #ffffff;
      --line: #d7dce3;
      --line-strong: #c7ced9;
      --text: #0f1722;
      --muted: #627082;
      --accent: #10a37f;
      --accent-strong: #0f8a6d;
      --header: #0b1017;
      --header-subtitle: #c8d0dc;
      --pill-running: #0f9b65;
      --pill-stopped: #6b7280;
      --shadow: 0 10px 24px rgba(15, 23, 42, 0.08);
    }
    @media (prefers-color-scheme: dark) {
      :root {
        --bg: #0a1018;
        --panel: #111923;
        --line: #2a3848;
        --line-strong: #32465d;
        --text: #e6edf7;
        --muted: #9aa8bb;
        --accent: #19b88e;
        --accent-strong: #16a480;
        --header: #060b11;
        --header-subtitle: #9fb1c6;
        --pill-running: #12b375;
        --pill-stopped: #738197;
        --shadow: 0 10px 24px rgba(0, 0, 0, 0.3);
      }
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      color: var(--text);
      background: var(--bg);
      font-family: "Sohne", "Soehne", "Avenir Next", "Inter", "Segoe UI", sans-serif;
      line-height: 1.45;
    }
    header {
      padding: 1.1rem 1.5rem;
      color: #fff;
      background: var(--header);
      border-bottom: 1px solid rgba(255, 255, 255, 0.08);
    }
    h1 { margin: 0; font-size: 1.35rem; letter-spacing: -0.02em; font-weight: 650; }
    .subhead { margin-top: 0.2rem; color: var(--header-subtitle); font-size: 0.92rem; }
    main {
      max-width: 1240px;
      margin: 0 auto;
      padding: 1rem;
      display: grid;
      gap: 1rem;
      grid-template-columns: minmax(420px, 1fr) minmax(420px, 1fr);
      align-items: start;
    }
    section {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 12px;
      padding: 1rem;
      box-shadow: var(--shadow);
    }
    section h2 { margin-top: 0; }
    .grid { display: grid; gap: 0.6rem; }
    input, textarea, button, select {
      width: 100%;
      padding: 0.58rem 0.62rem;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--panel);
      color: var(--text);
      font: inherit;
    }
    input:focus, textarea:focus, select:focus {
      outline: none;
      border-color: var(--accent);
      box-shadow: 0 0 0 3px rgba(16, 163, 127, 0.18);
    }
    textarea {
      min-height: 84px;
      resize: vertical;
      font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, monospace;
      line-height: 1.35;
    }
    .script-input { min-height: 132px; }
    .row { display: grid; grid-template-columns: 2fr 1fr; gap: 0.6rem; }
    .row.base-row { grid-template-columns: 1fr 2fr; }
    .chat {
      border: 1px solid var(--line);
      border-radius: 10px;
      padding: 0.8rem;
      margin-bottom: 0.85rem;
      background: var(--panel);
    }
    .chat h3 { margin: 0 0 0.4rem 0; }
    .meta { font-size: 0.85rem; color: var(--muted); }
    .pill { padding: 0.12rem 0.5rem; border-radius: 999px; font-size: 0.75rem; color: #fff; background: #607d8b; font-weight: 600; }
    .running { background: var(--pill-running); }
    .stopped { background: var(--pill-stopped); }
    .controls { display: flex; gap: 0.5rem; margin-top: 0.5rem; flex-wrap: wrap; }
    button {
      cursor: pointer;
      background: var(--accent);
      border-color: var(--accent);
      color: #fff;
      font-weight: 600;
      transition: background 120ms ease, border-color 120ms ease;
    }
    button:hover { background: var(--accent-strong); border-color: var(--accent-strong); }
    .controls button { width: auto; }
    .inline-controls { display: flex; gap: 0.45rem; align-items: center; flex-wrap: wrap; }
    .inline-controls button { width: auto; }
    .widget-list { display: grid; gap: 0.5rem; }
    .widget-row {
      display: grid;
      gap: 0.5rem;
      align-items: center;
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 0.5rem;
      background: color-mix(in srgb, var(--panel) 94%, transparent);
    }
    .widget-row.volume { grid-template-columns: minmax(180px, 1fr) minmax(180px, 1fr) 130px auto; }
    .widget-row.env { grid-template-columns: minmax(140px, 0.8fr) minmax(220px, 1fr) auto; }
    .widget-row button { width: auto; }
    .small { padding: 0.42rem 0.56rem; font-size: 0.85rem; }
    .section-label { font-size: 0.8rem; color: var(--muted); margin-top: 0.2rem; }
    .error-banner {
      display: none;
      margin: 0 1rem;
      padding: 0.6rem 0.75rem;
      border-radius: 8px;
      border: 1px solid #f3b2ad;
      color: #7a1610;
      background: #fff0ef;
      font-size: 0.9rem;
    }
    button.secondary {
      background: transparent;
      color: var(--text);
      border-color: var(--line-strong);
    }
    button.secondary:hover {
      background: rgba(127, 127, 127, 0.08);
      border-color: var(--line-strong);
    }
    button.danger {
      background: #b42318;
      border-color: #b42318;
      color: #fff;
    }
    button.danger:hover {
      background: #9f1f15;
      border-color: #9f1f15;
    }
    .muted { color: var(--muted); }
    @media (max-width: 980px) {
      main { grid-template-columns: 1fr; }
      .row { grid-template-columns: 1fr; }
      .widget-row.volume { grid-template-columns: 1fr; }
      .widget-row.env { grid-template-columns: 1fr; }
    }
  </style>
</head>
<body>
  <header>
    <h1>Agent Hub</h1>
    <div class="subhead">Project-level workspaces, one cloned directory per chat</div>
  </header>
  <div id="ui-error" class="error-banner"></div>
  <main>
    <section>
      <h2>Projects</h2>
      <form id="project-form" class="grid" onsubmit="createProject(event)">
        <input id="project-repo" required placeholder="git@github.com:org/repo.git or https://..." />
        <div class="row">
          <input id="project-name" placeholder="Optional project name" />
          <input id="project-branch" placeholder="Default branch (optional, auto-detect)" />
        </div>
        <div class="row base-row">
          <select id="project-base-image-mode" onchange="updateBasePlaceholderForCreate()">
            <option value="tag">Docker image tag</option>
            <option value="repo_path">Repo Dockerfile/path</option>
          </select>
          <input id="project-base-image-value" placeholder="Docker image tag (e.g. nvcr.io/nvidia/isaac-lab:2.3.2)" />
        </div>
        <textarea id="project-setup-script" class="script-input" placeholder="Setup script (one command per line, run in the checked-out project)&#10;example:&#10;uv sync&#10;uv run python -m pip install -e ."></textarea>
        <div class="section-label">Default volumes for new chats</div>
        <div id="project-default-volumes" class="widget-list"></div>
        <div class="inline-controls">
          <button type="button" class="secondary small" onclick="addVolumeRow('project-default-volumes')">Add volume</button>
        </div>
        <div class="section-label">Default environment variables for new chats</div>
        <div id="project-default-env" class="widget-list"></div>
        <div class="inline-controls">
          <button type="button" class="secondary small" onclick="addEnvRow('project-default-env')">Add environment variable</button>
        </div>
        <button type="submit">Add project</button>
      </form>
      <h2 style="margin-top:1rem;">Projects</h2>
      <div id="projects"></div>
    </section>
    <section>
      <h2>Chats</h2>
      <div id="chats"></div>
    </section>
  </main>
  <script>
    async function fetchJson(url, options={}) {
      const response = await fetch(url, Object.assign({ headers: { "Content-Type":"application/json" } }, options));
      if (!response.ok) {
        const text = await response.text();
        throw new Error(text || `Request failed with ${response.status}`);
      }
      if (response.status === 204) return null;
      return response.json();
    }

    async function fetchText(url) {
      const response = await fetch(url);
      if (!response.ok) {
        const text = await response.text();
        throw new Error(text || `Request failed with ${response.status}`);
      }
      return response.text();
    }

    function escapeHtml(value) {
      return String(value ?? '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
    }

    function normalizeBaseMode(mode) {
      return mode === 'repo_path' ? 'repo_path' : 'tag';
    }

    function baseModeLabel(mode) {
      return mode === 'repo_path' ? 'Repo path' : 'Docker tag';
    }

    function baseInputPlaceholder(mode) {
      if (mode === 'repo_path') {
        return 'Path in repo to Dockerfile or dir (e.g. docker/base or docker/base/Dockerfile)';
      }
      return 'Docker image tag (e.g. nvcr.io/nvidia/isaac-lab:2.3.2)';
    }

    function updateBasePlaceholderForCreate() {
      const mode = normalizeBaseMode(document.getElementById('project-base-image-mode').value);
      const input = document.getElementById('project-base-image-value');
      input.placeholder = baseInputPlaceholder(mode);
    }

    function updateBasePlaceholderForProject(projectId) {
      const mode = normalizeBaseMode(document.getElementById(`base-mode-${projectId}`).value);
      const input = document.getElementById(`base-value-${projectId}`);
      input.placeholder = baseInputPlaceholder(mode);
    }

    function addVolumeRow(listId, mount = null, markDirty = true) {
      const list = document.getElementById(listId);
      if (!list) return;
      if (markDirty) uiDirty = true;
      const mode = mount && mount.mode === 'ro' ? 'ro' : 'rw';
      const host = escapeHtml((mount && mount.host) || '');
      const container = escapeHtml((mount && mount.container) || '');
      const row = document.createElement('div');
      row.className = 'widget-row volume';
      row.innerHTML = `
        <input class="vol-host" placeholder="Local path (e.g. /data/datasets)" value="${host}" />
        <input class="vol-container" placeholder="Container path (e.g. /workspace/data)" value="${container}" />
        <select class="vol-mode">
          <option value="rw" ${mode === 'rw' ? 'selected' : ''}>Read-write</option>
          <option value="ro" ${mode === 'ro' ? 'selected' : ''}>Read-only</option>
        </select>
        <button type="button" class="secondary small" onclick="removeWidgetRow(this)">Remove</button>
      `;
      list.appendChild(row);
    }

    function addEnvRow(listId, envVar = null, markDirty = true) {
      const list = document.getElementById(listId);
      if (!list) return;
      if (markDirty) uiDirty = true;
      const key = escapeHtml((envVar && envVar.key) || '');
      const value = escapeHtml((envVar && envVar.value) || '');
      const row = document.createElement('div');
      row.className = 'widget-row env';
      row.innerHTML = `
        <input class="env-key" placeholder="KEY" value="${key}" />
        <input class="env-value" placeholder="VALUE" value="${value}" />
        <button type="button" class="secondary small" onclick="removeWidgetRow(this)">Remove</button>
      `;
      list.appendChild(row);
    }

    function removeWidgetRow(buttonEl) {
      const row = buttonEl.closest('.widget-row');
      if (row) {
        row.remove();
        uiDirty = true;
      }
    }

    function parseMountEntry(spec, mode) {
      if (typeof spec !== 'string') return null;
      const idx = spec.indexOf(':');
      if (idx <= 0 || idx === spec.length - 1) return null;
      return {
        host: spec.slice(0, idx),
        container: spec.slice(idx + 1),
        mode: mode === 'ro' ? 'ro' : 'rw',
      };
    }

    function seedVolumeRows(listId, roMounts = [], rwMounts = []) {
      const list = document.getElementById(listId);
      if (!list) return;
      list.innerHTML = '';
      const all = [];
      (roMounts || []).forEach((spec) => {
        const parsed = parseMountEntry(spec, 'ro');
        if (parsed) all.push(parsed);
      });
      (rwMounts || []).forEach((spec) => {
        const parsed = parseMountEntry(spec, 'rw');
        if (parsed) all.push(parsed);
      });
      all.forEach((entry) => addVolumeRow(listId, entry, false));
    }

    function splitEnvVar(entry) {
      if (typeof entry !== 'string') return { key: '', value: '' };
      const idx = entry.indexOf('=');
      if (idx < 0) return { key: entry, value: '' };
      return { key: entry.slice(0, idx), value: entry.slice(idx + 1) };
    }

    function seedEnvRows(listId, envVars = []) {
      const list = document.getElementById(listId);
      if (!list) return;
      list.innerHTML = '';
      (envVars || []).forEach((entry) => addEnvRow(listId, splitEnvVar(entry), false));
    }

    function collectMountPayload(listId) {
      const list = document.getElementById(listId);
      const ro = [];
      const rw = [];
      if (!list) return { ro_mounts: ro, rw_mounts: rw };

      list.querySelectorAll('.widget-row.volume').forEach((row) => {
        const hostEl = row.querySelector('.vol-host');
        const containerEl = row.querySelector('.vol-container');
        const modeEl = row.querySelector('.vol-mode');
        const host = (hostEl ? hostEl.value : '').trim();
        const container = (containerEl ? containerEl.value : '').trim();
        const mode = modeEl && modeEl.value === 'ro' ? 'ro' : 'rw';
        if (!host && !container) return;
        if (!host || !container) {
          throw new Error('Each volume needs both local and container path.');
        }
        const entry = `${host}:${container}`;
        if (mode === 'ro') ro.push(entry);
        else rw.push(entry);
      });

      return { ro_mounts: ro, rw_mounts: rw };
    }

    function collectEnvPayload(listId) {
      const list = document.getElementById(listId);
      const envVars = [];
      if (!list) return envVars;

      list.querySelectorAll('.widget-row.env').forEach((row) => {
        const keyEl = row.querySelector('.env-key');
        const valueEl = row.querySelector('.env-value');
        const key = (keyEl ? keyEl.value : '').trim();
        const value = valueEl ? valueEl.value : '';
        if (!key && !value) return;
        if (!key) {
          throw new Error('Environment variable key is required when value is provided.');
        }
        envVars.push(`${key}=${value}`);
      });

      return envVars;
    }

    function isEditingFormField() {
      const active = document.activeElement;
      if (!active) return false;
      const tag = (active.tagName || '').toLowerCase();
      return tag === 'input' || tag === 'textarea' || tag === 'select';
    }

    let hasRenderedOnce = false;
    let uiDirty = false;

    document.addEventListener('input', (event) => {
      if (event.target && event.target.closest('.widget-list')) {
        uiDirty = true;
      }
    });

    async function refresh() {
      if (hasRenderedOnce && (isEditingFormField() || uiDirty)) {
        return;
      }
      const errorEl = document.getElementById('ui-error');
      const projects = document.getElementById('projects');
      const chats = document.getElementById('chats');

      try {
        const state = await fetchJson('/api/state');
        errorEl.style.display = 'none';
        errorEl.textContent = '';

        projects.innerHTML = '';
        chats.innerHTML = '';

        state.projects.forEach(project => {
        const projectName = escapeHtml(project.name || 'Unnamed project');
        const projectId = escapeHtml(project.id || '');
        const projectBranch = escapeHtml(project.default_branch || 'master');
        const projectRepo = escapeHtml(project.repo_url || '');
        const setupScriptRaw = String(project.setup_script || '');
        const setupScript = escapeHtml(setupScriptRaw);
        const setupCommandCount = setupScriptRaw.split('\\n').map(line => line.trim()).filter(Boolean).length;
        const baseMode = normalizeBaseMode(project.base_image_mode);
        const baseValueRaw = String(project.base_image_value || '');
        const baseValue = escapeHtml(baseValueRaw);
        const baseSummary = baseValueRaw
          ? `${baseModeLabel(baseMode)}: ${escapeHtml(baseValueRaw)}`
          : 'Default agent_cli base image';
        const defaultVolumeCount = (project.default_ro_mounts || []).length + (project.default_rw_mounts || []).length;
        const defaultEnvCount = (project.default_env_vars || []).length;

        const card = document.createElement('div');
        card.className = 'chat';
        card.innerHTML = `
          <h3>${projectName}</h3>
          <div class="meta">ID: ${projectId}</div>
          <div class="meta">Branch: ${projectBranch}</div>
          <div class="meta">Setup commands: ${setupCommandCount}</div>
          <div class="meta">Base image source: ${baseSummary}</div>
          <div class="meta">Default volumes: ${defaultVolumeCount} | Default env vars: ${defaultEnvCount}</div>
          <div class="grid" style="margin-top:0.5rem;">
            <input value="${projectRepo}" placeholder="Repo URL" id="repo-${project.id}" disabled />
            <div class="row">
              <input id="profile-${project.id}" placeholder="Profile (e.g. fast)" />
              <button onclick="createChatForProject('${project.id}')">Start new chat</button>
            </div>
            <div class="row base-row">
              <select id="base-mode-${project.id}" onchange="updateBasePlaceholderForProject('${project.id}')">
                <option value="tag" ${baseMode === 'tag' ? 'selected' : ''}>Docker image tag</option>
                <option value="repo_path" ${baseMode === 'repo_path' ? 'selected' : ''}>Repo Dockerfile/path</option>
              </select>
              <input id="base-value-${project.id}" value="${baseValue}" placeholder="${escapeHtml(baseInputPlaceholder(baseMode))}" />
            </div>
            <textarea id="setup-${project.id}" class="script-input" placeholder="One command per line; executed sequentially in workspace">${setupScript}</textarea>
            <button onclick="saveProjectSettings('${project.id}')">Save project settings</button>
            <div class="section-label">Default volumes for new chats</div>
            <div id="new-volumes-${project.id}" class="widget-list"></div>
            <div class="inline-controls">
              <button type="button" class="secondary small" onclick="addVolumeRow('new-volumes-${project.id}')">Add volume</button>
            </div>
            <div class="section-label">Default environment variables for new chats</div>
            <div id="new-env-${project.id}" class="widget-list"></div>
            <div class="inline-controls">
              <button type="button" class="secondary small" onclick="addEnvRow('new-env-${project.id}')">Add environment variable</button>
            </div>
          </div>
          <div class="controls">
            <button class="danger" onclick="deleteProject('${project.id}')">Delete project</button>
          </div>
        `;
        projects.appendChild(card);
        seedVolumeRows(`new-volumes-${project.id}`, project.default_ro_mounts || [], project.default_rw_mounts || []);
        seedEnvRows(`new-env-${project.id}`, project.default_env_vars || []);
        });

        state.chats.forEach(chat => {
        const chatName = escapeHtml(chat.name || 'Unnamed chat');
        const chatProjectName = escapeHtml(chat.project_name || 'Unknown');
        const chatId = escapeHtml(chat.id || '');
        const chatProfile = escapeHtml(chat.profile || 'default');
        const chatProfileInput = escapeHtml(chat.profile || '');
        const workspace = escapeHtml(chat.workspace || '');
        const containerWorkspace = escapeHtml(chat.container_workspace || 'not started yet');
        const volumeCount = (chat.ro_mounts || []).length + (chat.rw_mounts || []).length;
        const envCount = (chat.env_vars || []).length;
        const card = document.createElement('div');
        card.className = 'chat';
        const pill = chat.is_running ? 'running' : 'stopped';
        card.innerHTML = `
          <h3>${chatName}</h3>
          <div class="meta"><span class="pill ${pill}">${chat.status}</span> ${chatProjectName}</div>
          <div class="meta">Chat ID: ${chatId}</div>
          <div class="meta">Profile: ${chatProfile}</div>
          <div class="meta">Workspace: ${workspace}</div>
          <div class="meta">Container folder: ${containerWorkspace}</div>
          <div class="meta">Volumes: ${volumeCount} | Env vars: ${envCount}</div>
          <div class="grid" style="margin-top:0.5rem;">
            <input id="chat-profile-${chat.id}" value="${chatProfileInput}" placeholder="Profile" />
            <div class="section-label">Volumes</div>
            <div id="chat-volumes-${chat.id}" class="widget-list"></div>
            <div class="inline-controls">
              <button type="button" class="secondary small" onclick="addVolumeRow('chat-volumes-${chat.id}')">Add volume</button>
            </div>
            <div class="section-label">Environment variables</div>
            <div id="chat-env-${chat.id}" class="widget-list"></div>
            <div class="inline-controls">
              <button type="button" class="secondary small" onclick="addEnvRow('chat-env-${chat.id}')">Add environment variable</button>
            </div>
          </div>
          <div class="controls">
            <button onclick="updateChat('${chat.id}')">Save config</button>
            ${chat.is_running ? `<button class="secondary" onclick="closeChat('${chat.id}')">Close</button>` : `<button onclick="startChat('${chat.id}')">Start</button>`}
            <button class="danger" onclick="deleteChat('${chat.id}')">Delete</button>
            <button class="secondary" onclick="viewLog('${chat.id}')">View logs</button>
          </div>
          <div id="log-${chat.id}" class="muted" style="white-space: pre-wrap; margin-top:0.5rem;"></div>
        `;
        chats.appendChild(card);
        seedVolumeRows(`chat-volumes-${chat.id}`, chat.ro_mounts || [], chat.rw_mounts || []);
        seedEnvRows(`chat-env-${chat.id}`, chat.env_vars || []);
        });

        hasRenderedOnce = true;
      } catch (err) {
        errorEl.style.display = 'block';
        errorEl.textContent = err && err.message ? err.message : String(err);
      }
    }

    async function createProject(event) {
      event.preventDefault();
      let defaultMounts;
      let defaultEnv;
      try {
        defaultMounts = collectMountPayload('project-default-volumes');
        defaultEnv = collectEnvPayload('project-default-env');
      } catch (err) {
        alert(err.message || String(err));
        return;
      }
      const payload = {
        repo_url: document.getElementById('project-repo').value,
        name: document.getElementById('project-name').value,
        default_branch: document.getElementById('project-branch').value,
        base_image_mode: document.getElementById('project-base-image-mode').value,
        base_image_value: document.getElementById('project-base-image-value').value,
        setup_script: document.getElementById('project-setup-script').value,
        default_ro_mounts: defaultMounts.ro_mounts,
        default_rw_mounts: defaultMounts.rw_mounts,
        default_env_vars: defaultEnv,
      };
      await fetchJson('/api/projects', { method: 'POST', body: JSON.stringify(payload) });
      document.getElementById('project-form').reset();
      updateBasePlaceholderForCreate();
      uiDirty = false;
      seedVolumeRows('project-default-volumes', [], []);
      seedEnvRows('project-default-env', []);
      await refresh();
    }

    async function saveProjectSettings(projectId) {
      let defaultMounts;
      let defaultEnv;
      try {
        defaultMounts = collectMountPayload(`new-volumes-${projectId}`);
        defaultEnv = collectEnvPayload(`new-env-${projectId}`);
      } catch (err) {
        alert(err.message || String(err));
        return;
      }
      const payload = {
        base_image_mode: document.getElementById(`base-mode-${projectId}`).value,
        base_image_value: document.getElementById(`base-value-${projectId}`).value,
        setup_script: document.getElementById(`setup-${projectId}`).value,
        default_ro_mounts: defaultMounts.ro_mounts,
        default_rw_mounts: defaultMounts.rw_mounts,
        default_env_vars: defaultEnv,
      };
      await fetchJson(`/api/projects/${projectId}`, { method: 'PATCH', body: JSON.stringify(payload) });
      uiDirty = false;
      await refresh();
    }

    async function createChatForProject(projectId) {
      let mountPayload;
      let envPayload;
      try {
        mountPayload = collectMountPayload(`new-volumes-${projectId}`);
        envPayload = collectEnvPayload(`new-env-${projectId}`);
      } catch (err) {
        alert(err.message || String(err));
        return;
      }
      const payload = {
        project_id: projectId,
        profile: document.getElementById(`profile-${projectId}`).value,
        ro_mounts: mountPayload.ro_mounts,
        rw_mounts: mountPayload.rw_mounts,
        env_vars: envPayload,
      };
      await fetchJson('/api/chats', { method: 'POST', body: JSON.stringify(payload) });
      await saveProjectSettings(projectId);
      uiDirty = false;
      await refresh();
    }

    async function startChat(chatId) {
      await fetchJson(`/api/chats/${chatId}/start`, { method: 'POST' });
      await refresh();
    }

    async function closeChat(chatId) {
      await fetchJson(`/api/chats/${chatId}/close`, { method: 'POST' });
      await refresh();
    }

    async function deleteChat(chatId) {
      await fetchJson(`/api/chats/${chatId}`, { method: 'DELETE' });
      await refresh();
    }

    async function deleteProject(projectId) {
      if (!confirm('Delete this project and all chats? This removes stored clones.')) return;
      await fetchJson(`/api/projects/${projectId}`, { method: 'DELETE' });
      await refresh();
    }

    async function updateChat(chatId) {
      let mountPayload;
      let envPayload;
      try {
        mountPayload = collectMountPayload(`chat-volumes-${chatId}`);
        envPayload = collectEnvPayload(`chat-env-${chatId}`);
      } catch (err) {
        alert(err.message || String(err));
        return;
      }
      const payload = {
        profile: document.getElementById(`chat-profile-${chatId}`).value,
        ro_mounts: mountPayload.ro_mounts,
        rw_mounts: mountPayload.rw_mounts,
        env_vars: envPayload,
      };
      await fetchJson(`/api/chats/${chatId}`, { method: 'PATCH', body: JSON.stringify(payload) });
      uiDirty = false;
      await refresh();
    }

    async function viewLog(chatId) {
      const el = document.getElementById(`log-${chatId}`);
      const text = await fetchText(`/api/chats/${chatId}/logs`);
      el.textContent = text || '';
    }

    updateBasePlaceholderForCreate();
    seedVolumeRows('project-default-volumes', [], []);
    seedEnvRows('project-default-env', []);
    refresh();
  </script>
</body>
</html>
    """


@click.command(help="Run the local agent hub.")
@click.option("--data-dir", default=str(_default_data_dir()), show_default=True, type=click.Path(file_okay=False, path_type=Path), help="Directory for hub state and chat workspaces.")
@click.option("--config-file", default=str(_default_config_file()), show_default=True, type=click.Path(exists=True, dir_okay=False, path_type=Path), help="Agent config file to pass into every chat.")
@click.option(
    "--system-prompt-file",
    default=str(_default_system_prompt_file()),
    show_default=True,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="Core system prompt markdown file to apply across all chat agents.",
)
@click.option("--host", default=DEFAULT_HOST, show_default=True)
@click.option("--port", default=DEFAULT_PORT, show_default=True, type=int)
@click.option(
    "--artifact-publish-base-url",
    default="",
    show_default=f"env {ARTIFACT_PUBLISH_BASE_URL_ENV} or auto (http://{DEFAULT_ARTIFACT_PUBLISH_HOST}:<port>)",
    help="Base URL reachable from agent_cli containers for artifact publish requests.",
)
@click.option("--frontend-build/--no-frontend-build", default=True, show_default=True, help="Automatically build the React frontend before starting the server.")
@click.option("--clean-start", is_flag=True, default=False, help="Clear hub chat artifacts and cached setup images before serving.")
@click.option(
    "--log-level",
    default=os.environ.get("AGENT_HUB_LOG_LEVEL", "info"),
    show_default=True,
    type=click.Choice(HUB_LOG_LEVEL_CHOICES, case_sensitive=False),
    help="Hub logging verbosity (applies to Agent Hub logs and Uvicorn).",
)
@click.option("--reload", is_flag=True, default=False)
def main(
    data_dir: Path,
    config_file: Path,
    system_prompt_file: Path,
    host: str,
    port: int,
    artifact_publish_base_url: str,
    frontend_build: bool,
    clean_start: bool,
    log_level: str,
    reload: bool,
) -> None:
    normalized_log_level = _normalize_log_level(log_level)
    _configure_hub_logging(normalized_log_level)
    LOGGER.info("Starting Agent Hub host=%s port=%s log_level=%s reload=%s", host, port, normalized_log_level, reload)
    if _default_config_file() and not Path(config_file).exists():
        raise click.ClickException(f"Missing config file: {config_file}")
    if _default_system_prompt_file() and not Path(system_prompt_file).exists():
        raise click.ClickException(f"Missing system prompt file: {system_prompt_file}")
    if frontend_build:
        _ensure_frontend_built(data_dir)

    try:
        state = HubState(
            data_dir=data_dir,
            config_file=config_file,
            system_prompt_file=system_prompt_file,
            hub_host=host,
            hub_port=port,
            artifact_publish_base_url=artifact_publish_base_url,
        )
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc
    LOGGER.info("Artifact publish base URL: %s", state.artifact_publish_base_url)
    if clean_start:
        summary = state.clean_start()
        click.echo(
            "Clean start completed: "
            f"stopped_chats={summary['stopped_chats']} "
            f"cleared_chats={summary['cleared_chats']} "
            f"projects_reset={summary['projects_reset']} "
            f"docker_images_requested={summary['docker_images_requested']}"
        )

    app = FastAPI()
    frontend_dist = _frontend_dist_dir()
    frontend_index = _frontend_index_file()

    @app.get("/", response_class=HTMLResponse)
    def index():
        if frontend_index.is_file():
            return FileResponse(frontend_index)
        return HTMLResponse(_frontend_not_built_page(), status_code=503)

    @app.websocket("/api/events")
    async def ws_events(websocket: WebSocket) -> None:
        listener = state.attach_events()
        await websocket.accept()
        LOGGER.debug("Hub events websocket connected.")
        snapshot_event = {
            "type": EVENT_TYPE_SNAPSHOT,
            "payload": state.events_snapshot(),
            "sent_at": _iso_now(),
        }
        await websocket.send_text(json.dumps(snapshot_event))

        async def stream_events() -> None:
            while True:
                try:
                    event = await asyncio.to_thread(listener.get, True, 0.5)
                except queue.Empty:
                    continue
                if event is None:
                    break
                await websocket.send_text(json.dumps(event))

        async def consume_input() -> None:
            while True:
                try:
                    message = await websocket.receive_text()
                except WebSocketDisconnect:
                    return
                if not message:
                    continue
                payload: Any = None
                try:
                    payload = json.loads(message)
                except json.JSONDecodeError:
                    continue
                if isinstance(payload, dict) and str(payload.get("type") or "") == "ping":
                    await websocket.send_text(
                        json.dumps({"type": "pong", "payload": {"at": _iso_now()}, "sent_at": _iso_now()})
                    )

        sender = asyncio.create_task(stream_events())
        receiver = asyncio.create_task(consume_input())
        try:
            done, pending = await asyncio.wait({sender, receiver}, return_when=asyncio.FIRST_COMPLETED)
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            for task in done:
                exc = task.exception()
                if exc and not isinstance(exc, WebSocketDisconnect):
                    raise exc
        except WebSocketDisconnect:
            pass
        finally:
            state._event_queue_put(listener, None)
            state.detach_events(listener)
            if not sender.done():
                sender.cancel()
            if not receiver.done():
                receiver.cancel()
            LOGGER.debug("Hub events websocket disconnected.")

    @app.get("/api/state")
    def api_state() -> dict[str, Any]:
        return state.state_payload()

    @app.get("/api/agent-capabilities")
    def api_agent_capabilities() -> dict[str, Any]:
        return state.agent_capabilities_payload()

    @app.post("/api/agent-capabilities/discover")
    def api_discover_agent_capabilities() -> dict[str, Any]:
        return state.start_agent_capabilities_discovery()

    @app.get("/api/settings/auth")
    def api_auth_settings() -> dict[str, Any]:
        return state.auth_settings_payload()

    @app.post("/api/settings/auth/openai/connect")
    async def api_connect_openai(request: Request) -> dict[str, Any]:
        payload = await request.json()
        if not isinstance(payload, dict):
            raise HTTPException(status_code=400, detail="Invalid JSON payload.")
        verify = _coerce_bool(payload.get("verify"), default=True, field_name="verify")
        return {"provider": state.connect_openai(payload.get("api_key"), verify=verify)}

    @app.post("/api/settings/auth/openai/disconnect")
    def api_disconnect_openai() -> dict[str, Any]:
        return {"provider": state.disconnect_openai()}

    @app.post("/api/settings/auth/github/connect")
    async def api_connect_github(request: Request) -> dict[str, Any]:
        payload = await request.json()
        if not isinstance(payload, dict):
            raise HTTPException(status_code=400, detail="Invalid JSON payload.")
        connection_mode = str(payload.get("connection_mode") or "").strip().lower()
        if not connection_mode:
            if str(payload.get("personal_access_token") or "").strip():
                connection_mode = GITHUB_CONNECTION_MODE_PERSONAL_ACCESS_TOKEN
            else:
                connection_mode = GITHUB_CONNECTION_MODE_GITHUB_APP

        if connection_mode == GITHUB_CONNECTION_MODE_GITHUB_APP:
            provider = state.connect_github_app(payload.get("installation_id"))
        elif connection_mode == GITHUB_CONNECTION_MODE_PERSONAL_ACCESS_TOKEN:
            provider = state.connect_github_personal_access_token(
                payload.get("personal_access_token"),
                host=payload.get("host"),
                owner_scopes=payload.get("owner_scopes"),
            )
        else:
            raise HTTPException(status_code=400, detail=f"Unsupported GitHub connection mode: {connection_mode}")
        return {"provider": provider}

    @app.post("/api/settings/auth/github/app/setup/start")
    async def api_start_github_app_setup(request: Request) -> dict[str, Any]:
        origin = f"{request.url.scheme}://{request.url.netloc}"
        raw_body = await request.body()
        if raw_body:
            try:
                payload = json.loads(raw_body.decode("utf-8", errors="ignore"))
            except json.JSONDecodeError as exc:
                raise HTTPException(status_code=400, detail="Invalid JSON payload.") from exc
            if payload is not None and not isinstance(payload, dict):
                raise HTTPException(status_code=400, detail="Invalid JSON payload.")
            if isinstance(payload, dict) and "origin" in payload:
                origin = str(payload.get("origin") or "").strip()
        return state.start_github_app_setup(origin=origin)

    @app.get("/api/settings/auth/github/app/setup/session")
    def api_github_app_setup_session() -> dict[str, Any]:
        return state.github_app_setup_session_payload()

    @app.get("/api/settings/auth/github/app/setup/callback", response_class=HTMLResponse)
    def api_github_app_setup_callback(request: Request) -> HTMLResponse:
        denied_error = str(request.query_params.get("error") or "").strip()
        state_value = str(request.query_params.get("state") or "").strip()
        if denied_error:
            message = str(request.query_params.get("error_description") or denied_error).strip()
            state.fail_github_app_setup(message=message or denied_error, state_value=state_value)
            return HTMLResponse(
                _github_app_setup_callback_page(
                    success=False,
                    message=message or "GitHub app setup was cancelled.",
                ),
                status_code=400,
            )

        code = str(request.query_params.get("code") or "").strip()
        try:
            payload = state.complete_github_app_setup(code=code, state_value=state_value)
            app_slug = str(payload.get("app_slug") or "")
            return HTMLResponse(
                _github_app_setup_callback_page(
                    success=True,
                    message="GitHub App setup completed. Return to Agent Hub and select the installation to connect.",
                    app_slug=app_slug,
                )
            )
        except HTTPException as exc:
            return HTMLResponse(
                _github_app_setup_callback_page(
                    success=False,
                    message=str(exc.detail or "GitHub app setup failed."),
                ),
                status_code=int(exc.status_code or 400),
            )

    @app.post("/api/settings/auth/github/disconnect")
    def api_disconnect_github() -> dict[str, Any]:
        return {"provider": state.disconnect_github_app()}

    @app.delete("/api/settings/auth/github/personal-access-tokens/{token_id}")
    def api_disconnect_github_personal_access_token(token_id: str) -> dict[str, Any]:
        return {"provider": state.disconnect_github_personal_access_token(token_id)}

    @app.get("/api/settings/auth/github/installations")
    def api_list_github_installations() -> dict[str, Any]:
        return state.list_github_app_installations()

    @app.post("/api/settings/auth/openai/title-test")
    async def api_test_openai_chat_title_generation(request: Request) -> dict[str, Any]:
        payload = await request.json()
        if not isinstance(payload, dict):
            raise HTTPException(status_code=400, detail="Invalid JSON payload.")
        return state.test_openai_chat_title_generation(payload.get("prompt"))

    @app.post("/api/settings/auth/openai/account/disconnect")
    def api_disconnect_openai_account() -> dict[str, Any]:
        return {"provider": state.disconnect_openai_account()}

    @app.get("/api/settings/auth/openai/account/session")
    def api_openai_account_session() -> dict[str, Any]:
        return state.openai_account_session_payload()

    @app.post("/api/settings/auth/openai/account/start")
    async def api_start_openai_account_login(request: Request) -> dict[str, Any]:
        method = "browser_callback"
        raw_body = await request.body()
        if raw_body:
            try:
                payload = json.loads(raw_body.decode("utf-8", errors="ignore"))
            except json.JSONDecodeError as exc:
                raise HTTPException(status_code=400, detail="Invalid JSON payload.") from exc
            if payload is not None and not isinstance(payload, dict):
                raise HTTPException(status_code=400, detail="Invalid JSON payload.")
            if isinstance(payload, dict):
                method = _normalize_openai_account_login_method(payload.get("method"))
        return state.start_openai_account_login(method=method)

    @app.post("/api/settings/auth/openai/account/cancel")
    def api_cancel_openai_account_login() -> dict[str, Any]:
        return state.cancel_openai_account_login()

    @app.get("/api/settings/auth/openai/account/callback")
    def api_openai_account_callback(request: Request) -> dict[str, Any]:
        callback_path = str(request.query_params.get("callback_path") or "/auth/callback")
        query_items = [(key, value) for key, value in request.query_params.multi_items() if key != "callback_path"]
        forwarded = state.forward_openai_account_callback(
            urllib.parse.urlencode(query_items, doseq=True),
            path=callback_path,
        )
        payload = state.openai_account_session_payload()
        payload["callback"] = forwarded
        return payload

    @app.post("/api/projects/auto-configure")
    async def api_auto_configure_project(request: Request) -> dict[str, Any]:
        payload = await request.json()
        if not isinstance(payload, dict):
            raise HTTPException(status_code=400, detail="Invalid JSON payload.")
        recommendation = await asyncio.to_thread(
            state.auto_configure_project,
            repo_url=payload.get("repo_url"),
            default_branch=payload.get("default_branch"),
        )
        return {"recommendation": recommendation}

    @app.post("/api/projects")
    async def api_create_project(request: Request) -> dict[str, Any]:
        payload = await request.json()
        repo_url = str(payload.get("repo_url", "")).strip()
        name = payload.get("name")
        if name is not None:
            name = str(name).strip() or None
        branch = payload.get("default_branch")
        setup_script = payload.get("setup_script")
        base_image_mode = _normalize_base_image_mode(payload.get("base_image_mode"))
        base_image_value = str(payload.get("base_image_value") or "").strip()
        default_ro_mounts = _parse_mounts(_empty_list(payload.get("default_ro_mounts")), "default read-only mount")
        default_rw_mounts = _parse_mounts(_empty_list(payload.get("default_rw_mounts")), "default read-write mount")
        default_env_vars = _parse_env_vars(_empty_list(payload.get("default_env_vars")))
        if setup_script is not None:
            setup_script = str(setup_script).strip()
        if isinstance(branch, str):
            branch = branch.strip() or None
        project = await asyncio.to_thread(
            state.add_project,
            repo_url=repo_url,
            name=name,
            default_branch=branch,
            setup_script=setup_script,
            base_image_mode=base_image_mode,
            base_image_value=base_image_value,
            default_ro_mounts=default_ro_mounts,
            default_rw_mounts=default_rw_mounts,
            default_env_vars=default_env_vars,
        )
        return {
            "project": project
        }

    @app.patch("/api/projects/{project_id}")
    async def api_update_project(project_id: str, request: Request) -> dict[str, Any]:
        payload = await request.json()
        update: dict[str, Any] = {}
        if "setup_script" in payload:
            script = payload.get("setup_script")
            update["setup_script"] = str(script).strip() if script is not None else ""
        if "name" in payload:
            name = payload.get("name")
            update["name"] = str(name).strip() if name is not None else ""
        if "default_branch" in payload:
            branch = payload.get("default_branch")
            update["default_branch"] = str(branch).strip() if branch is not None else ""
        if "base_image_mode" in payload:
            update["base_image_mode"] = _normalize_base_image_mode(payload.get("base_image_mode"))
        if "base_image_value" in payload:
            value = payload.get("base_image_value")
            update["base_image_value"] = str(value).strip() if value is not None else ""
        if "default_ro_mounts" in payload:
            update["default_ro_mounts"] = _parse_mounts(
                _empty_list(payload.get("default_ro_mounts")),
                "default read-only mount",
            )
        if "default_rw_mounts" in payload:
            update["default_rw_mounts"] = _parse_mounts(
                _empty_list(payload.get("default_rw_mounts")),
                "default read-write mount",
            )
        if "default_env_vars" in payload:
            update["default_env_vars"] = _parse_env_vars(_empty_list(payload.get("default_env_vars")))
        if not update:
            raise HTTPException(status_code=400, detail="No patch values provided.")
        project = await asyncio.to_thread(state.update_project, project_id, update)
        return {"project": project}

    @app.delete("/api/projects/{project_id}")
    def api_delete_project(project_id: str) -> None:
        state.delete_project(project_id)

    @app.get("/api/projects/{project_id}/build-logs", response_class=PlainTextResponse)
    def api_project_build_logs(project_id: str) -> str:
        project = state.project(project_id)
        if project is None:
            raise HTTPException(status_code=404, detail="Project not found.")
        log_path = state.project_build_log(project_id)
        if not log_path.exists():
            return ""
        return log_path.read_text(encoding="utf-8", errors="ignore")

    @app.post("/api/projects/{project_id}/chats/start")
    async def api_start_new_chat_for_project(project_id: str, request: Request) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        body = await request.body()
        if body:
            try:
                parsed_payload = json.loads(body.decode("utf-8"))
            except json.JSONDecodeError as exc:
                raise HTTPException(status_code=400, detail="Invalid JSON payload.") from exc
            if not isinstance(parsed_payload, dict):
                raise HTTPException(status_code=400, detail="Request body must be an object.")
            payload = parsed_payload

        agent_args = payload.get("agent_args")
        if agent_args is None and "codex_args" in payload:
            agent_args = payload.get("codex_args")
        if agent_args is None:
            agent_args = []
        if not isinstance(agent_args, list):
            raise HTTPException(status_code=400, detail="agent_args must be an array.")
        agent_type = (
            _normalize_chat_agent_type(payload.get("agent_type"), strict=True)
            if "agent_type" in payload
            else DEFAULT_CHAT_AGENT_TYPE
        )
        chat = await asyncio.to_thread(
            state.create_and_start_chat,
            project_id,
            agent_args=[str(arg) for arg in agent_args],
            agent_type=agent_type,
        )
        return {
            "chat": chat
        }

    @app.post("/api/chats")
    async def api_create_chat(request: Request) -> dict[str, Any]:
        payload = await request.json()
        project_id = str(payload.get("project_id", "")).strip()
        if not project_id:
            raise HTTPException(status_code=400, detail="project_id is required.")

        profile = payload.get("profile")
        if profile is not None:
            profile = str(profile).strip()

        ro_mounts = _parse_mounts(_empty_list(payload.get("ro_mounts")), "read-only mount")
        rw_mounts = _parse_mounts(_empty_list(payload.get("rw_mounts")), "read-write mount")
        env_vars = _parse_env_vars(_empty_list(payload.get("env_vars")))
        agent_args = payload.get("agent_args")
        if agent_args is None and "codex_args" in payload:
            agent_args = payload.get("codex_args")
        if agent_args is None:
            agent_args = []
        if not isinstance(agent_args, list):
            raise HTTPException(status_code=400, detail="agent_args must be an array.")
        agent_type = (
            _normalize_chat_agent_type(payload.get("agent_type"), strict=True)
            if "agent_type" in payload
            else DEFAULT_CHAT_AGENT_TYPE
        )
        chat = await asyncio.to_thread(
            state.create_chat,
            project_id,
            profile,
            ro_mounts,
            rw_mounts,
            env_vars,
            agent_args=[str(arg) for arg in agent_args],
            agent_type=agent_type,
        )
        return {
            "chat": chat
        }

    @app.post("/api/chats/{chat_id}/start")
    def api_start_chat(chat_id: str) -> dict[str, Any]:
        return {"chat": state.start_chat(chat_id)}

    @app.post("/api/chats/{chat_id}/close")
    def api_close_chat(chat_id: str) -> dict[str, Any]:
        return {"chat": state.close_chat(chat_id)}

    @app.patch("/api/chats/{chat_id}")
    async def api_patch_chat(chat_id: str, request: Request) -> dict[str, Any]:
        payload = await request.json()
        update: dict[str, Any] = {}
        if "profile" in payload:
            update["profile"] = str(payload.get("profile") or "").strip()
        if "ro_mounts" in payload:
            update["ro_mounts"] = _parse_mounts(_empty_list(payload.get("ro_mounts")), "read-only mount")
        if "rw_mounts" in payload:
            update["rw_mounts"] = _parse_mounts(_empty_list(payload.get("rw_mounts")), "read-write mount")
        if "env_vars" in payload:
            update["env_vars"] = _parse_env_vars(_empty_list(payload.get("env_vars")))
        args_key = "agent_args" if "agent_args" in payload else "codex_args" if "codex_args" in payload else ""
        if args_key:
            args = payload.get(args_key)
            if not isinstance(args, list):
                raise HTTPException(status_code=400, detail="agent_args must be an array.")
            update["agent_args"] = [str(arg) for arg in args]
        if "agent_type" in payload:
            update["agent_type"] = _normalize_chat_agent_type(payload.get("agent_type"), strict=True)
        if not update:
            raise HTTPException(status_code=400, detail="No patch values provided.")
        return {"chat": state.update_chat(chat_id, update)}

    @app.delete("/api/chats/{chat_id}")
    def api_delete_chat(chat_id: str) -> None:
        state.delete_chat(chat_id)

    @app.post("/api/chats/{chat_id}/title-prompt")
    async def api_chat_title_prompt(chat_id: str, request: Request) -> dict[str, Any]:
        payload = await request.json()
        if not isinstance(payload, dict):
            raise HTTPException(status_code=400, detail="Invalid JSON payload.")
        return state.record_chat_title_prompt(chat_id, payload.get("prompt"))

    @app.get("/api/chats/{chat_id}/artifacts")
    def api_list_chat_artifacts(chat_id: str) -> dict[str, Any]:
        return {"artifacts": state.list_chat_artifacts(chat_id)}

    @app.post("/api/chats/{chat_id}/artifacts/publish")
    async def api_publish_chat_artifact(chat_id: str, request: Request) -> dict[str, Any]:
        auth_header = str(request.headers.get("authorization") or "")
        token = ""
        if auth_header.lower().startswith("bearer "):
            token = auth_header[7:].strip()
        if not token:
            token = str(request.headers.get("x-agent-hub-artifact-token") or "").strip()

        try:
            payload = await request.json()
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=400, detail="Invalid JSON payload.") from exc
        if not isinstance(payload, dict):
            raise HTTPException(status_code=400, detail="Invalid JSON payload.")
        artifact = state.publish_chat_artifact(
            chat_id=chat_id,
            token=token,
            submitted_path=payload.get("path"),
            name=payload.get("name"),
        )
        return {"artifact": artifact}

    @app.get("/api/chats/{chat_id}/artifacts/{artifact_id}/download")
    def api_download_chat_artifact(chat_id: str, artifact_id: str) -> FileResponse:
        artifact_path, filename, media_type = state.resolve_chat_artifact_download(chat_id, artifact_id)
        return FileResponse(path=str(artifact_path), filename=filename, media_type=media_type)

    @app.get("/api/chats/{chat_id}/artifacts/{artifact_id}/preview")
    def api_preview_chat_artifact(chat_id: str, artifact_id: str) -> FileResponse:
        artifact_path, media_type = state.resolve_chat_artifact_preview(chat_id, artifact_id)
        return FileResponse(path=str(artifact_path), media_type=media_type)

    @app.get("/api/chats/{chat_id}/logs", response_class=PlainTextResponse)
    def api_chat_logs(chat_id: str) -> str:
        chat = state.chat(chat_id)
        if chat is None:
            raise HTTPException(status_code=404, detail="Chat not found.")
        log_path = state.chat_log(chat_id)
        if not log_path.exists():
            return ""
        return log_path.read_text(encoding="utf-8", errors="ignore")

    @app.websocket("/api/chats/{chat_id}/terminal")
    async def ws_chat_terminal(chat_id: str, websocket: WebSocket) -> None:
        chat = state.chat(chat_id)
        if chat is None:
            await websocket.close(code=4404)
            return

        try:
            listener, backlog = state.attach_terminal(chat_id)
        except HTTPException as exc:
            await websocket.close(code=4409, reason=str(exc.detail))
            return

        await websocket.accept()
        if backlog:
            await websocket.send_text(backlog)

        async def stream_output() -> None:
            while True:
                try:
                    chunk = await asyncio.to_thread(listener.get, True, 0.25)
                except queue.Empty:
                    continue
                if chunk is None:
                    break
                await websocket.send_text(chunk)

        async def stream_input() -> None:
            while True:
                message = await websocket.receive_text()
                payload: Any = None
                try:
                    payload = json.loads(message)
                except json.JSONDecodeError:
                    state.write_terminal_input(chat_id, message)
                    continue

                if isinstance(payload, dict):
                    message_type = str(payload.get("type") or "")
                    if message_type == "resize":
                        state.resize_terminal(chat_id, int(payload.get("cols") or 0), int(payload.get("rows") or 0))
                        continue
                    if message_type == "submit":
                        state.submit_chat_input_buffer(chat_id)
                        continue
                    if message_type == "input":
                        state.write_terminal_input(chat_id, str(payload.get("data") or ""))
                        continue

                state.write_terminal_input(chat_id, message)

        sender = asyncio.create_task(stream_output())
        receiver = asyncio.create_task(stream_input())
        try:
            done, pending = await asyncio.wait({sender, receiver}, return_when=asyncio.FIRST_COMPLETED)
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            for task in done:
                exc = task.exception()
                if exc and not isinstance(exc, WebSocketDisconnect):
                    raise exc
        except WebSocketDisconnect:
            pass
        finally:
            state._queue_put(listener, None)
            state.detach_terminal(chat_id, listener)
            if not sender.done():
                sender.cancel()
            if not receiver.done():
                receiver.cancel()

    assets_dir = frontend_dist / "assets"
    if assets_dir.is_dir():
        app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="frontend-assets")

    @app.on_event("shutdown")
    async def app_shutdown() -> None:
        try:
            summary = state.shutdown()
            if summary["closed_chats"] > 0:
                click.echo(
                    "Shutdown cleanup completed: "
                    f"stopped_chats={summary['stopped_chats']} "
                    f"closed_chats={summary['closed_chats']}"
                )
        except Exception as exc:  # pragma: no cover - defensive shutdown guard
            click.echo(f"Shutdown cleanup failed: {exc}", err=True)

    @app.get("/{path:path}")
    def spa(path: str):
        if path.startswith("api/"):
            raise HTTPException(status_code=404, detail="Not found.")
        candidate = frontend_dist / path
        if candidate.is_file():
            return FileResponse(candidate)
        if frontend_index.is_file():
            return FileResponse(frontend_index)
        return HTMLResponse(_frontend_not_built_page(), status_code=503)

    uvicorn.run(app, host=host, port=port, reload=reload, log_level=_uvicorn_log_level(normalized_log_level))


if __name__ == "__main__":
    main()
