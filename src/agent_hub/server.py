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
from agent_cli import cli as agent_cli_image
import uvicorn
from fastapi import FastAPI, HTTPException, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles


STATE_FILE_NAME = "state.json"
AGENT_CAPABILITIES_CACHE_FILE_NAME = "agent_capabilities_cache.json"
SECRETS_DIR_NAME = "secrets"
OPENAI_CREDENTIALS_FILE_NAME = "openai.env"
OPENAI_CODEX_AUTH_FILE_NAME = "auth.json"
GITHUB_APP_INSTALLATION_FILE_NAME = "github_app_installation.json"
GITHUB_TOKENS_FILE_NAME = "github_tokens.json"
GITLAB_TOKENS_FILE_NAME = "gitlab_tokens.json"
GIT_CREDENTIALS_DIR_NAME = "git_credentials"
CHAT_RUNTIME_CONFIGS_DIR_NAME = "chat_runtime_configs"
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
GIT_CONNECTION_MODE_PERSONAL_ACCESS_TOKEN = "personal_access_token"
PROJECT_CREDENTIAL_BINDING_MODE_AUTO = "auto"
PROJECT_CREDENTIAL_BINDING_MODE_SET = "set"
PROJECT_CREDENTIAL_BINDING_MODE_SINGLE = "single"
PROJECT_CREDENTIAL_BINDING_MODE_ALL = "all"
PROJECT_CREDENTIAL_BINDING_MODES = {
    PROJECT_CREDENTIAL_BINDING_MODE_AUTO,
    PROJECT_CREDENTIAL_BINDING_MODE_SET,
    PROJECT_CREDENTIAL_BINDING_MODE_SINGLE,
    PROJECT_CREDENTIAL_BINDING_MODE_ALL,
}
GITHUB_PERSONAL_ACCESS_TOKEN_MIN_CHARS = 20
GITHUB_PERSONAL_ACCESS_TOKEN_ID_MAX_CHARS = 120
GIT_CREDENTIAL_DEFAULT_SCHEME = "https"
GIT_CREDENTIAL_ALLOWED_SCHEMES = {"http", "https"}
GIT_PROVIDER_GITHUB = "github"
GIT_PROVIDER_GITLAB = "gitlab"
GITLAB_PERSONAL_ACCESS_TOKEN_REQUIRED_SCOPES = frozenset({"read_repository", "write_repository"})
DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 8765
DEFAULT_CONTAINER_HOME = "/workspace"
AGENT_TOOLS_MCP_RUNTIME_DIR_NAME = "agent_hub"
AGENT_TOOLS_MCP_RUNTIME_FILE_NAME = "agent_tools_mcp.py"
AGENT_TOOLS_URL_ENV = "AGENT_HUB_AGENT_TOOLS_URL"
AGENT_TOOLS_TOKEN_ENV = "AGENT_HUB_AGENT_TOOLS_TOKEN"
AGENT_TOOLS_PROJECT_ID_ENV = "AGENT_HUB_AGENT_TOOLS_PROJECT_ID"
AGENT_TOOLS_CHAT_ID_ENV = "AGENT_HUB_AGENT_TOOLS_CHAT_ID"
AGENT_TOOLS_MCP_CONTAINER_SCRIPT_PATH = str(
    PurePosixPath(DEFAULT_CONTAINER_HOME)
    / ".codex"
    / AGENT_TOOLS_MCP_RUNTIME_DIR_NAME
    / AGENT_TOOLS_MCP_RUNTIME_FILE_NAME
)
TMP_DIR_TMPFS_SPEC = "/tmp:mode=1777,exec"
ARTIFACT_PUBLISH_BASE_URL_ENV = "AGENT_ARTIFACT_BASE_URL"
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
DEFAULT_CLAUDE_MODEL = "opus"
SUPPORTED_CHAT_AGENT_TYPES = {AGENT_TYPE_CODEX, AGENT_TYPE_CLAUDE, AGENT_TYPE_GEMINI}
CHAT_LAYOUT_ENGINE_CLASSIC = "classic"
CHAT_LAYOUT_ENGINE_FLEXLAYOUT = "flexlayout"
DEFAULT_CHAT_LAYOUT_ENGINE = CHAT_LAYOUT_ENGINE_FLEXLAYOUT
SUPPORTED_CHAT_LAYOUT_ENGINES = {CHAT_LAYOUT_ENGINE_CLASSIC, CHAT_LAYOUT_ENGINE_FLEXLAYOUT}
AGENT_COMMAND_BY_TYPE = {
    AGENT_TYPE_CODEX: "codex",
    AGENT_TYPE_CLAUDE: "claude",
    AGENT_TYPE_GEMINI: "gemini",
}
AGENT_RESUME_ARGS_BY_TYPE = {
    AGENT_TYPE_CLAUDE: ("--continue",),
    AGENT_TYPE_GEMINI: ("--resume",),
}
AGENT_LABEL_BY_TYPE = {
    AGENT_TYPE_CODEX: "Codex",
    AGENT_TYPE_CLAUDE: "Claude",
    AGENT_TYPE_GEMINI: "Gemini CLI",
}
AGENT_CAPABILITY_DEFAULT_MODELS_BY_TYPE = {
    AGENT_TYPE_CODEX: ["default"],
    AGENT_TYPE_CLAUDE: ["default"],
    AGENT_TYPE_GEMINI: ["default"],
}
AGENT_CAPABILITY_DEFAULT_REASONING_BY_TYPE = {
    AGENT_TYPE_CODEX: ["default"],
    AGENT_TYPE_CLAUDE: ["default"],
    AGENT_TYPE_GEMINI: ["default"],
}
AGENT_CAPABILITY_MODEL_TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{1,120}")
AGENT_CAPABILITY_CODEX_MODEL_TOKEN_RE = re.compile(r"^(?:gpt-[a-z0-9][a-z0-9._-]*|o[0-9][a-z0-9._-]*)$")
AGENT_CAPABILITY_GEMINI_MODEL_ALIASES = {"auto", "pro", "flash", "flash-lite"}
AGENT_CAPABILITY_GEMINI_FALLBACK_MODELS = (
    "auto",
    "pro",
    "flash",
    "flash-lite",
    "gemini-2.5-pro",
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
)
AGENT_CAPABILITY_REASONING_LEVELS_BY_TYPE = {
    AGENT_TYPE_CODEX: ("minimal", "low", "medium", "high", "xhigh"),
    AGENT_TYPE_CLAUDE: ("low", "medium", "high", "max"),
    AGENT_TYPE_GEMINI: ("low", "medium", "high", "max"),
}
AGENT_CAPABILITY_REASONING_VALUE_RE = re.compile(r"\b(?:minimal|low|medium|high|xhigh|max)\b")
AGENT_CAPABILITY_REASONING_EXPECTED_VALUES_RE = re.compile(
    r"\bexpected\s+one\s+of\b\s+([^\n\r]+)",
    re.IGNORECASE,
)
AGENT_CAPABILITY_REASONING_LIST_RE = re.compile(
    r"(?:\b(?:reasoning|effort|thinking)(?:\s+(?:mode|modes|level|levels|effort))?\b[^:\n\r]{0,48})"
    r"(?:\b(?:possible values?|choices?|available values?|valid values?)\b)?[ \t]*[:=-][ \t]*([^\n\r]+)",
    re.IGNORECASE,
)
AGENT_CAPABILITY_MODEL_LIST_RE = re.compile(
    r"(?:\bmodel(?:\s+aliases?)?\b[^:\n\r]{0,48})"
    r"(?:\b(?:possible values?|choices?|available values?|valid values?)\b)?[ \t]*[:=-][ \t]*([^\n\r]+)",
    re.IGNORECASE,
)
AGENT_CAPABILITY_HELP_OPTION_RE = re.compile(r"(?<!\w)--([a-z0-9][a-z0-9-]*)", re.IGNORECASE)
AGENT_CAPABILITY_HELP_LIST_MARKER_RE = re.compile(
    r"\b(?:possible values?|choices?|available values?|valid values?)\b", re.IGNORECASE
)
AGENT_CAPABILITY_HELP_LIST_VALUE_RE = re.compile(
    r"\b(?:possible values?|choices?|available values?|valid values?)\b\s*[:=-]\s*([^\n\r]+)",
    re.IGNORECASE,
)
AGENT_CAPABILITY_HELP_INLINE_VALUES_RE = re.compile(
    r"\[\s*possible values?\s*:\s*([^\]]+)\]", re.IGNORECASE
)
AGENT_CAPABILITY_HELP_BULLET_VALUE_RE = re.compile(r"^\s*-\s*([A-Za-z0-9][A-Za-z0-9._-]{0,80})\b")
AGENT_CAPABILITY_HELP_NUMBERED_VALUE_RE = re.compile(
    r"^\s*(?:[>›]\s*)?(?:\d+[.)]\s+)([A-Za-z0-9][A-Za-z0-9._-]{0,80})\b"
)
AGENT_CAPABILITY_HELP_TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{1,80}")
AGENT_CAPABILITY_DISCOVERY_TIMEOUT_SECONDS = float(
    os.environ.get("AGENT_HUB_AGENT_CAPABILITY_DISCOVERY_TIMEOUT_SECONDS", "8.0")
)
AGENT_CAPABILITY_DISCOVERY_BASE_IMAGE_ENV = "AGENT_HUB_AGENT_CAPABILITY_DISCOVERY_BASE_IMAGE"
AGENT_CAPABILITY_DISCOVERY_RUNTIME_IMAGE_PREFIX = "agent-hub-capability"
AGENT_CAPABILITY_CODEX_MODELS_DOC_URL = "https://developers.openai.com/codex/models"
AGENT_CAPABILITY_CODEX_MODELS_DOC_NAME_RE = re.compile(
    r'\bname"\s*:\s*\[0,\s*"([a-z0-9][a-z0-9.-]*(?:-[a-z0-9][a-z0-9.-]*)*)"\]',
    re.IGNORECASE,
)
AGENT_CAPABILITY_CODEX_MODELS_DOC_MODEL_RE = re.compile(
    r"\bcodex\s+-m\s+([a-z0-9][a-z0-9.-]*(?:-[a-z0-9][a-z0-9.-]*)*)\b",
    re.IGNORECASE,
)
AGENT_CAPABILITY_CODEX_REASONING_FALLBACK_COMMAND = (
    "codex",
    "exec",
    "-c",
    'model_reasoning_effort="__agent_hub_invalid_reasoning__"',
    "capability-probe",
)
AGENT_CAPABILITY_DISCOVERY_COMMANDS_BY_TYPE = {
    AGENT_TYPE_CODEX: (
        ("codex", "--help"),
    ),
    AGENT_TYPE_CLAUDE: (
        ("claude", "--help"),
    ),
    AGENT_TYPE_GEMINI: (
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
CHAT_STATUS_STARTING = "starting"
CHAT_STATUS_RUNNING = "running"
CHAT_STATUS_STOPPED = "stopped"
CHAT_STATUS_FAILED = "failed"
CHAT_STATUS_REASON_CHAT_CREATED = "chat_created"
CHAT_STATUS_REASON_CHAT_CLOSE_REQUESTED = "chat_close_requested"
CHAT_STATUS_REASON_USER_CLOSED_TAB = "user_closed_tab"
CHAT_STATUS_REASON_STARTUP_RECONCILE_ORPHAN_PROCESS = "startup_reconcile_orphan_process"
CHAT_STATUS_REASON_STARTUP_RECONCILE_PROCESS_MISSING = "startup_reconcile_process_missing"
SUPPORTED_CHAT_STATUSES = {
    CHAT_STATUS_STARTING,
    CHAT_STATUS_RUNNING,
    CHAT_STATUS_STOPPED,
    CHAT_STATUS_FAILED,
}
STARTUP_STALE_DOCKER_CONTAINER_PREFIXES = ("agent-setup-", "agent-hub-openai-login-")
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
AUTO_CONFIG_CANCELLED_ERROR = "Auto-configure was cancelled by user."
AUTO_CONFIG_MISSING_OUTPUT_ERROR = "Temporary auto-config chat did not return a JSON recommendation."
AUTO_CONFIG_INVALID_OUTPUT_ERROR = "Temporary auto-config chat returned invalid JSON."
AUTO_CONFIG_NOTES_MAX_CHARS = 400
AUTO_CONFIG_REPO_DOCKERFILE_MIN_SCORE = 70
AUTO_CONFIG_REQUEST_ID_MAX_CHARS = 120
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
SNAPSHOT_AGENT_CLI_RUNTIME_INPUT_FILES = (
    "docker/agent_cli/Dockerfile",
    "docker/agent_cli/docker-entrypoint.py",
    "src/agent_hub/agent_tools_mcp.py",
    "src/agent_cli/cli.py",
)
ARTIFACT_STORAGE_DIR_NAME = "artifacts"
ARTIFACT_STORAGE_CHAT_DIR_NAME = "chats"
ARTIFACT_STORAGE_SESSION_DIR_NAME = "agent_tools_sessions"
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
    AGENT_TOOLS_URL_ENV,
    AGENT_TOOLS_TOKEN_ENV,
    AGENT_TOOLS_PROJECT_ID_ENV,
    AGENT_TOOLS_CHAT_ID_ENV,
}
AGENT_TOOLS_TOKEN_HEADER = "x-agent-hub-agent-tools-token"
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
EVENT_TYPE_AUTO_CONFIG_LOG = "auto_config_log"
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


@dataclass
class AutoConfigRequestState:
    request_id: str
    process: subprocess.Popen[str] | None = None
    cancel_requested: bool = False


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


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _agent_cli_runtime_inputs_fingerprint() -> str:
    repo_root = _repo_root()
    fingerprint_items: list[dict[str, str]] = []
    for relative_path in SNAPSHOT_AGENT_CLI_RUNTIME_INPUT_FILES:
        input_path = repo_root / relative_path
        file_hash = "missing"
        if input_path.is_file():
            try:
                file_hash = _sha256_file(input_path)
            except OSError as exc:
                file_hash = f"read-error:{exc.__class__.__name__}"
        fingerprint_items.append({"path": relative_path, "sha256": file_hash})

    payload = json.dumps(fingerprint_items, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


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


def _cli_arg_matches_option(arg: str, *, long_option: str, short_option: str | None = None) -> bool:
    if arg == long_option or arg.startswith(f"{long_option}="):
        return True
    if short_option and (arg == short_option or arg.startswith(f"{short_option}=")):
        return True
    return False


def _has_cli_option(args: list[str], *, long_option: str, short_option: str | None = None) -> bool:
    return any(_cli_arg_matches_option(str(arg), long_option=long_option, short_option=short_option) for arg in args)


def _cli_option_value(args: list[str], *, long_option: str, short_option: str | None = None) -> str:
    normalized_args = [str(arg) for arg in args]
    selected = ""
    for index, arg in enumerate(normalized_args):
        if arg == long_option or (short_option and arg == short_option):
            selected = str(normalized_args[index + 1]).strip() if index + 1 < len(normalized_args) else ""
            continue
        if arg.startswith(f"{long_option}="):
            _, _, selected = arg.partition("=")
            selected = str(selected).strip()
            continue
        if short_option and arg.startswith(f"{short_option}="):
            _, _, selected = arg.partition("=")
            selected = str(selected).strip()
            continue
    return selected


def _auto_config_analysis_model(agent_type: str, agent_args: list[str]) -> str:
    selected_model = _cli_option_value(agent_args, long_option="--model", short_option="-m")
    if selected_model and selected_model.lower() != "default":
        return selected_model
    if agent_type == AGENT_TYPE_CODEX:
        return AUTO_CONFIG_MODEL
    return f"{agent_type}-default"


def _strip_explicit_codex_default_model(agent_args: list[str]) -> list[str]:
    normalized_args = [str(arg) for arg in agent_args]
    filtered: list[str] = []
    skip_next = False
    for index, arg in enumerate(normalized_args):
        if skip_next:
            skip_next = False
            continue

        if arg == "--model":
            next_value = str(normalized_args[index + 1]).strip().lower() if index + 1 < len(normalized_args) else ""
            if next_value != "default":
                filtered.append(arg)
                if index + 1 < len(normalized_args):
                    filtered.append(normalized_args[index + 1])
            skip_next = index + 1 < len(normalized_args)
            continue

        if arg.startswith("--model="):
            _, _, value = arg.partition("=")
            if str(value).strip().lower() != "default":
                filtered.append(arg)
            continue

        if arg == "-m":
            next_value = str(normalized_args[index + 1]).strip().lower() if index + 1 < len(normalized_args) else ""
            if next_value != "default":
                filtered.append(arg)
                if index + 1 < len(normalized_args):
                    filtered.append(normalized_args[index + 1])
            skip_next = index + 1 < len(normalized_args)
            continue

        if arg.startswith("-m="):
            _, _, value = arg.partition("=")
            if str(value).strip().lower() != "default":
                filtered.append(arg)
            continue

        filtered.append(arg)

    return filtered


def _apply_default_model_for_agent(agent_type: str, agent_args: list[str]) -> list[str]:
    normalized_args = [str(arg) for arg in agent_args if str(arg).strip()]
    if agent_type != AGENT_TYPE_CLAUDE:
        if agent_type == AGENT_TYPE_CODEX:
            return _strip_explicit_codex_default_model(normalized_args)
        return normalized_args
    if _has_cli_option(normalized_args, long_option="--model", short_option="-m"):
        return normalized_args
    return ["--model", DEFAULT_CLAUDE_MODEL, *normalized_args]


def _normalize_chat_layout_engine(raw_value: Any, *, strict: bool = False) -> str:
    value = str(raw_value or "").strip().lower()
    if value in SUPPORTED_CHAT_LAYOUT_ENGINES:
        return value
    if strict:
        supported = ", ".join(sorted(SUPPORTED_CHAT_LAYOUT_ENGINES))
        raise HTTPException(status_code=400, detail=f"chat_layout_engine must be one of: {supported}.")
    return DEFAULT_CHAT_LAYOUT_ENGINE


def _normalize_chat_status(raw_value: Any, *, strict: bool = False) -> str:
    value = str(raw_value or "").strip().lower()
    if value in SUPPORTED_CHAT_STATUSES:
        return value
    if strict:
        supported = ", ".join(sorted(SUPPORTED_CHAT_STATUSES))
        raise HTTPException(status_code=400, detail=f"chat status must be one of: {supported}.")
    return CHAT_STATUS_STOPPED


def _normalize_optional_int(raw_value: Any) -> int | None:
    if raw_value is None or isinstance(raw_value, bool):
        return None
    if isinstance(raw_value, int):
        return raw_value
    if isinstance(raw_value, str) and raw_value.strip():
        try:
            return int(raw_value.strip())
        except ValueError:
            return None
    return None


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
    command_line = " ".join(cmd)
    start_time = time.monotonic()
    with log_path.open("a", encoding="utf-8", errors="ignore") as log_file:
        start_line = f"$ {command_line}\n"
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
        elapsed_ms = int((time.monotonic() - start_time) * 1000)
        completion_line = f"$ exit_code={result} (elapsed_ms={elapsed_ms})\n"
        log_file.write(completion_line)
        log_file.write("\n")
        log_file.flush()
        if on_output is not None:
            on_output(completion_line)
            on_output("\n")
    completed = subprocess.CompletedProcess(cmd, result, "", "")
    if check and completed.returncode != 0:
        command_name = command_line.split(" ", 1)[0] if command_line else "<unknown>"
        LOGGER.warning(
            "Command failed (snapshot task): command=%s exit_code=%s elapsed_ms=%s",
            command_name,
            completed.returncode,
            elapsed_ms,
        )
        raise HTTPException(status_code=400, detail=f"Command failed ({cmd[0]}) with exit code {completed.returncode}")
    LOGGER.debug(
        "Command completed (snapshot task): command=%s exit_code=%s elapsed_ms=%s",
        command_line.split(" ", 1)[0] if command_line else "<unknown>",
        completed.returncode,
        elapsed_ms,
    )
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
    return {
        "version": 1,
        "projects": {},
        "chats": {},
        "settings": {
            "default_agent_type": DEFAULT_CHAT_AGENT_TYPE,
            "chat_layout_engine": DEFAULT_CHAT_LAYOUT_ENGINE,
        },
    }


def _normalize_hub_settings_payload(raw_settings: Any) -> dict[str, Any]:
    if not isinstance(raw_settings, dict):
        raw_settings = {}
    return {
        "default_agent_type": _normalize_chat_agent_type(
            raw_settings.get("default_agent_type") or raw_settings.get("defaultAgentType")
        ),
        "chat_layout_engine": _normalize_chat_layout_engine(
            raw_settings.get("chat_layout_engine") or raw_settings.get("chatLayoutEngine")
        ),
    }


def _normalize_project_credential_binding(raw_binding: Any) -> dict[str, Any]:
    if not isinstance(raw_binding, dict):
        raw_binding = {}
    raw_mode = str(raw_binding.get("mode") or "").strip().lower()
    mode = (
        raw_mode
        if raw_mode in PROJECT_CREDENTIAL_BINDING_MODES
        else PROJECT_CREDENTIAL_BINDING_MODE_AUTO
    )
    raw_ids = raw_binding.get("credential_ids")
    credential_ids: list[str] = []
    if isinstance(raw_ids, list):
        seen: set[str] = set()
        for item in raw_ids:
            value = str(item or "").strip()
            if not value or value in seen:
                continue
            credential_ids.append(value)
            seen.add(value)
    source = str(raw_binding.get("source") or "").strip()
    updated_at = str(raw_binding.get("updated_at") or "").strip()
    return {
        "mode": mode,
        "credential_ids": credential_ids,
        "source": source,
        "updated_at": updated_at,
    }


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
    del fallback
    candidate_values = _normalize_mode_options(raw_values, ["default"])
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
    return filtered


def _normalize_reasoning_mode_options_for_agent(agent_type: str, raw_values: Any, fallback: list[str]) -> list[str]:
    del fallback
    candidate_values = _normalize_mode_options(raw_values, ["default"])
    candidate_levels = [value for value in candidate_values if _token_is_reasoning_candidate(agent_type, value)]
    if candidate_levels:
        return ["default", *candidate_levels]
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
        if value in {"claude", "claude-code"}:
            return False
        return (
            value.startswith("claude-")
            or value.startswith("sonnet")
            or value.startswith("opus")
            or value.startswith("haiku")
            or value in {"sonnet", "opus", "haiku"}
        )
    if agent_type == AGENT_TYPE_GEMINI:
        return value.startswith("gemini") or value in AGENT_CAPABILITY_GEMINI_MODEL_ALIASES
    return False


def _option_count_excluding_default(values: list[str]) -> int:
    return sum(1 for value in values if str(value or "").strip().lower() != "default")


def _token_is_reasoning_candidate(agent_type: str, token: str) -> bool:
    value = str(token or "").strip().lower()
    if not value or value == "default":
        return False
    levels = AGENT_CAPABILITY_REASONING_LEVELS_BY_TYPE.get(agent_type, ())
    return value in levels


def _fetch_codex_models_from_docs(timeout_seconds: float) -> list[str]:
    request = urllib.request.Request(
        AGENT_CAPABILITY_CODEX_MODELS_DOC_URL,
        headers={
            "Accept": "text/html",
            "User-Agent": "agent-hub-capability-discovery/1.0",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=max(1.0, float(timeout_seconds))) as response:
            status = int(response.getcode() or 0)
            body = response.read().decode("utf-8", errors="ignore")
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise RuntimeError(f"failed to fetch {AGENT_CAPABILITY_CODEX_MODELS_DOC_URL}: {exc}") from exc

    if status != 200:
        raise RuntimeError(
            f"failed to fetch {AGENT_CAPABILITY_CODEX_MODELS_DOC_URL}: HTTP {status}"
        )

    text = html.unescape(str(body or ""))
    discovered: list[str] = []
    seen: set[str] = set()

    # Prefer model card names from docs so we capture entries that do not have a
    # unique `codex -m ...` command token in the rendered examples.
    for match in AGENT_CAPABILITY_CODEX_MODELS_DOC_NAME_RE.finditer(text):
        token = str(match.group(1) or "").strip().lower()
        if not token or token in seen:
            continue
        if not _token_is_model_candidate(AGENT_TYPE_CODEX, token):
            continue
        seen.add(token)
        discovered.append(token)

    if discovered:
        return discovered

    for match in AGENT_CAPABILITY_CODEX_MODELS_DOC_MODEL_RE.finditer(text):
        token = str(match.group(1) or "").strip().lower()
        if not token or token in seen:
            continue
        if not _token_is_model_candidate(AGENT_TYPE_CODEX, token):
            continue
        seen.add(token)
        discovered.append(token)
    return discovered


def _extract_models_from_json_payload(payload: Any, agent_type: str) -> list[str]:
    discovered: list[str] = []
    seen: set[str] = set()
    model_keys = {"model", "name", "id", "slug", "display_name"}

    def add(value: Any) -> None:
        token = str(value or "").strip().lower()
        if not _token_is_model_candidate(agent_type, token) or token in seen:
            return
        seen.add(token)
        discovered.append(token)

    def walk(node: Any, parent_key: str = "") -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                normalized_key = str(key or "").strip().lower().replace("-", "_")
                if normalized_key in model_keys:
                    add(value)
                if isinstance(value, (dict, list)):
                    walk(value, normalized_key)
            return
        if isinstance(node, list):
            for item in node:
                walk(item, parent_key or "models")
            return
        if isinstance(node, str):
            if parent_key in model_keys or parent_key == "models":
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

    help_candidates = _extract_option_values_from_help_text(
        text,
        option_name_matcher=lambda option_name: option_name == "model" or option_name.endswith("-model"),
        token_validator=lambda token: _token_is_model_candidate(agent_type, token),
        contextual_list_pattern=AGENT_CAPABILITY_MODEL_LIST_RE,
    )
    if help_candidates:
        return help_candidates

    # Some CLIs print numbered model menus without explicit --model context.
    # Capture leading list tokens so capability discovery still reflects available models.
    discovered: list[str] = []
    seen: set[str] = set()
    for raw_line in text.splitlines():
        line = str(raw_line or "").rstrip()
        if not line:
            continue
        numbered_match = AGENT_CAPABILITY_HELP_NUMBERED_VALUE_RE.match(line)
        if not numbered_match:
            continue
        token = str(numbered_match.group(1) or "").strip().lower().strip(".,;:()[]{}")
        if not token or token in seen:
            continue
        if not _token_is_model_candidate(agent_type, token):
            continue
        seen.add(token)
        discovered.append(token)
    return discovered


def _extract_option_values_from_help_text(
    help_text: str,
    *,
    option_name_matcher: Callable[[str], bool],
    token_validator: Callable[[str], bool],
    contextual_list_pattern: re.Pattern[str] | None = None,
) -> list[str]:
    discovered: list[str] = []
    seen: set[str] = set()
    active_option_matches = False
    collect_bullet_values = False

    def add_token(raw_token: str) -> None:
        token = str(raw_token or "").strip().lower().strip(".,;:()[]{}")
        if not token or token in seen:
            return
        if not token_validator(token):
            return
        seen.add(token)
        discovered.append(token)

    def add_segment(raw_segment: str) -> None:
        for raw_token in AGENT_CAPABILITY_HELP_TOKEN_RE.findall(str(raw_segment or "")):
            add_token(raw_token)

    lines = str(help_text or "").splitlines()
    for raw_line in lines:
        line = str(raw_line or "").rstrip()
        lower_line = line.lower()
        option_names = [name.lower() for name in AGENT_CAPABILITY_HELP_OPTION_RE.findall(lower_line)]
        if option_names:
            active_option_matches = any(option_name_matcher(name) for name in option_names)
            collect_bullet_values = False

        if active_option_matches:
            # Parse the active option line directly so "e.g. 'sonnet' or 'opus'" style guidance is discovered.
            add_segment(line)
            for match in AGENT_CAPABILITY_HELP_INLINE_VALUES_RE.finditer(line):
                add_segment(match.group(1))

            has_inline_list_values = False
            for match in AGENT_CAPABILITY_HELP_LIST_VALUE_RE.finditer(line):
                add_segment(match.group(1))
                has_inline_list_values = True

            if AGENT_CAPABILITY_HELP_LIST_MARKER_RE.search(lower_line) and not has_inline_list_values:
                collect_bullet_values = True
                continue

        if collect_bullet_values:
            bullet_match = AGENT_CAPABILITY_HELP_BULLET_VALUE_RE.match(line)
            if bullet_match:
                add_token(bullet_match.group(1))
                continue
            numbered_match = AGENT_CAPABILITY_HELP_NUMBERED_VALUE_RE.match(line)
            if numbered_match:
                add_token(numbered_match.group(1))
                continue
            if not line.strip():
                collect_bullet_values = False

    if contextual_list_pattern is not None:
        for match in contextual_list_pattern.finditer(help_text):
            add_segment(match.group(1))

    return discovered


def _extract_reasoning_candidates_from_output(output_text: str, agent_type: str) -> list[str]:
    text = str(output_text or "")
    if not text:
        return []
    lower_text = text.lower()
    discovered: list[str] = []
    seen: set[str] = set()

    def add_token(raw_token: str) -> None:
        token = str(raw_token or "").strip().lower().strip(".,;:()[]{}")
        if not token or token in seen:
            return
        if not _token_is_reasoning_candidate(agent_type, token):
            return
        seen.add(token)
        discovered.append(token)

    def add_from_text(value: str) -> None:
        for token in AGENT_CAPABILITY_REASONING_VALUE_RE.findall(str(value or "").lower()):
            add_token(token)
        for token in AGENT_CAPABILITY_HELP_TOKEN_RE.findall(str(value or "").lower()):
            add_token(token)

    def maybe_normalized(values: list[str]) -> list[str]:
        normalized = _normalize_mode_options(values, ["default"])
        if _option_count_excluding_default(normalized) < 2:
            return []
        return normalized

    help_candidates = _extract_option_values_from_help_text(
        text,
        option_name_matcher=lambda option_name: any(
            keyword in option_name for keyword in ("effort", "reasoning", "thinking")
        ),
        token_validator=lambda token: _token_is_reasoning_candidate(agent_type, token),
        contextual_list_pattern=AGENT_CAPABILITY_REASONING_LIST_RE,
    )
    if help_candidates:
        normalized_help = maybe_normalized(help_candidates)
        if normalized_help:
            return normalized_help

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
            "supported_reasoning_levels",
            "effort_levels",
            "supported_effort_levels",
            "supported_effort",
            "supported_thinking_levels",
            "thinking_levels",
        }

        def walk(node: Any) -> None:
            if isinstance(node, dict):
                for key, value in node.items():
                    normalized_key = str(key or "").strip().lower().replace("-", "_")
                    if normalized_key in keys_with_mode_lists:
                        if isinstance(value, list):
                            for item in value:
                                if isinstance(item, dict):
                                    add_from_text(
                                        str(
                                            item.get("effort")
                                            or item.get("level")
                                            or item.get("name")
                                            or item.get("value")
                                            or ""
                                        )
                                    )
                                else:
                                    add_from_text(str(item or ""))
                        elif isinstance(value, dict):
                            add_from_text(
                                str(
                                    value.get("effort")
                                    or value.get("level")
                                    or value.get("name")
                                    or value.get("value")
                                    or ""
                                )
                            )
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
        normalized_json = maybe_normalized(discovered)
        if normalized_json:
            return normalized_json

    has_reasoning_context = any(
        marker in lower_text
        for marker in (
            "model_reasoning_effort",
            "reasoning_effort",
            "reasoning effort",
            "thinking_level",
            "thinking level",
        )
    )
    if has_reasoning_context:
        for match in AGENT_CAPABILITY_REASONING_EXPECTED_VALUES_RE.finditer(text):
            add_from_text(match.group(1))
        normalized_expected_values = maybe_normalized(discovered)
        if normalized_expected_values:
            return normalized_expected_values

    for match in AGENT_CAPABILITY_REASONING_LIST_RE.finditer(text):
        add_from_text(match.group(1))
    normalized_text = maybe_normalized(discovered)
    if normalized_text:
        return normalized_text
    return []


def _run_agent_capability_probe(cmd: list[str], timeout_seconds: float) -> tuple[int, str]:
    tokens = [str(token).strip() for token in cmd if str(token).strip()]
    if not tokens:
        return 2, "empty capability probe command"

    provider = _agent_capability_provider_for_command(tokens[0])
    if not provider:
        return 2, f"unsupported capability probe command: {tokens[0]}"

    try:
        runtime_image = _ensure_agent_capability_runtime_image(provider)
    except RuntimeError as exc:
        return 125, str(exc)

    docker_cmd = ["docker", "run", "--rm", runtime_image, *tokens]
    try:
        result = subprocess.run(
            docker_cmd,
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
        return 125, "docker command not found in PATH"


def _agent_capability_provider_for_command(command: str) -> str:
    normalized_command = Path(str(command or "").strip()).name.lower()
    for agent_type, agent_command in AGENT_COMMAND_BY_TYPE.items():
        if normalized_command == str(agent_command).strip().lower():
            return agent_type
    return ""


def _agent_capability_discovery_base_image() -> str:
    configured = str(os.environ.get(AGENT_CAPABILITY_DISCOVERY_BASE_IMAGE_ENV, "")).strip()
    if configured:
        return configured
    return DEFAULT_AGENT_IMAGE


def _agent_capability_runtime_image_tag(agent_provider: str, base_image: str) -> str:
    safe_provider = re.sub(r"[^a-z0-9_.-]+", "-", str(agent_provider or "").strip().lower()).strip("-")
    if not safe_provider:
        safe_provider = "agent"
    payload = json.dumps(
        {
            "provider": str(agent_provider or "").strip().lower(),
            "base_image": str(base_image or "").strip(),
            "runtime_inputs_fingerprint": _agent_cli_runtime_inputs_fingerprint(),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
    return f"{AGENT_CAPABILITY_DISCOVERY_RUNTIME_IMAGE_PREFIX}-{safe_provider}-{digest}"


def _ensure_agent_capability_runtime_image(agent_provider: str) -> str:
    normalized_provider = _normalize_chat_agent_type(agent_provider)
    if normalized_provider not in SUPPORTED_CHAT_AGENT_TYPES:
        raise RuntimeError(f"Unsupported capability discovery provider: {agent_provider}")
    base_image = _agent_capability_discovery_base_image()
    runtime_image = _agent_capability_runtime_image_tag(normalized_provider, base_image)
    try:
        agent_cli_image._ensure_runtime_image_built_if_missing(
            base_image=base_image,
            target_image=runtime_image,
            agent_provider=normalized_provider,
        )
    except click.ClickException as exc:
        raise RuntimeError(
            "Capability discovery runtime image build failed "
            f"(base_image={base_image}, provider={normalized_provider}, target_image={runtime_image}): {exc}"
        ) from exc
    except FileNotFoundError as exc:
        raise RuntimeError("docker command not found in PATH") from exc
    return runtime_image


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


def _split_host_port(host: str) -> tuple[str, int | None]:
    candidate = str(host or "").strip().lower()
    if not candidate:
        return "", None
    if ":" not in candidate:
        return candidate, None
    hostname, port_text = candidate.rsplit(":", 1)
    if not hostname or not port_text.isdigit():
        raise HTTPException(status_code=400, detail=f"Invalid host: {host}")
    port_value = int(port_text)
    if port_value <= 0 or port_value > 65535:
        raise HTTPException(status_code=400, detail=f"Invalid host: {host}")
    return hostname, port_value


def _normalize_github_credential_scheme(raw_value: Any, field_name: str = "scheme") -> str:
    scheme = str(raw_value or "").strip().lower() or GIT_CREDENTIAL_DEFAULT_SCHEME
    if scheme not in GIT_CREDENTIAL_ALLOWED_SCHEMES:
        raise HTTPException(status_code=400, detail=f"Invalid {field_name}: {raw_value}")
    return scheme


def _normalize_github_credential_endpoint(
    raw_value: Any,
    field_name: str = "host",
    default_scheme: str = GIT_CREDENTIAL_DEFAULT_SCHEME,
) -> tuple[str, str]:
    candidate = str(raw_value or "").strip()
    if not candidate:
        raise HTTPException(status_code=400, detail=f"{field_name} is required.")

    default_scheme_value = _normalize_github_credential_scheme(default_scheme, field_name=f"{field_name}_scheme")
    scheme = default_scheme_value
    host_value = candidate

    if "://" in candidate:
        parsed = urllib.parse.urlsplit(candidate)
        scheme = _normalize_github_credential_scheme(parsed.scheme, field_name=f"{field_name}_scheme")
        if parsed.username or parsed.password:
            raise HTTPException(status_code=400, detail=f"Invalid {field_name}: {raw_value}")
        if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
            raise HTTPException(status_code=400, detail=f"Invalid {field_name}: {raw_value}")
        hostname = str(parsed.hostname or "").strip().lower()
        if not hostname:
            raise HTTPException(status_code=400, detail=f"Invalid {field_name}: {raw_value}")
        try:
            port = parsed.port
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"Invalid {field_name}: {raw_value}") from exc
        host_value = f"{hostname}:{port}" if port else hostname
    else:
        host_value = candidate.lower()

    hostname, port = _split_host_port(host_value)
    if not hostname:
        raise HTTPException(status_code=400, detail=f"Invalid {field_name}: {raw_value}")
    if not re.fullmatch(r"[a-z0-9.-]+", hostname):
        raise HTTPException(status_code=400, detail=f"Invalid {field_name}: {raw_value}")
    normalized_host = f"{hostname}:{port}" if port else hostname
    return scheme, normalized_host


def _normalize_github_credential_host(raw_value: Any, field_name: str = "host") -> str:
    _scheme, host = _normalize_github_credential_endpoint(raw_value, field_name=field_name)
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
        host = parsed.hostname.lower()
        try:
            port = parsed.port
        except ValueError:
            port = None
        return f"{host}:{port}" if port else host

    scp_match = re.match(r"^[^@]+@([^:]+):", candidate)
    if scp_match:
        return scp_match.group(1).lower().strip()

    ssh_match = re.match(r"^ssh://[^@]+@([^/]+)/", candidate)
    if ssh_match:
        return ssh_match.group(1).lower().strip()

    return ""


def _git_repo_scheme(repo_url: str) -> str:
    candidate = str(repo_url or "").strip()
    if not candidate:
        return ""

    parsed = urllib.parse.urlsplit(candidate)
    if parsed.scheme:
        return parsed.scheme.lower().strip()

    if re.match(r"^[^@]+@[^:]+:", candidate):
        return "ssh"
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


def _agent_tools_mcp_source_path() -> Path:
    return Path(__file__).resolve().with_name(AGENT_TOOLS_MCP_RUNTIME_FILE_NAME)


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


def _normalize_base_image_value(mode: Any, value: Any) -> str:
    normalized_mode = _normalize_base_image_mode(mode)
    normalized_value = str(value or "").strip()
    if normalized_mode == "tag":
        return normalized_value or str(agent_cli_image.DEFAULT_BASE_IMAGE)
    return normalized_value


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


def _new_agent_tools_token() -> str:
    return secrets.token_hex(24)


def _hash_agent_tools_token(token: str) -> str:
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
        storage_relative_path = _coerce_artifact_relative_path(raw_artifact.get("storage_relative_path"))
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
                "storage_relative_path": storage_relative_path,
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


def _codex_exec_error_message_full(output_text: str) -> str:
    cleaned = ANSI_ESCAPE_RE.sub("", str(output_text or "")).replace("\r", "\n")
    lines = [line.strip() for line in cleaned.splitlines() if line.strip()]
    if not lines:
        return "Unknown error."
    for line in reversed(lines):
        if line.lower().startswith("error:"):
            detail = line.split(":", 1)[1].strip()
            if detail:
                return detail
    return lines[-1]


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

    for candidate in candidates:
        candidate_text = str(candidate or "").strip()
        if not candidate_text:
            continue
        idx = 0
        while True:
            start = candidate_text.find("{", idx)
            if start < 0:
                break
            try:
                parsed, _end = json.JSONDecoder().raw_decode(candidate_text, start)
            except json.JSONDecodeError:
                idx = start + 1
                continue
            if isinstance(parsed, dict):
                return parsed
            idx = start + 1
    raise ValueError("invalid json object")


def _json_payload_preview(raw_body: bytes, *, max_bytes: int = 160) -> str:
    clipped = raw_body[:max_bytes]
    return clipped.hex()


def _is_json_content_type(content_type: str) -> bool:
    normalized = str(content_type or "").strip().lower()
    return normalized == "application/json" or normalized.endswith("+json") or normalized == "text/json"


def _artifact_upload_name(request: Request, *, fallback: str) -> str:
    requested_name = (
        str(request.headers.get("x-agent-hub-artifact-name") or request.query_params.get("name") or "").strip()
    )
    if not requested_name:
        content_type = str(request.headers.get("content-type") or "").split(";", 1)[0].strip().lower()
        if content_type:
            extension = mimetypes.guess_extension(content_type) or ""
            requested_name = f"{fallback}{extension}"
        else:
            requested_name = str(fallback)
    requested_name = _normalize_artifact_name(requested_name, fallback=fallback)
    if not requested_name:
        requested_name = str(fallback)

    safe_name = Path(requested_name).name
    return _normalize_artifact_name(safe_name, fallback=fallback)


def _write_artifact_upload_to_workspace(
    workspace: Path,
    raw_body: bytes,
    *,
    requested_name: str,
    context: str,
) -> tuple[Path, str]:
    uploads_root = (workspace / ".agent-hub-artifacts").resolve()
    try:
        uploads_root.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise HTTPException(status_code=500, detail="Failed to prepare artifact upload staging directory.") from exc

    normalized_name = _normalize_artifact_name(requested_name, fallback="artifact")
    if not normalized_name:
        normalized_name = "artifact"
    normalized_name = Path(normalized_name).name
    if not normalized_name:
        normalized_name = "artifact"

    normalized_name = _normalize_artifact_name(normalized_name, fallback="artifact")
    staged_path = uploads_root / f"{uuid.uuid4().hex}-{normalized_name}"
    try:
        staged_path.write_bytes(raw_body)
    except OSError as exc:
        raise HTTPException(
            status_code=500,
            detail="Failed to persist uploaded artifact payload to chat workspace.",
        ) from exc
    LOGGER.info("Staged binary artifact payload for %s to %s", context, staged_path)
    return staged_path.resolve(), str(normalized_name)


async def _parse_artifact_request_payload(
    request: Request,
    *,
    context: str,
    workspace: Path,
) -> tuple[dict[str, Any], list[Path]]:
    content_type = str(request.headers.get("content-type") or "").split(";", 1)[0].strip().lower()
    if content_type == "multipart/form-data":
        form = await request.form()
        upload: UploadFile | None = None
        if isinstance(form.get("file"), UploadFile):
            upload = form.get("file")  # type: ignore[assignment]
        else:
            for value in form.values():
                if isinstance(value, UploadFile):
                    upload = value
                    break
        if upload is None:
            LOGGER.warning("Multipart artifact payload missing file for %s", context)
            raise HTTPException(status_code=400, detail="Multipart payload must include a file field.")

        raw_body = await upload.read()
        requested_name = str(form.get("name") or "").strip()
        if not requested_name:
            requested_name = _normalize_artifact_name(upload.filename, fallback=_artifact_upload_name(request, fallback="artifact"))
        uploaded_path, uploaded_name = _write_artifact_upload_to_workspace(
            workspace,
            raw_body,
            requested_name=requested_name,
            context=context,
        )
        return {"path": str(uploaded_path), "name": uploaded_name}, [uploaded_path]

    raw_body = await request.body()
    if not raw_body:
        if _is_json_content_type(content_type):
            LOGGER.warning("Invalid JSON payload for %s: empty body.", context)
            raise HTTPException(status_code=400, detail="Invalid JSON payload.")
        LOGGER.warning("Empty artifact payload for %s", context)
        raise HTTPException(status_code=400, detail="Artifact payload is empty.")

    if _is_json_content_type(content_type):
        try:
            payload = json.loads(raw_body)
        except UnicodeDecodeError as exc:
            LOGGER.warning(
                "Invalid UTF-8 JSON payload for %s (body_bytes=%s): %s",
                context,
                _json_payload_preview(raw_body),
                exc,
            )
            raise HTTPException(status_code=400, detail="Invalid JSON payload.") from exc
        except json.JSONDecodeError as exc:
            LOGGER.warning(
                "Invalid JSON payload for %s (body_bytes=%s): %s",
                context,
                _json_payload_preview(raw_body),
                exc,
            )
            raise HTTPException(status_code=400, detail="Invalid JSON payload.") from exc
        if not isinstance(payload, dict):
            LOGGER.warning(
                "Invalid JSON payload for %s: expected object, got %s. body_bytes=%s",
                context,
                type(payload).__name__,
                _json_payload_preview(raw_body),
            )
            raise HTTPException(status_code=400, detail="Invalid JSON payload.")
        return payload, []

    artifact_name = _artifact_upload_name(request, fallback="artifact")
    uploaded_path, uploaded_name = _write_artifact_upload_to_workspace(
        workspace,
        raw_body,
        requested_name=artifact_name,
        context=context,
    )
    return {"path": str(uploaded_path), "name": uploaded_name}, [uploaded_path]


def _cleanup_uploaded_artifact_paths(uploaded_paths: list[Path]) -> None:
    for uploaded_path in uploaded_paths:
        try:
            uploaded_path.unlink()
        except OSError:
            continue


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
    return 5


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


def _docker_remove_stale_containers(prefixes: tuple[str, ...]) -> int:
    normalized_prefixes = tuple(str(prefix or "").strip() for prefix in prefixes if str(prefix or "").strip())
    if not normalized_prefixes:
        return 0
    if shutil.which("docker") is None:
        return 0

    try:
        list_result = subprocess.run(
            ["docker", "ps", "-a", "--format", "{{.Names}}\t{{.State}}"],
            check=False,
            text=True,
            capture_output=True,
        )
    except OSError:
        return 0
    if list_result.returncode != 0:
        return 0

    stale_names: set[str] = set()
    for raw_line in list_result.stdout.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if "\t" in line:
            name, state = line.split("\t", 1)
        else:
            name, state = line, ""
        normalized_name = str(name or "").strip()
        normalized_state = str(state or "").strip().lower()
        if not normalized_name:
            continue
        if not any(normalized_name.startswith(prefix) for prefix in normalized_prefixes):
            continue
        if normalized_state in {"running", "restarting", "paused"}:
            continue
        stale_names.add(normalized_name)

    if not stale_names:
        return 0

    try:
        subprocess.run(
            ["docker", "rm", "-f", *sorted(stale_names)],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError:
        return 0
    return len(stale_names)


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
        self.agent_tools_mcp_runtime_script = (
            self.host_codex_dir / AGENT_TOOLS_MCP_RUNTIME_DIR_NAME / AGENT_TOOLS_MCP_RUNTIME_FILE_NAME
        )
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
        self.artifacts_dir = self.data_dir / ARTIFACT_STORAGE_DIR_NAME
        self.chat_artifacts_dir = self.artifacts_dir / ARTIFACT_STORAGE_CHAT_DIR_NAME
        self.session_artifacts_dir = self.artifacts_dir / ARTIFACT_STORAGE_SESSION_DIR_NAME
        self.secrets_dir = self.data_dir / SECRETS_DIR_NAME
        self.chat_runtime_configs_dir = self.data_dir / CHAT_RUNTIME_CONFIGS_DIR_NAME
        self.openai_credentials_file = self.secrets_dir / OPENAI_CREDENTIALS_FILE_NAME
        self.github_app_settings_file = self.secrets_dir / GITHUB_APP_SETTINGS_FILE_NAME
        self.github_app_installation_file = self.secrets_dir / GITHUB_APP_INSTALLATION_FILE_NAME
        self.github_tokens_file = self.secrets_dir / GITHUB_TOKENS_FILE_NAME
        self.gitlab_tokens_file = self.secrets_dir / GITLAB_TOKENS_FILE_NAME
        self.git_credentials_dir = self.secrets_dir / GIT_CREDENTIALS_DIR_NAME
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
        self._startup_reconcile_lock = Lock()
        self._startup_reconcile_thread: Thread | None = None
        self._startup_reconcile_scheduled = False
        self._agent_tools_sessions_lock = Lock()
        self._agent_tools_sessions: dict[str, dict[str, Any]] = {}
        self._auto_config_requests_lock = Lock()
        self._auto_config_requests: dict[str, AutoConfigRequestState] = {}
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.project_dir.mkdir(parents=True, exist_ok=True)
        self.chat_dir.mkdir(parents=True, exist_ok=True)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)
        self.chat_artifacts_dir.mkdir(parents=True, exist_ok=True)
        self.session_artifacts_dir.mkdir(parents=True, exist_ok=True)
        self.secrets_dir.mkdir(parents=True, exist_ok=True)
        self.chat_runtime_configs_dir.mkdir(parents=True, exist_ok=True)
        self.git_credentials_dir.mkdir(parents=True, exist_ok=True)
        self.host_codex_dir.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.secrets_dir, 0o700)
        except OSError:
            pass
        self._reload_github_app_settings()
        self._load_agent_capabilities_cache()
        self._reconcile_project_build_state()

    def _reconcile_project_build_state(self) -> None:
        state = self.load()
        rebuild_project_ids: list[str] = []
        changed = False

        for project_id, project in state["projects"].items():
            if not isinstance(project, dict):
                continue

            build_status = str(project.get("build_status") or "")
            if build_status in {"pending", "building"}:
                rebuild_project_ids.append(project_id)
                continue
            if build_status != "ready":
                continue

            expected_snapshot_tag = self._project_setup_snapshot_tag(project)
            snapshot_tag = str(project.get("setup_snapshot_image") or "").strip()
            snapshot_ready = (
                bool(snapshot_tag)
                and snapshot_tag == expected_snapshot_tag
                and _docker_image_exists(snapshot_tag)
            )
            if snapshot_ready:
                continue

            project["setup_snapshot_image"] = ""
            project.pop("snapshot_updated_at", None)
            project["build_status"] = "pending"
            project["build_error"] = ""
            project["build_started_at"] = ""
            project["build_finished_at"] = ""
            project["updated_at"] = _iso_now()
            state["projects"][project_id] = project
            changed = True
            rebuild_project_ids.append(project_id)

        if changed:
            self.save(state, reason="project_build_reconcile")
        for project_id in rebuild_project_ids:
            self._schedule_project_build(project_id)

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
        state = {
            "version": loaded.get("version", 1),
            "projects": projects,
            "chats": chats,
            "settings": _normalize_hub_settings_payload(loaded.get("settings")),
        }
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
            chat["status"] = _normalize_chat_status(chat.get("status"))
            chat["status_reason"] = _compact_whitespace(str(chat.get("status_reason") or ""))
            chat["last_status_transition_at"] = str(
                chat.get("last_status_transition_at") or chat.get("updated_at") or chat.get("created_at") or ""
            )
            chat["start_error"] = _compact_whitespace(str(chat.get("start_error") or ""))
            chat["last_exit_code"] = _normalize_optional_int(chat.get("last_exit_code"))
            chat["last_exit_at"] = str(chat.get("last_exit_at") or "")
            chat["stop_requested_at"] = str(chat.get("stop_requested_at") or "")
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
            chat["agent_tools_token_hash"] = str(chat.get("agent_tools_token_hash") or "")
            chat["agent_tools_token_issued_at"] = str(chat.get("agent_tools_token_issued_at") or "")
            chat["create_request_id"] = _compact_whitespace(str(chat.get("create_request_id") or "")).strip()
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

    def _emit_auto_config_log(self, request_id: str, text: str, replace: bool = False) -> None:
        self._emit_event(
            EVENT_TYPE_AUTO_CONFIG_LOG,
            {
                "request_id": str(request_id),
                "text": str(text or ""),
                "replace": bool(replace),
            },
        )

    def _normalize_auto_config_request_id(self, request_id: Any) -> str:
        return str(request_id or "").strip()[:AUTO_CONFIG_REQUEST_ID_MAX_CHARS]

    def _auto_config_request_state(self, request_id: str) -> AutoConfigRequestState | None:
        normalized_request_id = self._normalize_auto_config_request_id(request_id)
        if not normalized_request_id:
            return None
        with self._auto_config_requests_lock:
            return self._auto_config_requests.get(normalized_request_id)

    def _register_auto_config_request(self, request_id: str) -> None:
        normalized_request_id = self._normalize_auto_config_request_id(request_id)
        if not normalized_request_id:
            return
        with self._auto_config_requests_lock:
            # Keep existing request state so cancellation/process tracking cannot be reset
            # by repeated registration calls for the same request id.
            if normalized_request_id in self._auto_config_requests:
                return
            self._auto_config_requests[normalized_request_id] = AutoConfigRequestState(request_id=normalized_request_id)

    def _set_auto_config_request_process(self, request_id: str, process: subprocess.Popen[str] | None = None) -> None:
        normalized_request_id = self._normalize_auto_config_request_id(request_id)
        if not normalized_request_id:
            return
        should_stop_process = False
        with self._auto_config_requests_lock:
            state = self._auto_config_requests.get(normalized_request_id)
            if state is None:
                return
            state.process = process
            should_stop_process = (
                bool(state.cancel_requested)
                and process is not None
                and _is_process_running(process.pid)
            )
        if should_stop_process:
            _stop_process(process.pid)

    def _is_auto_config_request_cancelled(self, request_id: str) -> bool:
        state = self._auto_config_request_state(request_id)
        return bool(state and state.cancel_requested)

    def _clear_auto_config_request(self, request_id: str) -> None:
        normalized_request_id = self._normalize_auto_config_request_id(request_id)
        if not normalized_request_id:
            return
        with self._auto_config_requests_lock:
            self._auto_config_requests.pop(normalized_request_id, None)

    def cancel_auto_configure_project(self, request_id: str) -> dict[str, Any]:
        normalized_request_id = self._normalize_auto_config_request_id(request_id)
        if not normalized_request_id:
            raise HTTPException(status_code=400, detail="request_id is required.")

        process_to_cancel: subprocess.Popen[str] | None = None
        with self._auto_config_requests_lock:
            request_state = self._auto_config_requests.get(normalized_request_id)
            if request_state is None:
                return {"request_id": normalized_request_id, "cancelled": False, "active": False}
            request_state.cancel_requested = True
            process_to_cancel = request_state.process if request_state.process is not None else None

        was_active = bool(process_to_cancel is not None and _is_process_running(process_to_cancel.pid))
        if was_active:
            _stop_process(process_to_cancel.pid)
        return {"request_id": normalized_request_id, "cancelled": True, "active": was_active}

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
        discovered_models: list[str] = ["default"]
        discovered_reasoning_modes: list[str] = ["default"]
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
                    ["default"],
                )
            parsed_reasoning = _extract_reasoning_candidates_from_output(output_text, resolved_type)
            if parsed_reasoning:
                discovered_reasoning_modes = _normalize_reasoning_mode_options_for_agent(
                    resolved_type,
                    parsed_reasoning,
                    ["default"],
                )

            if (
                _option_count_excluding_default(discovered_models) >= 1
                and _option_count_excluding_default(discovered_reasoning_modes) >= 1
            ):
                break

        if resolved_type == AGENT_TYPE_CODEX and _option_count_excluding_default(discovered_models) < 1:
            try:
                codex_doc_models = _fetch_codex_models_from_docs(AGENT_CAPABILITY_DISCOVERY_TIMEOUT_SECONDS)
            except RuntimeError as exc:
                last_error = str(exc)
                LOGGER.warning("Codex model discovery from docs failed: %s", exc)
            else:
                if codex_doc_models:
                    discovered_models = _normalize_model_options_for_agent(
                        resolved_type,
                        codex_doc_models,
                        ["default"],
                    )
                    last_error = ""
                else:
                    last_error = f"no codex models found at {AGENT_CAPABILITY_CODEX_MODELS_DOC_URL}"
                    LOGGER.warning("%s", last_error)

        if resolved_type == AGENT_TYPE_CODEX and _option_count_excluding_default(discovered_reasoning_modes) < 1:
            reasoning_cmd = [str(token) for token in AGENT_CAPABILITY_CODEX_REASONING_FALLBACK_COMMAND]
            return_code, output_text = _run_agent_capability_probe(reasoning_cmd, AGENT_CAPABILITY_DISCOVERY_TIMEOUT_SECONDS)
            parsed_reasoning = _extract_reasoning_candidates_from_output(output_text, resolved_type)
            if parsed_reasoning:
                discovered_reasoning_modes = _normalize_reasoning_mode_options_for_agent(
                    resolved_type,
                    parsed_reasoning,
                    ["default"],
                )
                if return_code not in {127, 124} and (
                    _option_count_excluding_default(discovered_models) >= 1 or not last_error
                ):
                    last_error = ""
            elif return_code == 127:
                last_error = f"command not found: {reasoning_cmd[0]}"
            elif return_code == 124:
                last_error = f"timeout running command: {' '.join(reasoning_cmd)}"
            elif return_code != 0 and not last_error:
                last_error = (
                    "failed to parse codex reasoning levels from command output: "
                    f"{' '.join(reasoning_cmd)}"
                )

        if resolved_type == AGENT_TYPE_GEMINI and _option_count_excluding_default(discovered_models) < 1:
            discovered_models = _normalize_model_options_for_agent(
                resolved_type,
                list(AGENT_CAPABILITY_GEMINI_FALLBACK_MODELS),
                ["default"],
            )

        if not discovered_models:
            discovered_models = ["default"]
        if not discovered_reasoning_modes:
            discovered_reasoning_modes = _normalize_reasoning_mode_options_for_agent(
                resolved_type,
                [],
                ["default"],
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

    def _transition_chat_status(
        self,
        chat_id: str,
        chat: dict[str, Any],
        next_status: str,
        reason: str,
    ) -> bool:
        previous_status = _normalize_chat_status(chat.get("status"))
        resolved_next_status = _normalize_chat_status(next_status)
        transition_reason = _compact_whitespace(str(reason or "")).strip() or "unspecified"
        changed = previous_status != resolved_next_status
        transitioned_at = _iso_now()
        chat["status"] = resolved_next_status
        chat["status_reason"] = transition_reason
        chat["last_status_transition_at"] = transitioned_at
        chat["updated_at"] = transitioned_at
        if changed:
            LOGGER.info(
                "Chat state transition chat_id=%s from=%s to=%s reason=%s",
                chat_id,
                previous_status,
                resolved_next_status,
                transition_reason,
            )
        return changed

    @staticmethod
    def _chat_start_error_detail(exc: Exception) -> str:
        detail: Any = ""
        if isinstance(exc, HTTPException):
            detail = exc.detail
        elif isinstance(exc, click.ClickException):
            detail = exc.message
        else:
            detail = str(exc)
        if isinstance(detail, (dict, list)):
            try:
                detail = json.dumps(detail, sort_keys=True)
            except (TypeError, ValueError):
                detail = str(detail)
        message = _compact_whitespace(str(detail or "")).strip()
        if message:
            return message
        return exc.__class__.__name__

    def _mark_chat_start_failed(self, chat_id: str, *, detail: str, reason: str) -> dict[str, Any] | None:
        state = self.load()
        chat = state["chats"].get(chat_id)
        if chat is None:
            return None
        self._transition_chat_status(chat_id, chat, CHAT_STATUS_FAILED, reason)
        chat["pid"] = None
        chat["start_error"] = _compact_whitespace(str(detail or "")).strip()
        chat["artifact_publish_token_hash"] = ""
        chat["artifact_publish_token_issued_at"] = ""
        chat["agent_tools_token_hash"] = ""
        chat["agent_tools_token_issued_at"] = ""
        chat["last_exit_code"] = None
        chat["last_exit_at"] = _iso_now()
        chat["stop_requested_at"] = ""
        state["chats"][chat_id] = chat
        self.save(state, reason=reason)
        return dict(chat)

    def _record_chat_runtime_exit(self, chat_id: str, exit_code: int | None, *, reason: str) -> None:
        state = self.load()
        chat = state["chats"].get(chat_id)
        if chat is None:
            LOGGER.info(
                "Chat runtime exit ignored because chat is missing chat_id=%s reason=%s exit_code=%s",
                chat_id,
                reason,
                exit_code,
            )
            return
        normalized_reason = _compact_whitespace(str(reason or "")).strip() or "chat_runtime_exited"
        normalized_status = _normalize_chat_status(chat.get("status"))
        stop_requested = bool(str(chat.get("stop_requested_at") or "").strip())
        if stop_requested:
            requested_reason = _compact_whitespace(str(chat.get("status_reason") or "")).strip()
            if requested_reason in {CHAT_STATUS_REASON_CHAT_CLOSE_REQUESTED, CHAT_STATUS_REASON_USER_CLOSED_TAB}:
                stop_reason = requested_reason
            else:
                stop_reason = f"{normalized_reason}:stop_requested"
            self._transition_chat_status(chat_id, chat, CHAT_STATUS_STOPPED, stop_reason)
            chat["start_error"] = ""
            chat["stop_requested_at"] = ""
        elif normalized_status in {CHAT_STATUS_RUNNING, CHAT_STATUS_STARTING}:
            self._transition_chat_status(chat_id, chat, CHAT_STATUS_FAILED, f"{normalized_reason}:unexpected_exit")
            if not str(chat.get("start_error") or "").strip():
                chat["start_error"] = "Chat process exited unexpectedly."
        else:
            LOGGER.info(
                "Chat runtime exit observed without status transition chat_id=%s status=%s reason=%s exit_code=%s",
                chat_id,
                normalized_status,
                normalized_reason,
                exit_code,
            )
            chat["status"] = normalized_status
            if not str(chat.get("status_reason") or "").strip():
                chat["status_reason"] = normalized_reason
        chat["pid"] = None
        chat["artifact_publish_token_hash"] = ""
        chat["artifact_publish_token_issued_at"] = ""
        chat["agent_tools_token_hash"] = ""
        chat["agent_tools_token_issued_at"] = ""
        chat["last_exit_code"] = _normalize_optional_int(exit_code)
        chat["last_exit_at"] = _iso_now()
        chat["updated_at"] = _iso_now()
        state["chats"][chat_id] = chat
        self.save(state, reason=normalized_reason)

    def settings_payload(self) -> dict[str, Any]:
        state = self.load()
        return _normalize_hub_settings_payload(state.get("settings"))

    def default_chat_agent_type(self) -> str:
        settings = self.settings_payload()
        return str(settings.get("default_agent_type") or DEFAULT_CHAT_AGENT_TYPE)

    def update_settings(self, update: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(update, dict):
            raise HTTPException(status_code=400, detail="Invalid settings payload.")
        has_default_agent_type = "default_agent_type" in update or "defaultAgentType" in update
        has_chat_layout_engine = "chat_layout_engine" in update or "chatLayoutEngine" in update
        if not has_default_agent_type and not has_chat_layout_engine:
            raise HTTPException(status_code=400, detail="No settings values provided.")
        state = self.load()
        settings = _normalize_hub_settings_payload(state.get("settings"))
        if has_default_agent_type:
            settings["default_agent_type"] = _normalize_chat_agent_type(
                update.get("default_agent_type", update.get("defaultAgentType")),
                strict=True,
            )
        if has_chat_layout_engine:
            settings["chat_layout_engine"] = _normalize_chat_layout_engine(
                update.get("chat_layout_engine", update.get("chatLayoutEngine")),
                strict=True,
            )
        state["settings"] = settings
        self.save(state, reason="settings_updated")
        return settings

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
            (parsed_origin.scheme, parsed_origin.netloc, "/api/settings/auth/github-app/setup/callback", "", "")
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

    def _clear_materialized_git_credentials(self) -> None:
        if not self.git_credentials_dir.exists():
            return
        for path in self.git_credentials_dir.iterdir():
            if not path.is_file():
                continue
            try:
                path.unlink()
            except OSError as exc:
                raise HTTPException(status_code=500, detail="Failed to clear materialized git credentials.") from exc

    def _clear_github_installation_state(self, remove_credentials: bool = True) -> None:
        paths = [self.github_app_installation_file]
        for path in paths:
            if not path.exists():
                continue
            try:
                path.unlink()
            except OSError as exc:
                raise HTTPException(status_code=500, detail="Failed to clear previous GitHub installation state.") from exc
        if remove_credentials:
            self._clear_materialized_git_credentials()
        with self._github_token_lock:
            self._github_token_cache = {}

    def _clear_personal_access_token_state(self, provider: str, remove_credentials: bool = True) -> None:
        token_file = self._token_store_file_for_provider(provider)
        if token_file.exists():
            try:
                token_file.unlink()
            except OSError as exc:
                raise HTTPException(status_code=500, detail="Failed to clear stored personal access token credentials.") from exc
        if remove_credentials:
            self._clear_materialized_git_credentials()

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

    def _token_store_file_for_provider(self, provider: str) -> Path:
        normalized_provider = str(provider or "").strip().lower()
        if normalized_provider == GIT_PROVIDER_GITLAB:
            return self.gitlab_tokens_file
        return self.github_tokens_file

    def _normalize_personal_access_token_record(
        self,
        raw_record: dict[str, Any],
        *,
        default_host: str,
        default_provider: str,
        record_index: int,
    ) -> dict[str, Any] | None:
        token = str(raw_record.get("personal_access_token") or "").strip()
        account_login = str(raw_record.get("account_login") or "").strip()
        if not token or not account_login:
            return None

        provider = str(raw_record.get("provider") or default_provider).strip().lower()
        if provider not in {GIT_PROVIDER_GITHUB, GIT_PROVIDER_GITLAB}:
            provider = default_provider

        host_value = raw_record.get("host") or default_host
        default_scheme = (
            str(raw_record.get("scheme") or GIT_CREDENTIAL_DEFAULT_SCHEME).strip()
            or GIT_CREDENTIAL_DEFAULT_SCHEME
        )
        try:
            scheme, host = _normalize_github_credential_endpoint(
                host_value,
                field_name="host",
                default_scheme=default_scheme,
            )
        except HTTPException:
            return None

        account_name = str(raw_record.get("account_name") or account_login).strip() or account_login
        account_email = str(raw_record.get("account_email") or "").strip()
        host_name, _port = _split_host_port(host)
        if not account_email:
            if provider == GIT_PROVIDER_GITLAB:
                account_email = f"{account_login}@users.noreply.{host_name or 'gitlab.com'}"
            else:
                account_email = f"{account_login}@users.noreply.github.com"

        git_user_name = str(raw_record.get("git_user_name") or account_name).strip() or account_name
        git_user_email = str(raw_record.get("git_user_email") or account_email).strip() or account_email
        account_id = str(raw_record.get("account_id") or "").strip()
        token_scopes = str(raw_record.get("token_scopes") or "").strip()
        verified_at = str(raw_record.get("verified_at") or "").strip()
        connected_at = str(raw_record.get("connected_at") or "").strip()

        token_id = str(raw_record.get("token_id") or raw_record.get("id") or "").strip()
        if token_id:
            token_id = token_id[:GITHUB_PERSONAL_ACCESS_TOKEN_ID_MAX_CHARS]
        if not token_id:
            token_seed = f"{provider}|{host}|{account_login.lower()}|{record_index}"
            token_id = hashlib.sha256(token_seed.encode("utf-8")).hexdigest()[:32]

        return {
            "token_id": token_id,
            "provider": provider,
            "host": host,
            "scheme": scheme,
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
        }

    def _connected_personal_access_tokens(self, provider: str = "") -> list[dict[str, Any]]:
        providers: list[str]
        normalized_provider = str(provider or "").strip().lower()
        if normalized_provider in {GIT_PROVIDER_GITHUB, GIT_PROVIDER_GITLAB}:
            providers = [normalized_provider]
        else:
            providers = [GIT_PROVIDER_GITHUB, GIT_PROVIDER_GITLAB]

        records: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        for provider_name in providers:
            token_file = self._token_store_file_for_provider(provider_name)
            payload = _read_json_if_exists(token_file)
            if payload is None:
                continue
            raw_records: list[dict[str, Any]] = []
            if isinstance(payload.get("tokens"), list):
                raw_records = [item for item in payload["tokens"] if isinstance(item, dict)]
            elif isinstance(payload, dict):
                raw_records = [payload]
            default_host = self._github_provider_host() if provider_name == GIT_PROVIDER_GITHUB else "gitlab.com"
            for index, raw_record in enumerate(raw_records):
                normalized = self._normalize_personal_access_token_record(
                    raw_record,
                    default_host=default_host,
                    default_provider=provider_name,
                    record_index=index,
                )
                if normalized is None:
                    continue
                token_id = str(normalized.get("token_id") or "").strip()
                if token_id in seen_ids:
                    token_id = hashlib.sha256(f"{token_id}|{provider_name}|{index}".encode("utf-8")).hexdigest()[:32]
                    normalized["token_id"] = token_id
                seen_ids.add(token_id)
                records.append(normalized)
        return records

    def _persist_personal_access_tokens(self, records: list[dict[str, Any]], provider: str) -> None:
        normalized_provider = (
            GIT_PROVIDER_GITLAB if str(provider or "").strip().lower() == GIT_PROVIDER_GITLAB else GIT_PROVIDER_GITHUB
        )
        token_file = self._token_store_file_for_provider(normalized_provider)
        provider_records = [
            record
            for record in records
            if str(record.get("provider") or "").strip().lower() == normalized_provider
        ]
        if not provider_records:
            if token_file.exists():
                try:
                    token_file.unlink()
                except OSError as exc:
                    raise HTTPException(status_code=500, detail="Failed to clear stored personal access token credentials.") from exc
            return

        payload_records: list[dict[str, Any]] = []
        for record in provider_records:
            payload_records.append(
                {
                    "token_id": str(record.get("token_id") or "").strip(),
                    "provider": normalized_provider,
                    "host": str(record.get("host") or "").strip(),
                    "scheme": _normalize_github_credential_scheme(record.get("scheme"), field_name="scheme"),
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
                }
            )
        payload = {"tokens": payload_records, "updated_at": _iso_now()}
        _write_private_env_file(token_file, json.dumps(payload, indent=2) + "\n")

    @staticmethod
    def _connected_at_sort_key(record: dict[str, Any]) -> tuple[str, str]:
        connected_at = str(record.get("connected_at") or "").strip()
        token_id = str(record.get("token_id") or "").strip()
        return connected_at, token_id

    def _personal_access_tokens_for_repo(
        self,
        repo_url: str,
        credential_binding: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        repo_host = _git_repo_host(repo_url)
        if not repo_host:
            return []
        repo_scheme = _git_repo_scheme(repo_url)
        matching_host = [
            token
            for token in self._connected_personal_access_tokens()
            if str(token.get("host") or "").strip().lower() == repo_host
        ]
        if not matching_host:
            return []
        if repo_scheme in GIT_CREDENTIAL_ALLOWED_SCHEMES:
            matching_scheme = [
                token
                for token in matching_host
                if str(token.get("scheme") or GIT_CREDENTIAL_DEFAULT_SCHEME).strip().lower() == repo_scheme
            ]
            if matching_scheme:
                matching_host = matching_scheme

        tokens: list[dict[str, Any]] = []
        seen_token_ids: set[str] = set()

        normalized_binding = _normalize_project_credential_binding(credential_binding)
        if normalized_binding["mode"] in {
            PROJECT_CREDENTIAL_BINDING_MODE_SET,
            PROJECT_CREDENTIAL_BINDING_MODE_SINGLE,
        }:
            preferred_ids = normalized_binding["credential_ids"]
            if preferred_ids:
                by_id = {str(token.get("token_id") or "").strip(): token for token in matching_host}
                for token_id in preferred_ids:
                    if token_id in by_id and token_id not in seen_token_ids:
                        tokens.append(by_id[token_id])
                        seen_token_ids.add(token_id)
                if normalized_binding["mode"] == PROJECT_CREDENTIAL_BINDING_MODE_SINGLE and tokens:
                    return tokens

        # Add remaining matches, ordered by most recent first
        ordered_matches = list(enumerate(matching_host))
        ordered_matches.sort(
            key=lambda item: (str(item[1].get("connected_at") or "").strip(), -item[0]),
            reverse=True,
        )
        for _, token in ordered_matches:
            token_id = str(token.get("token_id") or "").strip()
            if token_id not in seen_token_ids:
                tokens.append(token)
                seen_token_ids.add(token_id)

        return tokens

    def _personal_access_token_for_repo(
        self,
        repo_url: str,
        credential_binding: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        tokens = self._personal_access_tokens_for_repo(repo_url, credential_binding=credential_binding)
        return tokens[0] if tokens else None

    def _github_personal_access_token_for_repo(
        self,
        repo_url: str,
        credential_binding: dict[str, Any] | None = None,
    ) -> str:
        token_record = self._personal_access_token_for_repo(repo_url, credential_binding=credential_binding)
        if (
            token_record is None
            or str(token_record.get("provider") or "").strip().lower() != GIT_PROVIDER_GITHUB
        ):
            return ""
        return str(token_record.get("personal_access_token") or "").strip()

    def _github_connected_personal_access_tokens(self) -> list[dict[str, Any]]:
        return self._connected_personal_access_tokens(GIT_PROVIDER_GITHUB)

    def _gitlab_connected_personal_access_tokens(self) -> list[dict[str, Any]]:
        return self._connected_personal_access_tokens(GIT_PROVIDER_GITLAB)

    def _github_api_base_url_for_host(self, host: str, scheme: str = GIT_CREDENTIAL_DEFAULT_SCHEME) -> str:
        normalized_scheme = _normalize_github_credential_scheme(scheme, field_name="scheme")
        normalized_host = _normalize_github_credential_host(host, field_name="host")
        if (
            normalized_scheme == "https"
            and self.github_app_settings is not None
            and self._github_provider_host() == normalized_host
        ):
            return self.github_app_settings.api_base_url
        if normalized_scheme == "https" and normalized_host == "github.com":
            return GITHUB_APP_DEFAULT_API_BASE_URL
        if normalized_scheme != "https":
            return f"{normalized_scheme}://{normalized_host}/api/v3"
        return f"https://{normalized_host}/api/v3"

    @staticmethod
    def _gitlab_api_base_url_for_host(host: str, scheme: str = GIT_CREDENTIAL_DEFAULT_SCHEME) -> str:
        normalized_scheme = _normalize_github_credential_scheme(scheme, field_name="scheme")
        normalized_host = _normalize_github_credential_host(host, field_name="host")
        return f"{normalized_scheme}://{normalized_host}/api/v4"

    @staticmethod
    def _pat_verification_request(
        request: urllib.request.Request,
        provider_label: str,
    ) -> tuple[int, str, dict[str, str]]:
        try:
            with urllib.request.urlopen(request, timeout=GITHUB_APP_API_TIMEOUT_SECONDS) as response:
                status = int(response.getcode() or 0)
                payload_text = response.read().decode("utf-8", errors="ignore")
                response_headers = {str(key): str(value) for key, value in response.headers.items()}
                return status, payload_text, response_headers
        except urllib.error.HTTPError as exc:
            status = int(exc.code or 0)
            payload_text = exc.read().decode("utf-8", errors="ignore")
            response_headers = {str(key): str(value) for key, value in (exc.headers.items() if exc.headers else [])}
            return status, payload_text, response_headers
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise HTTPException(
                status_code=502,
                detail=f"{provider_label} personal access token verification failed due to a network error.",
            ) from exc

    @staticmethod
    def _header_value(headers: dict[str, str], *keys: str) -> str:
        if not headers:
            return ""
        for key in keys:
            for header_name, value in headers.items():
                if header_name.lower() == key.lower():
                    return str(value or "").strip()
        return ""

    @staticmethod
    def _token_scope_set(raw_scopes: Any) -> set[str]:
        if raw_scopes is None:
            return set()
        text = str(raw_scopes).strip().lower()
        if not text:
            return set()
        return {token.strip() for token in re.split(r"[\s,]+", text) if token.strip()}

    @classmethod
    def _validate_gitlab_personal_access_token_scopes(cls, token_scopes: Any) -> None:
        scope_set = cls._token_scope_set(token_scopes)
        if not scope_set:
            return
        if "api" in scope_set:
            return
        missing_scopes = sorted(GITLAB_PERSONAL_ACCESS_TOKEN_REQUIRED_SCOPES.difference(scope_set))
        if not missing_scopes:
            return
        missing_text = ", ".join(missing_scopes)
        raise HTTPException(
            status_code=400,
            detail=(
                "GitLab personal access token is missing required scopes: "
                f"{missing_text}. Provide `api` or both `read_repository` and `write_repository`."
            ),
        )

    def _verify_github_personal_access_token(self, token: str, host: str, scheme: str = GIT_CREDENTIAL_DEFAULT_SCHEME) -> dict[str, str]:
        normalized_host = _normalize_github_credential_host(host, field_name="host")
        normalized_scheme = _normalize_github_credential_scheme(scheme, field_name="scheme")
        host_name, _port = _split_host_port(normalized_host)
        preferred_provider = GIT_PROVIDER_GITLAB if "gitlab" in host_name else GIT_PROVIDER_GITHUB
        providers = (
            [GIT_PROVIDER_GITLAB, GIT_PROVIDER_GITHUB]
            if preferred_provider == GIT_PROVIDER_GITLAB
            else [GIT_PROVIDER_GITHUB, GIT_PROVIDER_GITLAB]
        )

        failures: list[tuple[str, int, str]] = []
        for provider in providers:
            if provider == GIT_PROVIDER_GITHUB:
                api_base_url = self._github_api_base_url_for_host(normalized_host, normalized_scheme)
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
                provider_label = "GitHub"
            else:
                api_base_url = self._gitlab_api_base_url_for_host(normalized_host, normalized_scheme)
                request = urllib.request.Request(
                    f"{api_base_url}/user",
                    headers={
                        "Accept": "application/json",
                        "User-Agent": "agent-hub",
                        "Authorization": f"Bearer {token}",
                        "PRIVATE-TOKEN": token,
                    },
                    method="GET",
                )
                provider_label = "GitLab"

            status, payload_text, response_headers = self._pat_verification_request(request, provider_label)
            if 200 <= status < 300:
                try:
                    payload = json.loads(payload_text) if payload_text else {}
                except json.JSONDecodeError as exc:
                    failures.append((provider_label, 502, "returned invalid PAT verification payload."))
                    continue
                if not isinstance(payload, dict):
                    failures.append((provider_label, 502, "returned invalid PAT verification payload."))
                    continue

                if provider == GIT_PROVIDER_GITHUB:
                    account_login = str(payload.get("login") or "").strip()
                    account_name = str(payload.get("name") or "").strip()
                    raw_account_id = payload.get("id")
                    account_email = str(payload.get("email") or "").strip()
                    token_scopes = self._header_value(response_headers, "X-OAuth-Scopes")
                    if not account_login:
                        failures.append(("GitHub", 502, "did not return a user login for this token."))
                        continue
                    account_id = 0
                    if isinstance(raw_account_id, int) and raw_account_id > 0:
                        account_id = raw_account_id
                    elif isinstance(raw_account_id, str) and raw_account_id.isdigit():
                        account_id = int(raw_account_id)
                    if not account_email:
                        if account_id > 0:
                            account_email = f"{account_id}+{account_login}@users.noreply.github.com"
                        else:
                            account_email = f"{account_login}@users.noreply.github.com"
                    return {
                        "provider": GIT_PROVIDER_GITHUB,
                        "account_login": account_login,
                        "account_name": account_name or account_login,
                        "account_email": account_email,
                        "account_id": str(account_id) if account_id > 0 else "",
                        "token_scopes": token_scopes,
                    }

                account_login = str(payload.get("username") or payload.get("login") or "").strip()
                account_name = str(payload.get("name") or "").strip()
                account_email = str(payload.get("email") or "").strip()
                raw_account_id = payload.get("id")
                if not account_login:
                    failures.append(("GitLab", 502, "did not return a user login for this token."))
                    continue
                account_id = 0
                if isinstance(raw_account_id, int) and raw_account_id > 0:
                    account_id = raw_account_id
                elif isinstance(raw_account_id, str) and raw_account_id.isdigit():
                    account_id = int(raw_account_id)
                if not account_email:
                    account_email = f"{account_login}@users.noreply.{host_name or 'gitlab.com'}"
                token_scopes = self._header_value(
                    response_headers,
                    "X-Gitlab-Scopes",
                    "X-GitLab-Scopes",
                    "X-OAuth-Scopes",
                    "X-Oauth-Scopes",
                )
                self._validate_gitlab_personal_access_token_scopes(token_scopes)
                return {
                    "provider": GIT_PROVIDER_GITLAB,
                    "account_login": account_login,
                    "account_name": account_name or account_login,
                    "account_email": account_email,
                    "account_id": str(account_id) if account_id > 0 else "",
                    "token_scopes": token_scopes,
                }

            message = _github_api_error_message(payload_text)
            failures.append((provider_label, status, message))

        unauthorized_failures = [failure for failure in failures if failure[1] in {401, 403}]
        if unauthorized_failures:
            provider_label, status, message = unauthorized_failures[0]
            detail = f"{provider_label} personal access token verification failed with status {status}."
            if message:
                detail = f"{detail} {message}"
            raise HTTPException(status_code=400, detail=detail)

        if failures:
            provider_label, status, message = failures[0]
            detail = f"{provider_label} personal access token verification failed with status {status}."
            if message:
                detail = f"{detail} {message}"
            raise HTTPException(status_code=502, detail=detail)

        raise HTTPException(status_code=502, detail="Git provider personal access token verification failed.")

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

    def _materialized_credential_file_path(self, context_key: str, credential_id: str) -> Path:
        context = str(context_key or "").strip() or "default"
        token = str(credential_id or "").strip() or "credential"
        digest = hashlib.sha256(f"{context}|{token}".encode("utf-8")).hexdigest()[:24]
        return self.git_credentials_dir / f"{digest}.git-credentials"

    def _refresh_github_git_credentials(self, installation_id: int, host: str, context_key: str = "") -> str:
        token, _expires_at = self._github_installation_token(installation_id)
        return self._write_github_git_credentials(
            host=host,
            username="x-access-token",
            secret=token,
            scheme=GIT_CREDENTIAL_DEFAULT_SCHEME,
            credential_id=f"github_app:{installation_id}",
            context_key=context_key,
        )

    def _write_github_git_credentials(
        self,
        host: str,
        username: str,
        secret: str,
        scheme: str = GIT_CREDENTIAL_DEFAULT_SCHEME,
        credential_id: str = "",
        context_key: str = "",
    ) -> str:
        normalized_scheme = _normalize_github_credential_scheme(scheme, field_name="scheme")
        normalized_host = _normalize_github_credential_host(host, field_name="host")
        resolved_username = str(username or "").strip()
        resolved_secret = str(secret or "").strip()
        if not resolved_username:
            raise HTTPException(status_code=500, detail="Missing GitHub credential username.")
        if not resolved_secret:
            raise HTTPException(status_code=500, detail="Missing GitHub credential secret.")
        encoded_username = urllib.parse.quote(resolved_username, safe="")
        encoded_secret = urllib.parse.quote(resolved_secret, safe="")
        resolved_credential_id = str(credential_id or "").strip() or f"{normalized_host}:{resolved_username}"
        output_file = self._materialized_credential_file_path(context_key, resolved_credential_id)
        _write_private_env_file(
            output_file,
            f"{normalized_scheme}://{encoded_username}:{encoded_secret}@{normalized_host}\n",
        )
        return str(output_file)

    def _refresh_github_git_credentials_for_personal_access_token(
        self,
        token: str,
        host: str,
        account_login: str,
        scheme: str = GIT_CREDENTIAL_DEFAULT_SCHEME,
        context_key: str = "",
        credential_id: str = "",
    ) -> str:
        return self._write_github_git_credentials(
            host=host,
            username=account_login,
            secret=token,
            scheme=scheme,
            credential_id=credential_id,
            context_key=context_key,
        )

    @staticmethod
    def _git_env_for_credentials_file(
        credential_file: str,
        host: str,
        scheme: str = GIT_CREDENTIAL_DEFAULT_SCHEME,
    ) -> dict[str, str]:
        normalized_scheme = _normalize_github_credential_scheme(scheme, field_name="scheme")
        normalized_host = str(host or "github.com").strip().lower()
        host_name, _port = _split_host_port(normalized_host)
        normalized_ssh_host = host_name or normalized_host
        git_prefix = f"{normalized_scheme}://{normalized_host}/"
        
        # Ensure we use an absolute path for the credential file
        abs_cred_file = str(Path(credential_file).resolve())
        
        return {
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_CONFIG_COUNT": "3",
            "GIT_CONFIG_KEY_0": "credential.helper",
            "GIT_CONFIG_VALUE_0": f"store --file={abs_cred_file}",
            "GIT_CONFIG_KEY_1": f"url.{git_prefix}.insteadOf",
            "GIT_CONFIG_VALUE_1": f"git@{normalized_ssh_host}:",
            "GIT_CONFIG_KEY_2": f"url.{git_prefix}.insteadOf",
            "GIT_CONFIG_VALUE_2": f"ssh://git@{normalized_ssh_host}/",
        }

    def _github_repo_all_auth_contexts(
        self,
        repo_url: str,
        project: dict[str, Any] | None = None,
    ) -> list[tuple[str, str, dict[str, Any]]]:
        repo_host = _git_repo_host(repo_url)
        if not repo_host:
            return []

        credential_binding = None
        if isinstance(project, dict):
            credential_binding = _normalize_project_credential_binding(project.get("credential_binding"))

        contexts: list[tuple[str, str, dict[str, Any]]] = []
        personal_access_tokens = self._personal_access_tokens_for_repo(repo_url, credential_binding=credential_binding)
        for token in personal_access_tokens:
            pat_host = str(token.get("host") or "")
            if pat_host and repo_host == pat_host:
                payload = dict(token)
                payload["credential_id"] = str(payload.get("token_id") or "").strip()
                contexts.append((GIT_CONNECTION_MODE_PERSONAL_ACCESS_TOKEN, pat_host, payload))

        installation = self._github_connected_installation()
        provider_host = self._github_provider_host()
        if installation is not None and repo_host == provider_host:
            installation_id = int(installation.get("installation_id") or 0)
            if installation_id > 0:
                app_credential_id = f"github_app:{installation_id}"
                normalized_binding = _normalize_project_credential_binding(credential_binding)
                is_allowed = True
                if normalized_binding["mode"] in {
                    PROJECT_CREDENTIAL_BINDING_MODE_SET,
                    PROJECT_CREDENTIAL_BINDING_MODE_SINGLE,
                }:
                    preferred_ids = normalized_binding["credential_ids"]
                    if preferred_ids and app_credential_id not in preferred_ids:
                        is_allowed = False

                if is_allowed:
                    contexts.append(
                        (
                            GITHUB_CONNECTION_MODE_GITHUB_APP,
                            provider_host,
                            {
                                "installation_id": installation_id,
                                "credential_id": app_credential_id,
                                "provider": GIT_PROVIDER_GITHUB,
                                "account_login": str(installation.get("installation_account_login") or ""),
                            },
                        )
                    )
        return contexts

    def _auto_discover_project_credential_binding(
        self,
        repo_url: str,
        credential_binding: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        normalized_binding = _normalize_project_credential_binding(credential_binding)
        if (
            normalized_binding["mode"] != PROJECT_CREDENTIAL_BINDING_MODE_AUTO
            or normalized_binding["credential_ids"]
        ):
            return normalized_binding

        normalized_repo_url = str(repo_url or "").strip()
        if not normalized_repo_url:
            return normalized_binding

        discovered_ids = self._resolve_agent_tools_credential_ids(
            {"repo_url": normalized_repo_url, "credential_binding": normalized_binding},
            PROJECT_CREDENTIAL_BINDING_MODE_AUTO,
            [],
        )
        if not discovered_ids:
            return normalized_binding

        # Verify each discovered credential can actually access this specific repo,
        # not just the host. Tokens may be scoped to certain repositories.
        discovered_id_set = set(discovered_ids)
        stub_project: dict[str, Any] = {"repo_url": normalized_repo_url, "credential_binding": normalized_binding}
        all_contexts = self._github_repo_all_auth_contexts(normalized_repo_url, project=stub_project)
        candidate_contexts = [
            ctx for ctx in all_contexts
            if str(ctx[2].get("credential_id") or "").strip() in discovered_id_set
        ]
        if candidate_contexts:
            verified_contexts = self._verify_repo_access_for_contexts(
                normalized_repo_url,
                candidate_contexts,
                context_key="auto-discover",
            )
            if verified_contexts:
                verified_id_set = set(
                    str(ctx[2].get("credential_id") or "").strip()
                    for ctx in verified_contexts
                )
                # Preserve original ordering, keep only verified
                discovered_ids = [cid for cid in discovered_ids if cid in verified_id_set]

        if not discovered_ids:
            return normalized_binding

        return _normalize_project_credential_binding(
            {
                "mode": PROJECT_CREDENTIAL_BINDING_MODE_SET,
                "credential_ids": discovered_ids,
                "source": normalized_binding["source"] or "auto_create",
                "updated_at": _iso_now(),
            }
        )

    def _github_repo_auth_context(
        self,
        repo_url: str,
        project: dict[str, Any] | None = None,
    ) -> tuple[str, str, dict[str, Any]] | None:
        contexts = self._github_repo_all_auth_contexts(repo_url, project=project)
        return contexts[0] if contexts else None

    def _refresh_all_github_git_credentials(
        self,
        contexts: list[tuple[str, str, dict[str, Any]]],
        *,
        context_key: str = "",
    ) -> str:
        lines: list[str] = []
        seen_lines: set[str] = set()
        for mode, host, auth_payload in contexts:
            scheme = GIT_CREDENTIAL_DEFAULT_SCHEME
            if mode == GITHUB_CONNECTION_MODE_GITHUB_APP:
                installation_id = int(auth_payload.get("installation_id") or 0)
                if installation_id <= 0:
                    continue
                token, _expires_at = self._github_installation_token(installation_id)
                username = "x-access-token"
            elif mode == GIT_CONNECTION_MODE_PERSONAL_ACCESS_TOKEN:
                token = str(auth_payload.get("personal_access_token") or "").strip()
                username = str(auth_payload.get("account_login") or "").strip()
                try:
                    scheme = _normalize_github_credential_scheme(
                        auth_payload.get("scheme"),
                        field_name="scheme",
                    )
                except HTTPException:
                    scheme = GIT_CREDENTIAL_DEFAULT_SCHEME
            else:
                continue

            if not token or not username:
                continue

            encoded_username = urllib.parse.quote(username, safe="")
            encoded_secret = urllib.parse.quote(token, safe="")
            line = f"{scheme}://{encoded_username}:{encoded_secret}@{host}\n"
            if line not in seen_lines:
                lines.append(line)
                seen_lines.add(line)

        if not lines:
            return ""

        output_file = self._materialized_credential_file_path(context_key, "merged")
        _write_private_env_file(output_file, "".join(lines))
        return str(output_file)

    @staticmethod
    def _git_scheme_for_auth_context(
        mode: str,
        auth_payload: dict[str, Any],
    ) -> str:
        if mode != GIT_CONNECTION_MODE_PERSONAL_ACCESS_TOKEN:
            return GIT_CREDENTIAL_DEFAULT_SCHEME
        try:
            return _normalize_github_credential_scheme(
                auth_payload.get("scheme"),
                field_name="scheme",
            )
        except HTTPException:
            return GIT_CREDENTIAL_DEFAULT_SCHEME

    def _verify_repo_access_for_contexts(
        self,
        repo_url: str,
        contexts: list[tuple[str, str, dict[str, Any]]],
        *,
        context_key: str = "",
    ) -> list[tuple[str, str, dict[str, Any]]]:
        """Probe each credential context with ``git ls-remote`` and return only those that can access *repo_url*."""
        normalized_repo_url = str(repo_url or "").strip()
        if not normalized_repo_url or not contexts:
            return []

        normalized_context_key = str(context_key or "").strip()
        if normalized_context_key:
            probe_prefix = f"{normalized_context_key}:probe"
        else:
            repo_digest = hashlib.sha256(normalized_repo_url.encode("utf-8")).hexdigest()[:12]
            probe_prefix = f"repo-auth-probe:{repo_digest}"

        verified: list[tuple[str, str, dict[str, Any]]] = []
        for index, context in enumerate(contexts):
            mode, host, auth_payload = context
            credential_id = str(auth_payload.get("credential_id") or "").strip() or f"{mode}:{host}:{index}"
            probe_context_key = f"{probe_prefix}:{credential_id}:{index}"

            try:
                credentials_file = self._refresh_all_github_git_credentials(
                    [context],
                    context_key=probe_context_key,
                )
            except HTTPException:
                continue
            if not credentials_file:
                continue

            probe_env = self._git_env_for_credentials_file(
                credentials_file,
                host,
                scheme=self._git_scheme_for_auth_context(mode, auth_payload),
            )
            probe_result = _run(
                ["git", "ls-remote", "--exit-code", normalized_repo_url, "HEAD"],
                capture=True,
                check=False,
                env=probe_env,
            )
            if probe_result.returncode == 0:
                verified.append(context)

        return verified

    def _ordered_repo_auth_contexts_for_git(
        self,
        repo_url: str,
        contexts: list[tuple[str, str, dict[str, Any]]],
        *,
        context_key: str = "",
    ) -> list[tuple[str, str, dict[str, Any]]]:
        if len(contexts) <= 1:
            return contexts

        verified = self._verify_repo_access_for_contexts(
            repo_url, contexts, context_key=context_key,
        )
        if not verified:
            return contexts

        verified_set = set(id(ctx) for ctx in verified)
        unverified = [ctx for ctx in contexts if id(ctx) not in verified_set]
        return [*verified, *unverified]

    def _github_git_env_for_repo(
        self,
        repo_url: str,
        project: dict[str, Any] | None = None,
        *,
        context_key: str = "",
    ) -> dict[str, str]:
        ordered_contexts = self._ordered_repo_auth_contexts_for_git(
            repo_url,
            self._github_repo_all_auth_contexts(repo_url, project=project),
            context_key=context_key,
        )
        if not ordered_contexts:
            return {}
        credentials_file = self._refresh_all_github_git_credentials(
            ordered_contexts,
            context_key=context_key,
        )
        if not credentials_file:
            return {}
        mode, host, auth_payload = ordered_contexts[0]
        return self._git_env_for_credentials_file(
            credentials_file,
            host,
            scheme=self._git_scheme_for_auth_context(mode, auth_payload),
        )

    def _github_git_args_for_repo(
        self,
        repo_url: str,
        project: dict[str, Any] | None = None,
        *,
        context_key: str = "",
    ) -> list[str]:
        ordered_contexts = self._ordered_repo_auth_contexts_for_git(
            repo_url,
            self._github_repo_all_auth_contexts(repo_url, project=project),
            context_key=context_key,
        )
        if not ordered_contexts:
            return []
        credentials_file = self._refresh_all_github_git_credentials(
            ordered_contexts,
            context_key=context_key,
        )
        if not credentials_file:
            return []
        _mode, host, _auth_payload = ordered_contexts[0]
        return [
            "--git-credential-file",
            credentials_file,
            "--git-credential-host",
            host,
        ]

    def _github_git_identity_env_vars_for_repo(
        self,
        repo_url: str,
        project: dict[str, Any] | None = None,
    ) -> list[str]:
        context = self._github_repo_auth_context(repo_url, project=project)
        if context is None:
            return []
        mode, _host, auth_payload = context
        if mode != GIT_CONNECTION_MODE_PERSONAL_ACCESS_TOKEN:
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

    def github_app_auth_status(self) -> dict[str, Any]:
        installation = self._github_connected_installation()
        app_configured = self.github_app_settings is not None and not self.github_app_settings_error
        installation_id = int(installation.get("installation_id") or 0) if installation else 0

        updated_at = ""
        if self.github_app_installation_file.exists():
            try:
                updated_at = _iso_from_timestamp(self.github_app_installation_file.stat().st_mtime)
            except OSError:
                updated_at = ""
        elif self.github_app_settings_file.exists():
            try:
                updated_at = _iso_from_timestamp(self.github_app_settings_file.stat().st_mtime)
            except OSError:
                updated_at = ""

        return {
            "provider": "github_app",
            "connected": bool(app_configured and installation_id > 0),
            "app_configured": app_configured,
            "app_slug": self.github_app_settings.app_slug if self.github_app_settings else "",
            "install_url": self._github_install_url(),
            "installation_id": installation_id,
            "installation_account_login": str(installation.get("account_login") or "") if installation else "",
            "installation_account_type": str(installation.get("account_type") or "") if installation else "",
            "repository_selection": str(installation.get("repository_selection") or "") if installation else "",
            "connection_host": self._github_provider_host(),
            "updated_at": updated_at,
            "error": str(self.github_app_settings_error or ""),
        }

    def _personal_access_tokens_status(self, provider: str) -> dict[str, Any]:
        normalized_provider = (
            GIT_PROVIDER_GITLAB if str(provider or "").strip().lower() == GIT_PROVIDER_GITLAB else GIT_PROVIDER_GITHUB
        )
        token_records = self._connected_personal_access_tokens(normalized_provider)
        entries: list[dict[str, Any]] = []
        for token_record in token_records:
            token_value = str(token_record.get("personal_access_token") or "").strip()
            entries.append(
                {
                    "token_id": str(token_record.get("token_id") or "").strip(),
                    "token_hint": _mask_secret(token_value) if token_value else "",
                    "host": str(token_record.get("host") or "").strip(),
                    "scheme": _normalize_github_credential_scheme(token_record.get("scheme"), field_name="scheme"),
                    "provider": normalized_provider,
                    "account_login": str(token_record.get("account_login") or "").strip(),
                    "account_name": str(token_record.get("account_name") or "").strip(),
                    "account_email": str(token_record.get("account_email") or "").strip(),
                    "account_id": str(token_record.get("account_id") or "").strip(),
                    "git_user_name": str(token_record.get("git_user_name") or "").strip(),
                    "git_user_email": str(token_record.get("git_user_email") or "").strip(),
                    "token_scopes": str(token_record.get("token_scopes") or "").strip(),
                    "verified_at": str(token_record.get("verified_at") or "").strip(),
                    "connected_at": str(token_record.get("connected_at") or "").strip(),
                }
            )

        token_file = self._token_store_file_for_provider(normalized_provider)
        updated_at = ""
        if token_file.exists():
            try:
                updated_at = _iso_from_timestamp(token_file.stat().st_mtime)
            except OSError:
                updated_at = ""

        provider_key = "gitlab_tokens" if normalized_provider == GIT_PROVIDER_GITLAB else "github_tokens"
        default_host = (
            "gitlab.com" if normalized_provider == GIT_PROVIDER_GITLAB else self._github_provider_host()
        )
        return {
            "provider": provider_key,
            "git_provider": normalized_provider,
            "connected": bool(entries),
            "token_count": len(entries),
            "tokens": entries,
            "default_host": default_host,
            "updated_at": updated_at,
        }

    def github_tokens_status(self) -> dict[str, Any]:
        return self._personal_access_tokens_status(GIT_PROVIDER_GITHUB)

    def gitlab_tokens_status(self) -> dict[str, Any]:
        return self._personal_access_tokens_status(GIT_PROVIDER_GITLAB)

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

    def _credential_catalog(self) -> list[dict[str, Any]]:
        credentials: list[dict[str, Any]] = []

        github_app_status = self.github_app_auth_status()
        if github_app_status.get("connected"):
            installation_id = int(github_app_status.get("installation_id") or 0)
            if installation_id > 0:
                installation_login = str(github_app_status.get("installation_account_login") or "")
                credentials.append(
                    {
                        "credential_id": f"github_app:{installation_id}",
                        "kind": "github_app_installation",
                        "provider": GIT_PROVIDER_GITHUB,
                        "host": self._github_provider_host(),
                        "scheme": GIT_CREDENTIAL_DEFAULT_SCHEME,
                        "account_login": installation_login,
                        "account_name": installation_login,
                        "connected_at": str(github_app_status.get("updated_at") or ""),
                        "summary": f"GitHub App installation #{installation_id}"
                        + (f" ({installation_login})" if installation_login else ""),
                    }
                )

        for provider in (GIT_PROVIDER_GITHUB, GIT_PROVIDER_GITLAB):
            for token in self._connected_personal_access_tokens(provider):
                token_id = str(token.get("token_id") or "").strip()
                if not token_id:
                    continue
                account_login = str(token.get("account_login") or "").strip()
                host = str(token.get("host") or "").strip()
                credentials.append(
                    {
                        "credential_id": token_id,
                        "kind": "personal_access_token",
                        "provider": provider,
                        "host": host,
                        "scheme": _normalize_github_credential_scheme(token.get("scheme"), field_name="scheme"),
                        "account_login": account_login,
                        "account_name": str(token.get("account_name") or "").strip(),
                        "connected_at": str(token.get("connected_at") or "").strip(),
                        "summary": (
                            f"{provider.capitalize()} token"
                            f"{f' ({account_login})' if account_login else ''}"
                            f"{f' on {host}' if host else ''}"
                        ),
                    }
                )

        credentials.sort(
            key=lambda entry: (
                str(entry.get("provider") or ""),
                str(entry.get("host") or ""),
                str(entry.get("account_login") or ""),
                str(entry.get("credential_id") or ""),
            )
        )
        return credentials

    def auth_settings_payload(self) -> dict[str, Any]:
        return {
            "providers": {
                "openai": self.openai_auth_status(),
                "github_app": self.github_app_auth_status(),
                "github_tokens": self.github_tokens_status(),
                "gitlab_tokens": self.gitlab_tokens_status(),
            },
            "credential_catalog": self._credential_catalog(),
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

    @staticmethod
    def _is_auto_config_container_workspace_mount(
        container_path: str,
        reserved_container_workspace: str | None = None,
    ) -> bool:
        normalized_container = HubState._normalize_auto_config_mount_path(container_path)
        if not normalized_container:
            return False
        normalized_workspace = HubState._normalize_auto_config_mount_path(reserved_container_workspace or "")
        if not normalized_workspace or normalized_workspace == "/":
            return False
        if normalized_container == normalized_workspace:
            return True
        return normalized_container.startswith(f"{normalized_workspace}/")

    def _normalize_auto_config_mounts(
        self,
        entries: list[str],
        direction: str,
        *,
        reserved_container_workspace: str | None = None,
    ) -> list[str]:
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
            if self._is_auto_config_container_workspace_mount(
                container,
                reserved_container_workspace=reserved_container_workspace,
            ):
                continue
            normalized_entries.append(f"{host_path}:{container}")
        return _parse_mounts(normalized_entries, direction)

    def _normalize_auto_config_recommendation(
        self,
        raw_payload: dict[str, Any],
        workspace: Path,
        project_container_workspace: str | None = None,
    ) -> dict[str, Any]:
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
            reserved_container_workspace=project_container_workspace,
        )
        default_rw_mounts = self._normalize_auto_config_mounts(
            _empty_list(raw_payload.get("default_rw_mounts")),
            "default read-write mount",
            reserved_container_workspace=project_container_workspace,
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

    def _prepare_agent_cli_command(
        self,
        *,
        workspace: Path,
        container_project_name: str,
        runtime_config_file: Path,
        agent_type: str,
        agent_tools_url: str,
        agent_tools_token: str,
        agent_tools_project_id: str = "",
        agent_tools_chat_id: str = "",
        repo_url: str = "",
        project: dict[str, Any] | None = None,
        snapshot_tag: str = "",
        ro_mounts: list[str] | None = None,
        rw_mounts: list[str] | None = None,
        env_vars: list[str] | None = None,
        artifacts_url: str = "",
        artifacts_token: str = "",
        resume: bool = False,
        allocate_tty: bool = True,
        context_key: str = "",
        extra_args: list[str] | None = None,
        setup_script: str = "",
        prepare_snapshot_only: bool = False,
    ) -> list[str]:
        agent_command = AGENT_COMMAND_BY_TYPE.get(agent_type, AGENT_COMMAND_BY_TYPE[DEFAULT_CHAT_AGENT_TYPE])
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
            container_project_name,
            "--agent-home-path",
            str(self.host_agent_home),
            "--config-file",
            str(runtime_config_file),
            "--system-prompt-file",
            str(self.system_prompt_file),
            "--no-alt-screen",
        ]
        if not allocate_tty:
            cmd.append("--no-tty")
        if resume and agent_type == AGENT_TYPE_CODEX:
            cmd.append("--resume")
        cmd.extend(self._openai_credentials_arg())
        cmd.extend(
            self._github_git_args_for_repo(
                repo_url,
                project=project,
                context_key=context_key,
            )
        )
        for git_identity_env in self._github_git_identity_env_vars_for_repo(repo_url, project=project):
            cmd.extend(["--env-var", git_identity_env])
        if snapshot_tag:
            self._append_project_base_args(cmd, workspace, project)
            cmd.extend(["--snapshot-image-tag", snapshot_tag])
        for mount in ro_mounts or []:
            cmd.extend(["--ro-mount", mount])
        for mount in rw_mounts or []:
            cmd.extend(["--rw-mount", mount])

        if setup_script:
            cmd.extend(["--setup-script", setup_script])
        if prepare_snapshot_only:
            cmd.append("--prepare-snapshot-only")

        if artifacts_url:
            cmd.extend(["--env-var", f"AGENT_ARTIFACTS_URL={artifacts_url}"])
        if artifacts_token:
            cmd.extend(["--env-var", f"AGENT_ARTIFACT_TOKEN={artifacts_token}"])

        cmd.extend(["--env-var", f"{AGENT_TOOLS_URL_ENV}={agent_tools_url}"])
        cmd.extend(["--env-var", f"{AGENT_TOOLS_TOKEN_ENV}={agent_tools_token}"])
        cmd.extend(["--env-var", f"{AGENT_TOOLS_PROJECT_ID_ENV}={agent_tools_project_id}"])
        cmd.extend(["--env-var", f"{AGENT_TOOLS_CHAT_ID_ENV}={agent_tools_chat_id}"])

        for env_entry in env_vars or []:
            if _is_reserved_env_entry(str(env_entry)):
                continue
            cmd.extend(["--env-var", env_entry])

        if extra_args:
            cmd.append("--")
            cmd.extend(extra_args)
        return cmd

    def _run_temporary_auto_config_chat(
        self,
        workspace: Path,
        repo_url: str,
        branch: str,
        agent_type: str = AGENT_TYPE_CODEX,
        agent_args: list[str] | None = None,
        on_output: Callable[[str], None] | None = None,
        request_id: str = "",
    ) -> dict[str, Any]:
        normalized_request_id = self._normalize_auto_config_request_id(request_id)
        resolved_agent_type = _normalize_chat_agent_type(agent_type, strict=True)
        normalized_agent_args = [str(arg) for arg in (agent_args or []) if str(arg).strip()]

        def emit(chunk: str) -> None:
            if on_output is None:
                return
            text = str(chunk or "")
            if not text:
                return
            try:
                on_output(text)
            except Exception:
                LOGGER.exception("Auto-config output callback failed.")

        if resolved_agent_type == AGENT_TYPE_CODEX:
            account_connected, _ = _read_codex_auth(self.openai_codex_auth_file)
            if not account_connected:
                raise HTTPException(status_code=409, detail=AUTO_CONFIG_NOT_CONNECTED_ERROR)

        prompt = self._auto_config_prompt(repo_url, branch)
        output_file = workspace / f".agent-hub-auto-config-{uuid.uuid4().hex}.json"
        container_project_name = _container_project_name(_extract_repo_name(repo_url) or "auto-config")
        container_workspace = str(PurePosixPath(DEFAULT_CONTAINER_HOME) / container_project_name)
        container_output_file = str(PurePosixPath(container_workspace) / output_file.name)
        session_id, session_token = self._create_agent_tools_session(repo_url=repo_url, workspace=workspace)
        agent_tools_url = f"{self.artifact_publish_base_url}/api/agent-tools/sessions/{session_id}"
        agent_tools_chat_id = f"auto-config:{session_id}"
        runtime_config_file = self._prepare_chat_runtime_config(
            f"auto-config-{session_id}",
            agent_type=resolved_agent_type,
            agent_tools_url=agent_tools_url,
            agent_tools_token=session_token,
            agent_tools_project_id="",
            agent_tools_chat_id=agent_tools_chat_id,
        )
        artifact_publish_token = _new_artifact_publish_token()
        with self._agent_tools_sessions_lock:
            active_session = self._agent_tools_sessions.get(session_id)
            if active_session is not None:
                active_session["artifact_publish_token_hash"] = _hash_artifact_publish_token(artifact_publish_token)
                self._agent_tools_sessions[session_id] = active_session

        extra_args = [
            *normalized_agent_args,
            "exec",
            "--skip-git-repo-check",
            "--cd",
            container_workspace,
            "--sandbox",
            "workspace-write",
            "--output-last-message",
            container_output_file,
            prompt,
        ]
        cmd = self._prepare_agent_cli_command(
            workspace=workspace,
            container_project_name=container_project_name,
            runtime_config_file=runtime_config_file,
            agent_type=resolved_agent_type,
            agent_tools_url=agent_tools_url,
            agent_tools_token=session_token,
            agent_tools_project_id="",
            agent_tools_chat_id=agent_tools_chat_id,
            repo_url=repo_url,
            artifacts_url=f"{self.artifact_publish_base_url}/api/agent-tools/sessions/{session_id}/artifacts/publish",
            artifacts_token=artifact_publish_token,
            allocate_tty=False,
            context_key=f"auto_config_chat:{session_id}",
            extra_args=extra_args,
        )
        emit("Launching temporary repository analysis chat...\n")
        emit(f"Working directory: {workspace}\n")
        emit(f"Repository URL: {repo_url}\n")
        emit(f"Branch: {branch}\n\n")

        if self._is_auto_config_request_cancelled(normalized_request_id):
            raise HTTPException(status_code=409, detail=AUTO_CONFIG_CANCELLED_ERROR)

        try:
            process = subprocess.Popen(
                cmd,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                bufsize=1,
            )
            self._set_auto_config_request_process(normalized_request_id, process)
        except OSError as exc:
            try:
                runtime_config_file.unlink()
            except OSError:
                pass
            self._remove_agent_tools_session(session_id)
            raise HTTPException(status_code=502, detail=f"Temporary auto-config chat failed to start: {exc}") from exc

        output_chunks: list[str] = []

        def consume_output() -> None:
            stdout = process.stdout
            if stdout is None:
                return
            try:
                for line in iter(stdout.readline, ""):
                    if line == "":
                        break
                    output_chunks.append(line)
                    emit(line)
            finally:
                stdout.close()

        try:
            try:
                consumer = Thread(target=consume_output, daemon=True)
                consumer.start()
                return_code = process.wait(timeout=max(20.0, float(AUTO_CONFIG_CHAT_TIMEOUT_SECONDS)))
                consumer.join(timeout=2.0)
            except subprocess.TimeoutExpired as exc:
                process.kill()
                try:
                    process.wait(timeout=2.0)
                except subprocess.TimeoutExpired:
                    pass
                emit("\nTemporary auto-config chat timed out.\n")
                if self._is_auto_config_request_cancelled(normalized_request_id):
                    raise HTTPException(status_code=409, detail=AUTO_CONFIG_CANCELLED_ERROR) from exc
                raise HTTPException(status_code=504, detail="Temporary auto-config chat timed out.") from exc

            output_text = "".join(output_chunks).strip()
            if return_code != 0:
                if self._is_auto_config_request_cancelled(normalized_request_id):
                    emit("\nAuto-config chat was cancelled by user.\n")
                    raise HTTPException(status_code=409, detail=AUTO_CONFIG_CANCELLED_ERROR)
                detail = _codex_exec_error_message_full(output_text)
                raise HTTPException(status_code=502, detail=f"Temporary auto-config chat failed: {detail}")

            try:
                raw_payload_text = output_file.read_text(encoding="utf-8", errors="ignore").strip()
            except OSError as exc:
                raise HTTPException(status_code=502, detail=AUTO_CONFIG_MISSING_OUTPUT_ERROR) from exc
            if not raw_payload_text:
                raise HTTPException(status_code=502, detail=AUTO_CONFIG_MISSING_OUTPUT_ERROR)

            try:
                parsed_payload = _parse_json_object_from_text(raw_payload_text)
            except ValueError as exc:
                raise HTTPException(status_code=502, detail=AUTO_CONFIG_INVALID_OUTPUT_ERROR) from exc
            return {
                "payload": parsed_payload,
                "model": _auto_config_analysis_model(resolved_agent_type, normalized_agent_args),
                "agent_type": resolved_agent_type,
                "agent_args": normalized_agent_args,
            }
        finally:
            self._set_auto_config_request_process(normalized_request_id, None)
            try:
                output_file.unlink()
            except OSError:
                pass
            try:
                runtime_config_file.unlink()
            except OSError:
                pass
            self._remove_agent_tools_session(session_id)

    def auto_configure_project(
        self,
        repo_url: Any,
        default_branch: Any = None,
        request_id: Any = None,
        agent_type: Any = None,
        agent_args: Any = None,
    ) -> dict[str, Any]:
        normalized_repo_url = str(repo_url or "").strip()
        if not normalized_repo_url:
            raise HTTPException(status_code=400, detail="repo_url is required.")
        resolved_agent_type = _normalize_chat_agent_type(agent_type)
        if agent_args is None:
            normalized_agent_args: list[str] = []
        elif isinstance(agent_args, list):
            normalized_agent_args = [str(arg) for arg in agent_args if str(arg).strip()]
        else:
            raise HTTPException(status_code=400, detail="agent_args must be an array.")
        normalized_request_id = str(request_id or "").strip()[:AUTO_CONFIG_REQUEST_ID_MAX_CHARS]
        if normalized_request_id:
            self._register_auto_config_request(normalized_request_id)
            if self._is_auto_config_request_cancelled(normalized_request_id):
                self._clear_auto_config_request(normalized_request_id)
                raise HTTPException(status_code=409, detail=AUTO_CONFIG_CANCELLED_ERROR)

        def emit_auto_config_log(text: str, replace: bool = False) -> None:
            if not normalized_request_id:
                return
            self._emit_auto_config_log(normalized_request_id, text, replace=replace)

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

        emit_auto_config_log("", replace=True)
        emit_auto_config_log("Preparing repository checkout for temporary analysis chat...\n")
        emit_auto_config_log(f"Repository URL: {normalized_repo_url}\n")
        emit_auto_config_log(f"Requested branch: {requested_branch or 'auto-detect'}\n")
        emit_auto_config_log(f"Analysis agent: {resolved_agent_type}\n")
        emit_auto_config_log(
            f"Analysis model: {_auto_config_analysis_model(resolved_agent_type, normalized_agent_args)}\n"
        )

        if self._is_auto_config_request_cancelled(normalized_request_id):
            raise HTTPException(status_code=409, detail=AUTO_CONFIG_CANCELLED_ERROR)

        try:
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
                        emit_auto_config_log(f"\n$ {' '.join(cmd)}\n")
                        result = _run(cmd, capture=True, check=False, env=env_candidate)
                        command_output = ((result.stdout or "") + (result.stderr or "")).strip()
                        if command_output:
                            emit_auto_config_log(f"{command_output}\n")
                        elif result.returncode != 0:
                            emit_auto_config_log(f"Command exited with code {result.returncode}.\n")
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

                emit_auto_config_log("\nRepository checkout complete. Starting temporary analysis chat...\n")
                if self._is_auto_config_request_cancelled(normalized_request_id):
                    raise HTTPException(status_code=409, detail=AUTO_CONFIG_CANCELLED_ERROR)

                recommendation: dict[str, Any] = {}
                chat_result: dict[str, Any] = {}
                emit_auto_config_log("Running temporary analysis chat...\n")
                chat_result = self._run_temporary_auto_config_chat(
                    workspace,
                    normalized_repo_url,
                    resolved_branch,
                    agent_type=resolved_agent_type,
                    agent_args=normalized_agent_args,
                    on_output=emit_auto_config_log if normalized_request_id else None,
                    request_id=normalized_request_id,
                )
                container_workspace = _container_workspace_path_for_project(
                    _extract_repo_name(normalized_repo_url) or "auto-config"
                )
                recommendation = self._normalize_auto_config_recommendation(
                    chat_result.get("payload") or {},
                    workspace,
                    project_container_workspace=container_workspace,
                )
                recommendation = self._apply_auto_config_repository_hints(recommendation, workspace)
                recommendation = self._normalize_auto_config_recommendation(
                    recommendation,
                    workspace,
                    project_container_workspace=container_workspace,
                )
                emit_auto_config_log("Auto-config recommendation discovery completed.\n")
        except HTTPException as exc:
            detail = str(exc.detail or f"HTTP {exc.status_code}")
            emit_auto_config_log(f"\nAuto-config failed: {detail}\n")
            raise
        finally:
            self._clear_auto_config_request(normalized_request_id)

        recommendation["default_branch"] = resolved_branch
        emit_auto_config_log("\nAuto-config completed successfully.\n")
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
        status = self.github_app_auth_status()
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
        status = self.github_app_auth_status()
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

        self._clear_github_installation_state(remove_credentials=False)
        record = {
            "installation_id": normalized_id,
            "account_login": account_login,
            "account_type": account_type,
            "repository_selection": repository_selection,
            "connected_at": _iso_now(),
        }
        _write_private_env_file(self.github_app_installation_file, json.dumps(record, indent=2) + "\n")
        status = self.github_app_auth_status()
        self._emit_auth_changed(reason="github_app_connected")
        LOGGER.debug("GitHub App installation connected: id=%s account=%s", normalized_id, account_login)
        return status

    def _connect_personal_access_token(
        self,
        provider: str,
        personal_access_token: Any,
        host: Any = "",
    ) -> dict[str, Any]:
        normalized_provider = (
            GIT_PROVIDER_GITLAB if str(provider or "").strip().lower() == GIT_PROVIDER_GITLAB else GIT_PROVIDER_GITHUB
        )
        normalized_token = _normalize_github_personal_access_token(personal_access_token)
        host_candidate = str(host or "").strip()
        if not host_candidate:
            host_candidate = "gitlab.com" if normalized_provider == GIT_PROVIDER_GITLAB else self._github_provider_host()
        normalized_scheme, normalized_host = _normalize_github_credential_endpoint(
            host_candidate,
            field_name="host",
            default_scheme=GIT_CREDENTIAL_DEFAULT_SCHEME,
        )
        verification = self._verify_github_personal_access_token(
            normalized_token,
            normalized_host,
            normalized_scheme,
        )
        verified_provider = str(verification.get("provider") or "").strip().lower()
        if verified_provider != normalized_provider:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Connected token resolved to provider '{verified_provider or 'unknown'}', "
                    f"but this endpoint expects '{normalized_provider}'."
                ),
            )
        account_login = verification["account_login"]

        account_name = str(verification.get("account_name") or account_login).strip() or account_login
        account_email = str(verification.get("account_email") or "").strip()
        account_id = str(verification.get("account_id") or "").strip()
        connected_at = _iso_now()
        record = {
            "token_id": uuid.uuid4().hex,
            "host": normalized_host,
            "scheme": normalized_scheme,
            "provider": normalized_provider,
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
        }

        existing = self._connected_personal_access_tokens(normalized_provider)
        filtered_existing: list[dict[str, Any]] = []
        for existing_record in existing:
            existing_host = str(existing_record.get("host") or "").strip().lower()
            try:
                existing_scheme = _normalize_github_credential_scheme(
                    existing_record.get("scheme"),
                    field_name="scheme",
                )
            except HTTPException:
                existing_scheme = GIT_CREDENTIAL_DEFAULT_SCHEME
            existing_login = str(existing_record.get("account_login") or "").strip().lower()
            existing_token = str(existing_record.get("personal_access_token") or "").strip()
            if (
                existing_host == normalized_host
                and existing_scheme == normalized_scheme
                and existing_login == account_login.lower()
                and existing_token == normalized_token
            ):
                continue
            filtered_existing.append(existing_record)

        self._persist_personal_access_tokens([record, *filtered_existing], normalized_provider)
        status = (
            self.gitlab_tokens_status()
            if normalized_provider == GIT_PROVIDER_GITLAB
            else self.github_tokens_status()
        )
        self._emit_auth_changed(reason=f"{normalized_provider}_personal_access_token_connected")
        LOGGER.debug(
            "Personal access token connected: provider=%s host=%s account=%s",
            normalized_provider,
            normalized_host,
            account_login,
        )
        return status

    def connect_github_personal_access_token(
        self,
        personal_access_token: Any,
        host: Any = "",
    ) -> dict[str, Any]:
        return self._connect_personal_access_token(
            GIT_PROVIDER_GITHUB,
            personal_access_token=personal_access_token,
            host=host,
        )

    def connect_gitlab_personal_access_token(
        self,
        personal_access_token: Any,
        host: Any = "",
    ) -> dict[str, Any]:
        return self._connect_personal_access_token(
            GIT_PROVIDER_GITLAB,
            personal_access_token=personal_access_token,
            host=host,
        )

    def _disconnect_personal_access_token(self, provider: str, token_id: Any) -> dict[str, Any]:
        normalized_provider = (
            GIT_PROVIDER_GITLAB if str(provider or "").strip().lower() == GIT_PROVIDER_GITLAB else GIT_PROVIDER_GITHUB
        )
        normalized_token_id = str(token_id or "").strip()
        if not normalized_token_id:
            raise HTTPException(status_code=400, detail="token_id is required.")
        if len(normalized_token_id) > GITHUB_PERSONAL_ACCESS_TOKEN_ID_MAX_CHARS:
            raise HTTPException(status_code=400, detail="token_id is invalid.")

        existing = self._connected_personal_access_tokens(normalized_provider)
        remaining = [record for record in existing if str(record.get("token_id") or "").strip() != normalized_token_id]
        if len(remaining) == len(existing):
            raise HTTPException(status_code=404, detail=f"{normalized_provider.capitalize()} personal access token not found.")

        self._persist_personal_access_tokens(remaining, normalized_provider)

        status = (
            self.gitlab_tokens_status()
            if normalized_provider == GIT_PROVIDER_GITLAB
            else self.github_tokens_status()
        )
        self._emit_auth_changed(reason=f"{normalized_provider}_personal_access_token_disconnected")
        LOGGER.debug(
            "Personal access token disconnected: provider=%s token_id=%s remaining=%s",
            normalized_provider,
            normalized_token_id,
            len(remaining),
        )
        return status

    def disconnect_github_personal_access_token(self, token_id: Any) -> dict[str, Any]:
        return self._disconnect_personal_access_token(GIT_PROVIDER_GITHUB, token_id)

    def disconnect_gitlab_personal_access_token(self, token_id: Any) -> dict[str, Any]:
        return self._disconnect_personal_access_token(GIT_PROVIDER_GITLAB, token_id)

    def _disconnect_all_personal_access_tokens(self, provider: str) -> dict[str, Any]:
        normalized_provider = (
            GIT_PROVIDER_GITLAB if str(provider or "").strip().lower() == GIT_PROVIDER_GITLAB else GIT_PROVIDER_GITHUB
        )
        self._clear_personal_access_token_state(normalized_provider, remove_credentials=False)
        status = (
            self.gitlab_tokens_status()
            if normalized_provider == GIT_PROVIDER_GITLAB
            else self.github_tokens_status()
        )
        self._emit_auth_changed(reason=f"{normalized_provider}_personal_access_tokens_disconnected")
        LOGGER.debug("All personal access tokens disconnected for provider=%s", normalized_provider)
        return status

    def disconnect_github_personal_access_tokens(self) -> dict[str, Any]:
        return self._disconnect_all_personal_access_tokens(GIT_PROVIDER_GITHUB)

    def disconnect_gitlab_personal_access_tokens(self) -> dict[str, Any]:
        return self._disconnect_all_personal_access_tokens(GIT_PROVIDER_GITLAB)

    def disconnect_github_app(self) -> dict[str, Any]:
        self._clear_github_installation_state(remove_credentials=False)
        status = self.github_app_auth_status()
        self._emit_auth_changed(reason="github_app_disconnected")
        LOGGER.debug("GitHub App installation disconnected.")
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
            "--tmpfs",
            TMP_DIR_TMPFS_SPEC,
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

    def _chat_runtime_config_path(self, chat_id: str) -> Path:
        return self.chat_runtime_configs_dir / f"{chat_id}.toml"

    @staticmethod
    def _strip_mcp_server_table(config_text: str, server_name: str) -> str:
        if not config_text:
            return ""
        escaped_name = re.escape(server_name)
        pattern = re.compile(r"(?ms)^\[mcp_servers\." + escaped_name + r"(?:\.[^\]]+)?\]\n.*?(?=^\[|\Z)")
        stripped = re.sub(pattern, "", config_text)
        return stripped.rstrip() + "\n"

    def _prepare_chat_runtime_config(
        self,
        chat_id: str,
        agent_type: str,
        *,
        agent_tools_url: str,
        agent_tools_token: str,
        agent_tools_project_id: str,
        agent_tools_chat_id: str,
    ) -> Path:
        from agent_cli import providers as agent_providers
        try:
            base_text = self.config_file.read_text(encoding="utf-8", errors="ignore")
        except OSError as exc:
            raise HTTPException(status_code=500, detail=f"Failed to read config file: {self.config_file}") from exc

        normalized_agent_tools_url = str(agent_tools_url or "").strip()
        normalized_agent_tools_token = str(agent_tools_token or "").strip()
        if not normalized_agent_tools_url:
            raise HTTPException(
                status_code=500,
                detail=f"Missing required {AGENT_TOOLS_URL_ENV} while preparing runtime config for {chat_id}.",
            )
        if not normalized_agent_tools_token:
            raise HTTPException(
                status_code=500,
                detail=f"Missing required {AGENT_TOOLS_TOKEN_ENV} while preparing runtime config for {chat_id}.",
            )

        self._ensure_agent_tools_mcp_runtime_script()

        agent_provider = agent_providers.get_provider(agent_type)
        mcp_env = {
            AGENT_TOOLS_URL_ENV: normalized_agent_tools_url,
            AGENT_TOOLS_TOKEN_ENV: normalized_agent_tools_token,
            AGENT_TOOLS_PROJECT_ID_ENV: str(agent_tools_project_id or '').strip(),
            AGENT_TOOLS_CHAT_ID_ENV: str(agent_tools_chat_id or '').strip(),
        }
        merged_text = agent_provider.build_mcp_config(
            base_config_text=base_text,
            mcp_env=mcp_env,
            script_path=AGENT_TOOLS_MCP_CONTAINER_SCRIPT_PATH,
        )

        ext = ".json" if isinstance(
            agent_provider,
            (agent_providers.ClaudeProvider, agent_providers.GeminiProvider),
        ) else ".toml"
        runtime_config_path = self.chat_runtime_configs_dir / f"{chat_id}{ext}"
        _write_private_env_file(runtime_config_path, merged_text)
        return runtime_config_path

    def _ensure_agent_tools_mcp_runtime_script(self) -> None:
        source_path = _agent_tools_mcp_source_path()
        try:
            script_text = source_path.read_text(encoding="utf-8")
        except OSError as exc:
            raise HTTPException(
                status_code=500,
                detail=f"Failed to read agent_tools MCP source script: {source_path}",
            ) from exc

        if self.agent_tools_mcp_runtime_script.exists():
            try:
                existing_text = self.agent_tools_mcp_runtime_script.read_text(encoding="utf-8")
            except OSError:
                existing_text = ""
            if existing_text == script_text:
                return

        try:
            _write_private_env_file(self.agent_tools_mcp_runtime_script, script_text)
        except OSError as exc:
            raise HTTPException(
                status_code=500,
                detail=f"Failed to materialize agent_tools MCP runtime script: {self.agent_tools_mcp_runtime_script}",
            ) from exc

    def _chat_agent_tools_url(self, chat_id: str) -> str:
        return f"{self.artifact_publish_base_url}/api/chats/{chat_id}/agent-tools"

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
            LOGGER.warning(
                "Artifact path outside chat workspace: chat_id=%s raw_path=%s resolved=%s workspace=%s",
                chat_id,
                raw_path,
                resolved,
                workspace,
            )
            raise HTTPException(status_code=400, detail="Artifact path must be inside the chat workspace.") from exc
        if not resolved.exists():
            LOGGER.warning(
                "Artifact file not found for chat_id=%s raw_path=%s resolved=%s",
                chat_id,
                raw_path,
                resolved,
            )
            raise HTTPException(status_code=404, detail=f"Artifact file not found: {raw_path}")
        if not resolved.is_file():
            LOGGER.warning(
                "Artifact path is not a file for chat_id=%s raw_path=%s resolved=%s",
                chat_id,
                raw_path,
                resolved,
            )
            raise HTTPException(status_code=400, detail=f"Artifact path is not a file: {raw_path}")
        relative_path = _coerce_artifact_relative_path(relative.as_posix())
        if not relative_path:
            LOGGER.warning("Artifact path normalized to empty for chat_id=%s raw_path=%s resolved=%s", chat_id, raw_path, resolved)
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

    @staticmethod
    def _require_session_artifact_publish_token(session: dict[str, Any], token: Any) -> None:
        expected_hash = str(session.get("artifact_publish_token_hash") or "")
        if not expected_hash:
            raise HTTPException(status_code=409, detail="Artifact publishing is unavailable for this session.")

        submitted_token = str(token or "").strip()
        if not submitted_token:
            raise HTTPException(status_code=401, detail="Missing artifact publish token.")
        submitted_hash = _hash_artifact_publish_token(submitted_token)
        if not submitted_hash or not hmac.compare_digest(submitted_hash, expected_hash):
            raise HTTPException(status_code=403, detail="Invalid artifact publish token.")

    @staticmethod
    def _require_agent_tools_token(chat: dict[str, Any], token: Any) -> None:
        expected_hash = str(chat.get("agent_tools_token_hash") or "")
        if not expected_hash:
            raise HTTPException(status_code=409, detail="agent_tools is unavailable until the chat is started.")

        submitted_token = str(token or "").strip()
        if not submitted_token:
            raise HTTPException(status_code=401, detail="Missing agent_tools token.")
        submitted_hash = _hash_agent_tools_token(submitted_token)
        if not submitted_hash or not hmac.compare_digest(submitted_hash, expected_hash):
            raise HTTPException(status_code=403, detail="Invalid agent_tools token.")

    def _chat_and_project_for_agent_tools(self, chat_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
        state = self.load()
        chat = state["chats"].get(chat_id)
        if chat is None:
            raise HTTPException(status_code=404, detail="Chat not found.")
        project_id = str(chat.get("project_id") or "").strip()
        project = state["projects"].get(project_id)
        if project is None:
            raise HTTPException(status_code=404, detail="Project not found.")
        return chat, project

    @staticmethod
    def _credential_lookup_key(credential: dict[str, Any]) -> str:
        return str(credential.get("credential_id") or "").strip()

    def _credential_from_id(self, credential_id: str) -> dict[str, Any] | None:
        normalized_id = str(credential_id or "").strip()
        if not normalized_id:
            return None
        for credential in self._credential_catalog():
            if self._credential_lookup_key(credential) == normalized_id:
                return credential
        return None

    def _materialize_agent_tool_credential(
        self,
        credential: dict[str, Any],
        *,
        context_key: str,
    ) -> dict[str, Any]:
        credential_id = str(credential.get("credential_id") or "").strip()
        kind = str(credential.get("kind") or "").strip()
        provider = str(credential.get("provider") or "").strip()
        host = str(credential.get("host") or "").strip()
        scheme = _normalize_github_credential_scheme(credential.get("scheme"), field_name="scheme")
        account_login = str(credential.get("account_login") or "").strip()
        account_name = str(credential.get("account_name") or "").strip()

        username = account_login
        secret = ""
        if kind == "github_app_installation":
            installation_id = int(str(credential_id).split(":", 1)[1]) if ":" in credential_id else 0
            if installation_id <= 0:
                raise HTTPException(status_code=400, detail=f"Invalid GitHub App credential id: {credential_id}")
            installation_status = self.github_app_auth_status()
            if int(installation_status.get("installation_id") or 0) != installation_id:
                raise HTTPException(status_code=404, detail="GitHub App installation credential is no longer connected.")
            token, _expires_at = self._github_installation_token(installation_id)
            username = "x-access-token"
            secret = token
        elif kind == "personal_access_token":
            matching = None
            for token in self._connected_personal_access_tokens(provider):
                if str(token.get("token_id") or "").strip() == credential_id:
                    matching = token
                    break
            if matching is None:
                raise HTTPException(status_code=404, detail="Personal access token credential is no longer connected.")
            username = str(matching.get("account_login") or "").strip()
            secret = str(matching.get("personal_access_token") or "").strip()
            account_login = str(matching.get("account_login") or "").strip()
            account_name = str(matching.get("account_name") or "").strip()
            host = str(matching.get("host") or "").strip()
            scheme = _normalize_github_credential_scheme(matching.get("scheme"), field_name="scheme")
        else:
            raise HTTPException(status_code=400, detail=f"Unsupported credential kind: {kind}")

        if not username or not secret or not host:
            raise HTTPException(status_code=500, detail="Resolved credential is missing required fields.")

        credential_file = self._write_github_git_credentials(
            host=host,
            username=username,
            secret=secret,
            scheme=scheme,
            credential_id=credential_id,
            context_key=context_key,
        )
        encoded_username = urllib.parse.quote(username, safe="")
        encoded_secret = urllib.parse.quote(secret, safe="")
        credential_line = f"{scheme}://{encoded_username}:{encoded_secret}@{host}"
        return {
            "credential_id": credential_id,
            "kind": kind,
            "provider": provider,
            "host": host,
            "scheme": scheme,
            "account_login": account_login,
            "account_name": account_name,
            "summary": str(credential.get("summary") or ""),
            "username": username,
            "secret": secret,
            "credential_line": credential_line,
            "host_credential_file": credential_file,
            "git_env": self._git_env_for_credentials_file(credential_file, host, scheme=scheme),
        }

    def _resolve_agent_tools_credential_ids(
        self,
        project: dict[str, Any],
        mode: str,
        credential_ids: list[str],
    ) -> list[str]:
        available = self._project_available_credentials(project)
        available_ids = [self._credential_lookup_key(entry) for entry in available if self._credential_lookup_key(entry)]
        available_id_set = set(available_ids)
        requested_ids = [str(item or "").strip() for item in credential_ids if str(item or "").strip()]

        if mode == PROJECT_CREDENTIAL_BINDING_MODE_ALL:
            return available_ids
        if mode in {PROJECT_CREDENTIAL_BINDING_MODE_SET, PROJECT_CREDENTIAL_BINDING_MODE_SINGLE}:
            selected = requested_ids
            if not selected:
                selected = self._resolved_project_credential_ids(project)
            selected = [credential_id for credential_id in selected if credential_id in available_id_set]
            if mode == PROJECT_CREDENTIAL_BINDING_MODE_SINGLE and selected:
                return selected[:1]
            return selected

        if mode == PROJECT_CREDENTIAL_BINDING_MODE_AUTO:
            contexts = self._github_repo_all_auth_contexts(str(project.get("repo_url") or ""), project=project)
            resolved_ids: list[str] = []
            for _m, _h, auth_payload in contexts:
                candidate_id = str(auth_payload.get("credential_id") or "").strip()
                if candidate_id and candidate_id in available_id_set and candidate_id not in resolved_ids:
                    resolved_ids.append(candidate_id)
            return resolved_ids

        return []

    def agent_tools_credentials_list_payload(self, chat_id: str) -> dict[str, Any]:
        _chat, project = self._chat_and_project_for_agent_tools(chat_id)
        binding_payload = self.project_credential_binding_payload(str(project.get("id") or ""))
        return {
            "project_id": str(project.get("id") or ""),
            "repo_url": str(project.get("repo_url") or ""),
            "credential_binding": binding_payload["binding"],
            "available_credentials": binding_payload["available_credentials"],
            "effective_credential_ids": binding_payload["effective_credential_ids"],
        }

    def resolve_agent_tools_credentials(
        self,
        chat_id: str,
        mode: Any = PROJECT_CREDENTIAL_BINDING_MODE_AUTO,
        credential_ids: Any = None,
    ) -> dict[str, Any]:
        _chat, project = self._chat_and_project_for_agent_tools(chat_id)
        normalized_mode = str(mode or "").strip().lower()
        if normalized_mode not in PROJECT_CREDENTIAL_BINDING_MODES:
            normalized_mode = PROJECT_CREDENTIAL_BINDING_MODE_AUTO
        submitted_ids = credential_ids if isinstance(credential_ids, list) else []
        selected_ids = self._resolve_agent_tools_credential_ids(project, normalized_mode, submitted_ids)
        resolved_credentials: list[dict[str, Any]] = []
        for credential_id in selected_ids:
            credential = self._credential_from_id(credential_id)
            if credential is None:
                continue
            resolved_credentials.append(
                self._materialize_agent_tool_credential(
                    credential,
                    context_key=f"agent_tools:{chat_id}:{credential_id}",
                )
            )
        return {
            "project_id": str(project.get("id") or ""),
            "repo_url": str(project.get("repo_url") or ""),
            "mode": normalized_mode,
            "credential_ids": selected_ids,
            "credentials": resolved_credentials,
        }

    def attach_agent_tools_project_credentials(
        self,
        chat_id: str,
        mode: Any,
        credential_ids: Any = None,
    ) -> dict[str, Any]:
        _chat, project = self._chat_and_project_for_agent_tools(chat_id)
        return self.attach_project_credentials(
            str(project.get("id") or ""),
            mode=mode,
            credential_ids=credential_ids if isinstance(credential_ids, list) else [],
            source=f"agent_tools:{chat_id}",
        )

    def _create_agent_tools_session(
        self,
        *,
        project_id: str = "",
        repo_url: str = "",
        credential_binding: dict[str, Any] | None = None,
        workspace: Path | None = None,
    ) -> tuple[str, str]:
        session_id = uuid.uuid4().hex
        token = _new_agent_tools_token()
        payload = {
            "id": session_id,
            "project_id": str(project_id or "").strip(),
            "repo_url": str(repo_url or "").strip(),
            "credential_binding": _normalize_project_credential_binding(credential_binding),
            "token_hash": _hash_agent_tools_token(token),
            "workspace": str(workspace) if workspace else "",
            "created_at": _iso_now(),
            "artifacts": [],
            "artifact_current_ids": [],
            "artifact_publish_token_hash": "",
        }
        with self._agent_tools_sessions_lock:
            self._agent_tools_sessions[session_id] = payload
        return session_id, token

    def _agent_tools_session(self, session_id: str) -> dict[str, Any]:
        normalized_session_id = str(session_id or "").strip()
        if not normalized_session_id:
            raise HTTPException(status_code=400, detail="session_id is required.")
        with self._agent_tools_sessions_lock:
            session = self._agent_tools_sessions.get(normalized_session_id)
        if session is None:
            raise HTTPException(status_code=404, detail="agent_tools session not found.")
        return dict(session)

    def _remove_agent_tools_session(self, session_id: Any) -> None:
        normalized_session_id = str(session_id or "").strip()
        if not normalized_session_id:
            return
        with self._agent_tools_sessions_lock:
            self._agent_tools_sessions.pop(normalized_session_id, None)
        session_artifact_root = self._session_artifact_storage_root(normalized_session_id)
        if session_artifact_root.exists():
            self._delete_path(session_artifact_root)

    def require_agent_tools_session_token(self, session_id: str, token: Any) -> dict[str, Any]:
        session = self._agent_tools_session(session_id)
        expected_hash = str(session.get("token_hash") or "")
        if not expected_hash:
            raise HTTPException(status_code=409, detail="agent_tools session is not active.")
        submitted = str(token or "").strip()
        if not submitted:
            raise HTTPException(status_code=401, detail="Missing agent_tools token.")
        submitted_hash = _hash_agent_tools_token(submitted)
        if not submitted_hash or not hmac.compare_digest(submitted_hash, expected_hash):
            raise HTTPException(status_code=403, detail="Invalid agent_tools token.")
        return session

    def _agent_tools_project_context_from_session(self, session: dict[str, Any]) -> dict[str, Any]:
        project_id = str(session.get("project_id") or "").strip()
        if project_id:
            project = self.project(project_id)
            if project is not None:
                return dict(project)
        repo_url = str(session.get("repo_url") or "").strip()
        return {
            "id": project_id,
            "repo_url": repo_url,
            "credential_binding": _normalize_project_credential_binding(session.get("credential_binding")),
        }

    def agent_tools_session_credentials_list_payload(self, session_id: str) -> dict[str, Any]:
        session = self._agent_tools_session(session_id)
        project = self._agent_tools_project_context_from_session(session)
        binding = _normalize_project_credential_binding(project.get("credential_binding"))
        return {
            "project_id": str(project.get("id") or ""),
            "repo_url": str(project.get("repo_url") or ""),
            "credential_binding": binding,
            "available_credentials": self._project_available_credentials(project),
            "effective_credential_ids": self._resolve_agent_tools_credential_ids(
                project,
                binding["mode"],
                binding["credential_ids"],
            ),
        }

    def resolve_agent_tools_session_credentials(
        self,
        session_id: str,
        mode: Any = PROJECT_CREDENTIAL_BINDING_MODE_AUTO,
        credential_ids: Any = None,
    ) -> dict[str, Any]:
        session = self._agent_tools_session(session_id)
        project = self._agent_tools_project_context_from_session(session)
        normalized_mode = str(mode or "").strip().lower()
        if normalized_mode not in PROJECT_CREDENTIAL_BINDING_MODES:
            normalized_mode = PROJECT_CREDENTIAL_BINDING_MODE_AUTO
        submitted_ids = credential_ids if isinstance(credential_ids, list) else []
        selected_ids = self._resolve_agent_tools_credential_ids(project, normalized_mode, submitted_ids)
        resolved_credentials: list[dict[str, Any]] = []
        for credential_id in selected_ids:
            credential = self._credential_from_id(credential_id)
            if credential is None:
                continue
            resolved_credentials.append(
                self._materialize_agent_tool_credential(
                    credential,
                    context_key=f"agent_tools_session:{session_id}:{credential_id}",
                )
            )
        return {
            "project_id": str(project.get("id") or ""),
            "repo_url": str(project.get("repo_url") or ""),
            "mode": normalized_mode,
            "credential_ids": selected_ids,
            "credentials": resolved_credentials,
        }

    def attach_agent_tools_session_project_credentials(
        self,
        session_id: str,
        mode: Any,
        credential_ids: Any = None,
    ) -> dict[str, Any]:
        session = self._agent_tools_session(session_id)
        project_id = str(session.get("project_id") or "").strip()
        if not project_id:
            project = self._agent_tools_project_context_from_session(session)
            requested_ids = credential_ids if isinstance(credential_ids, list) else []
            binding = _normalize_project_credential_binding(
                {
                    "mode": mode,
                    "credential_ids": requested_ids,
                    "source": f"agent_tools_session:{session_id}",
                    "updated_at": _iso_now(),
                }
            )
            available_credentials = self._project_available_credentials(project)
            available_ids = {
                str(entry.get("credential_id") or "").strip()
                for entry in available_credentials
                if str(entry.get("credential_id") or "").strip()
            }
            if binding["mode"] in {PROJECT_CREDENTIAL_BINDING_MODE_SET, PROJECT_CREDENTIAL_BINDING_MODE_SINGLE}:
                filtered_ids = [credential_id for credential_id in binding["credential_ids"] if credential_id in available_ids]
                if not filtered_ids:
                    raise HTTPException(status_code=400, detail="No valid credentials were provided for this repository.")
                binding["credential_ids"] = (
                    filtered_ids[:1] if binding["mode"] == PROJECT_CREDENTIAL_BINDING_MODE_SINGLE else filtered_ids
                )
            else:
                binding["credential_ids"] = []

            project["credential_binding"] = binding
            effective_ids = self._resolve_agent_tools_credential_ids(
                project,
                binding["mode"],
                binding["credential_ids"],
            )
            with self._agent_tools_sessions_lock:
                active_session = self._agent_tools_sessions.get(session_id)
                if active_session is not None:
                    active_session["credential_binding"] = binding
                    self._agent_tools_sessions[session_id] = active_session

            return {
                "project_id": "",
                "binding": binding,
                "available_credentials": available_credentials,
                "effective_credential_ids": effective_ids,
            }
        return self.attach_project_credentials(
            project_id=project_id,
            mode=mode,
            credential_ids=credential_ids if isinstance(credential_ids, list) else [],
            source=f"agent_tools_session:{session_id}",
        )

    def list_chat_artifacts(self, chat_id: str) -> list[dict[str, Any]]:
        chat = self.chat(chat_id)
        if chat is None:
            raise HTTPException(status_code=404, detail="Chat not found.")
        artifacts = _normalize_chat_artifacts(chat.get("artifacts"))
        return [self._chat_artifact_public_payload(chat_id, artifact) for artifact in reversed(artifacts)]

    def _chat_artifact_storage_root(self, chat_id: str) -> Path:
        return self.chat_artifacts_dir / str(chat_id)

    def _session_artifact_storage_root(self, session_id: str) -> Path:
        return self.session_artifacts_dir / str(session_id)

    def _persist_artifact_file_copy(
        self,
        *,
        source_file: Path,
        storage_root: Path,
        artifact_id: str,
        relative_path: str,
    ) -> tuple[str, int]:
        storage_entry_name = _normalize_artifact_name("", fallback=Path(relative_path).name)
        artifact_storage_dir = storage_root / artifact_id
        if artifact_storage_dir.exists():
            self._delete_path(artifact_storage_dir)
        try:
            artifact_storage_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise HTTPException(
                status_code=500,
                detail=f"Failed to create artifact storage directory: {artifact_storage_dir}",
            ) from exc

        destination = artifact_storage_dir / storage_entry_name
        temporary_destination = artifact_storage_dir / f".{storage_entry_name}.tmp-{uuid.uuid4().hex}"
        try:
            with source_file.open("rb") as src_handle, temporary_destination.open("wb") as dst_handle:
                shutil.copyfileobj(src_handle, dst_handle, length=1024 * 1024)
            os.replace(temporary_destination, destination)
            size_bytes = int(destination.stat().st_size)
        except OSError as exc:
            raise HTTPException(status_code=500, detail=f"Failed to persist artifact file copy: {source_file}") from exc
        finally:
            if temporary_destination.exists():
                try:
                    temporary_destination.unlink()
                except OSError:
                    pass

        try:
            storage_relative = destination.resolve().relative_to(self.artifacts_dir.resolve()).as_posix()
        except ValueError as exc:
            raise HTTPException(status_code=500, detail="Artifact storage path escaped managed directory.") from exc
        normalized_storage_relative = _coerce_artifact_relative_path(storage_relative)
        if not normalized_storage_relative:
            raise HTTPException(status_code=500, detail="Artifact storage path is invalid.")
        return normalized_storage_relative, size_bytes

    def _upsert_chat_artifact_from_file(
        self,
        *,
        state: dict[str, Any],
        chat_id: str,
        chat: dict[str, Any],
        file_path: Path,
        relative_path: str,
        name: Any = None,
    ) -> dict[str, Any]:
        now = _iso_now()
        artifacts = _normalize_chat_artifacts(chat.get("artifacts"))
        normalized_name = _normalize_artifact_name(name, fallback=file_path.name)

        existing_index = -1
        for index, artifact in enumerate(artifacts):
            if str(artifact.get("relative_path") or "") == relative_path:
                existing_index = index
                break

        artifact_id = (
            str(artifacts[existing_index].get("id") or "") or uuid.uuid4().hex
            if existing_index >= 0
            else uuid.uuid4().hex
        )
        storage_relative_path, persisted_size_bytes = self._persist_artifact_file_copy(
            source_file=file_path,
            storage_root=self._chat_artifact_storage_root(chat_id),
            artifact_id=artifact_id,
            relative_path=relative_path,
        )
        stored_artifact = {
            "id": artifact_id,
            "name": normalized_name,
            "relative_path": relative_path,
            "storage_relative_path": storage_relative_path,
            "size_bytes": int(persisted_size_bytes),
            "created_at": now,
        }

        if existing_index >= 0:
            artifacts[existing_index] = stored_artifact
        else:
            artifacts.append(stored_artifact)
            if len(artifacts) > CHAT_ARTIFACTS_MAX_ITEMS:
                artifacts = artifacts[-CHAT_ARTIFACTS_MAX_ITEMS:]

        current_ids = _normalize_chat_current_artifact_ids(chat.get("artifact_current_ids"), artifacts)
        if artifact_id and artifact_id not in current_ids:
            current_ids.append(artifact_id)
        if len(current_ids) > CHAT_ARTIFACTS_MAX_ITEMS:
            current_ids = current_ids[-CHAT_ARTIFACTS_MAX_ITEMS:]

        chat["artifacts"] = artifacts
        chat["artifact_current_ids"] = current_ids
        chat["artifact_prompt_history"] = _normalize_chat_artifact_prompt_history(chat.get("artifact_prompt_history"))
        chat["updated_at"] = now
        state["chats"][chat_id] = chat
        return stored_artifact

    def _upsert_session_artifact_from_file(
        self,
        *,
        session_id: str,
        session: dict[str, Any],
        file_path: Path,
        relative_path: str,
        name: Any = None,
    ) -> dict[str, Any]:
        now = _iso_now()
        artifacts = _normalize_chat_artifacts(session.get("artifacts"))
        normalized_name = _normalize_artifact_name(name, fallback=file_path.name)

        existing_index = -1
        for index, artifact in enumerate(artifacts):
            if str(artifact.get("relative_path") or "") == relative_path:
                existing_index = index
                break

        artifact_id = (
            str(artifacts[existing_index].get("id") or "") or uuid.uuid4().hex
            if existing_index >= 0
            else uuid.uuid4().hex
        )
        storage_relative_path, persisted_size_bytes = self._persist_artifact_file_copy(
            source_file=file_path,
            storage_root=self._session_artifact_storage_root(session_id),
            artifact_id=artifact_id,
            relative_path=relative_path,
        )
        stored_artifact = {
            "id": artifact_id,
            "name": normalized_name,
            "relative_path": relative_path,
            "storage_relative_path": storage_relative_path,
            "size_bytes": int(persisted_size_bytes),
            "created_at": now,
        }

        if existing_index >= 0:
            artifacts[existing_index] = stored_artifact
        else:
            artifacts.append(stored_artifact)
            if len(artifacts) > CHAT_ARTIFACTS_MAX_ITEMS:
                artifacts = artifacts[-CHAT_ARTIFACTS_MAX_ITEMS:]

        current_ids = _normalize_chat_current_artifact_ids(session.get("artifact_current_ids"), artifacts)
        if artifact_id and artifact_id not in current_ids:
            current_ids.append(artifact_id)
        if len(current_ids) > CHAT_ARTIFACTS_MAX_ITEMS:
            current_ids = current_ids[-CHAT_ARTIFACTS_MAX_ITEMS:]

        with self._agent_tools_sessions_lock:
            active_session = self._agent_tools_sessions.get(session_id)
            if active_session is not None:
                active_session["artifacts"] = artifacts
                active_session["artifact_current_ids"] = current_ids
                self._agent_tools_sessions[session_id] = active_session

        return stored_artifact

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
        stored_artifact = self._upsert_chat_artifact_from_file(
            state=state,
            chat_id=chat_id,
            chat=chat,
            file_path=file_path,
            relative_path=relative_path,
            name=name,
        )
        self.save(state, reason="chat_artifact_published")
        return self._chat_artifact_public_payload(chat_id, stored_artifact)

    def submit_chat_artifact(
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
        self._require_agent_tools_token(chat, token)

        file_path, relative_path = self._resolve_chat_artifact_file(chat_id, submitted_path)
        stored_artifact = self._upsert_chat_artifact_from_file(
            state=state,
            chat_id=chat_id,
            chat=chat,
            file_path=file_path,
            relative_path=relative_path,
            name=name,
        )
        self.save(state, reason="chat_artifact_submitted")
        return self._chat_artifact_public_payload(chat_id, stored_artifact)

    def publish_session_artifact(
        self,
        session_id: str,
        token: Any,
        submitted_path: Any,
        name: Any = None,
    ) -> dict[str, Any]:
        session = self._agent_tools_session(session_id)
        self._require_session_artifact_publish_token(session, token)

        workspace = Path(str(session.get("workspace") or "")).resolve()
        if not workspace or not workspace.exists():
            raise HTTPException(status_code=409, detail="Session workspace is unavailable.")

        file_path, relative_path = self._resolve_artifact_file_in_workspace(workspace, submitted_path)
        stored_artifact = self._upsert_session_artifact_from_file(
            session_id=session_id,
            session=session,
            file_path=file_path,
            relative_path=relative_path,
            name=name,
        )
        return self._session_artifact_public_payload(session_id, stored_artifact)

    def submit_session_artifact(
        self,
        session_id: str,
        token: Any,
        submitted_path: Any,
        name: Any = None,
    ) -> dict[str, Any]:
        session = self.require_agent_tools_session_token(session_id, token)
        workspace = Path(str(session.get("workspace") or "")).resolve()
        if not workspace or not workspace.exists():
            raise HTTPException(status_code=409, detail="Session workspace is unavailable.")

        file_path, relative_path = self._resolve_artifact_file_in_workspace(workspace, submitted_path)
        stored_artifact = self._upsert_session_artifact_from_file(
            session_id=session_id,
            session=session,
            file_path=file_path,
            relative_path=relative_path,
            name=name,
        )
        return self._session_artifact_public_payload(session_id, stored_artifact)

    def _resolve_artifact_file_in_workspace(self, workspace: Path, submitted_path: Any) -> tuple[Path, str]:
        normalized_path = str(submitted_path or "").strip()
        if not normalized_path:
            raise HTTPException(status_code=400, detail="path is required.")

        candidate = (workspace / normalized_path).resolve()
        try:
            candidate.relative_to(workspace)
        except ValueError as exc:
            LOGGER.warning(
                "Artifact path outside workspace: workspace=%s raw_path=%s candidate=%s",
                workspace,
                normalized_path,
                candidate,
            )
            raise HTTPException(status_code=400, detail="Artifact path must be inside the workspace.") from exc

        if not candidate.exists():
            LOGGER.warning(
                "Artifact file not found in workspace: workspace=%s raw_path=%s candidate=%s",
                workspace,
                normalized_path,
                candidate,
            )
            raise HTTPException(status_code=404, detail=f"Artifact file not found: {normalized_path}")
        if not candidate.is_file():
            LOGGER.warning(
                "Artifact path is not a file in workspace: workspace=%s raw_path=%s candidate=%s",
                workspace,
                normalized_path,
                candidate,
            )
            raise HTTPException(status_code=400, detail=f"Artifact path is not a file: {normalized_path}")

        relative = candidate.relative_to(workspace)
        relative_path = _coerce_artifact_relative_path(relative.as_posix())
        if not relative_path:
            LOGGER.warning(
                "Artifact path normalized to empty in workspace: workspace=%s raw_path=%s candidate=%s",
                workspace,
                normalized_path,
                candidate,
            )
            raise HTTPException(status_code=400, detail="Artifact path is invalid.")
        return candidate, relative_path

    def _session_artifact_public_payload(self, session_id: str, artifact: dict[str, Any]) -> dict[str, Any]:
        artifact_id = str(artifact.get("id") or "")
        return {
            "id": artifact_id,
            "name": _normalize_artifact_name(artifact.get("name"), fallback=Path(str(artifact.get("relative_path") or "")).name),
            "relative_path": str(artifact.get("relative_path") or ""),
            "size_bytes": int(artifact.get("size_bytes") or 0),
            "created_at": str(artifact.get("created_at") or ""),
            "preview_url": self._session_artifact_preview_url(session_id, artifact_id),
            "download_url": self._session_artifact_download_url(session_id, artifact_id),
        }

    def _session_artifact_publish_url(self, session_id: str) -> str:
        return f"{self.artifact_publish_base_url}/api/agent-tools/sessions/{session_id}/artifacts/publish"

    def _session_artifact_download_url(self, session_id: str, artifact_id: str) -> str:
        return f"/api/agent-tools/sessions/{session_id}/artifacts/{artifact_id}/download"

    def _session_artifact_preview_url(self, session_id: str, artifact_id: str) -> str:
        return f"/api/agent-tools/sessions/{session_id}/artifacts/{artifact_id}/preview"

    def _resolve_persisted_artifact_path(self, artifact: dict[str, Any]) -> Path | None:
        storage_relative_path = _coerce_artifact_relative_path(artifact.get("storage_relative_path"))
        if not storage_relative_path:
            return None
        artifacts_root = self.artifacts_dir.resolve()
        resolved = (artifacts_root / storage_relative_path).resolve()
        try:
            resolved.relative_to(artifacts_root)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Artifact path is invalid.") from exc
        if not resolved.exists() or not resolved.is_file():
            return None
        return resolved

    def resolve_session_artifact_download(self, session_id: str, artifact_id: str) -> tuple[Path, str, str]:
        session = self._agent_tools_session(session_id)
        normalized_artifact_id = str(artifact_id or "").strip()
        if not normalized_artifact_id:
            raise HTTPException(status_code=400, detail="artifact_id is required.")

        artifacts = _normalize_chat_artifacts(session.get("artifacts"))
        match = next((entry for entry in artifacts if str(entry.get("id") or "") == normalized_artifact_id), None)
        if match is None:
            raise HTTPException(status_code=404, detail="Artifact not found.")

        resolved = self._resolve_persisted_artifact_path(match)
        if resolved is None:
            workspace = Path(str(session.get("workspace") or "")).resolve()
            if not workspace or not workspace.exists():
                raise HTTPException(status_code=409, detail="Session workspace is unavailable.")

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

    def resolve_session_artifact_preview(self, session_id: str, artifact_id: str) -> tuple[Path, str]:
        artifact_path, _filename, media_type = self.resolve_session_artifact_download(session_id, artifact_id)
        return artifact_path, media_type

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

        resolved = self._resolve_persisted_artifact_path(match)
        if resolved is None:
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
        credential_binding: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not repo_url:
            raise HTTPException(status_code=400, detail="repo_url is required.")

        state = self.load()
        project_id = uuid.uuid4().hex
        project_name = name or _extract_repo_name(repo_url)
        normalized_binding = self._auto_discover_project_credential_binding(
            repo_url,
            credential_binding=credential_binding,
        )
        normalized_env_vars = self._dedupe_entries(default_env_vars or [])
        if not any(
            str(entry).split("=", 1)[0].strip().upper() == "GH_TOKEN"
            for entry in normalized_env_vars
        ):
            gh_token = self._github_personal_access_token_for_repo(repo_url, credential_binding=normalized_binding)
            if gh_token:
                normalized_env_vars.append(f"GH_TOKEN={gh_token}")
        resolved_default_branch = str(default_branch or "").strip()
        if not resolved_default_branch:
            git_env = self._github_git_env_for_repo(
                repo_url,
                project={"repo_url": repo_url, "credential_binding": normalized_binding},
            )
            resolved_default_branch = _detect_default_branch(repo_url, env=git_env)
        normalized_base_mode = _normalize_base_image_mode(base_image_mode)
        normalized_base_value = _normalize_base_image_value(normalized_base_mode, base_image_value)
        if normalized_base_mode == "repo_path" and not normalized_base_value:
            raise HTTPException(
                status_code=400,
                detail="base_image_value is required when base_image_mode is 'repo_path'.",
            )
        project = {
            "id": project_id,
            "name": project_name,
            "repo_url": repo_url,
            "setup_script": setup_script or "",
            "base_image_mode": normalized_base_mode,
            "base_image_value": normalized_base_value,
            "default_ro_mounts": default_ro_mounts or [],
            "default_rw_mounts": default_rw_mounts or [],
            "default_env_vars": normalized_env_vars,
            "default_branch": resolved_default_branch,
            "created_at": _iso_now(),
            "updated_at": _iso_now(),
            "setup_snapshot_image": "",
            "build_status": "pending",
            "build_error": "",
            "build_started_at": "",
            "build_finished_at": "",
            "credential_binding": normalized_binding,
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
            "credential_binding",
        ]:
            if field in update:
                project[field] = update[field]
        normalized_base_mode = _normalize_base_image_mode(project.get("base_image_mode"))
        normalized_base_value = _normalize_base_image_value(normalized_base_mode, project.get("base_image_value"))
        if normalized_base_mode == "repo_path" and not normalized_base_value:
            raise HTTPException(
                status_code=400,
                detail="base_image_value is required when base_image_mode is 'repo_path'.",
            )
        project["base_image_mode"] = normalized_base_mode
        project["base_image_value"] = normalized_base_value

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

    def _project_available_credentials(self, project: dict[str, Any]) -> list[dict[str, Any]]:
        repo_host = _git_repo_host(str(project.get("repo_url") or ""))
        if not repo_host:
            return []
        host_credentials: list[dict[str, Any]] = []
        for credential in self._credential_catalog():
            credential_host = str(credential.get("host") or "").strip().lower()
            if credential_host != repo_host:
                continue
            host_credentials.append(dict(credential))
        return host_credentials

    def _resolved_project_credential_ids(self, project: dict[str, Any]) -> list[str]:
        binding = _normalize_project_credential_binding(project.get("credential_binding"))
        available = self._project_available_credentials(project)
        available_ids = [str(entry.get("credential_id") or "").strip() for entry in available if str(entry.get("credential_id") or "").strip()]
        available_id_set = set(available_ids)

        if binding["mode"] in {PROJECT_CREDENTIAL_BINDING_MODE_SET, PROJECT_CREDENTIAL_BINDING_MODE_SINGLE}:
            selected = [credential_id for credential_id in binding["credential_ids"] if credential_id in available_id_set]
            if binding["mode"] == PROJECT_CREDENTIAL_BINDING_MODE_SINGLE and selected:
                return selected[:1]
            return selected
        if binding["mode"] == PROJECT_CREDENTIAL_BINDING_MODE_ALL:
            return available_ids
        return []

    def project_credential_binding_payload(self, project_id: str) -> dict[str, Any]:
        project = self.project(project_id)
        if project is None:
            raise HTTPException(status_code=404, detail="Project not found.")
        binding = _normalize_project_credential_binding(project.get("credential_binding"))
        return {
            "project_id": project_id,
            "binding": binding,
            "available_credentials": self._project_available_credentials(project),
            "effective_credential_ids": self._resolved_project_credential_ids(project),
        }

    def attach_project_credentials(
        self,
        project_id: str,
        mode: Any,
        credential_ids: Any = None,
        source: str = "agent_tools",
    ) -> dict[str, Any]:
        state = self.load()
        project = state["projects"].get(project_id)
        if project is None:
            raise HTTPException(status_code=404, detail="Project not found.")

        requested_ids = credential_ids if isinstance(credential_ids, list) else []
        binding = _normalize_project_credential_binding(
            {
                "mode": mode,
                "credential_ids": requested_ids,
                "source": source,
                "updated_at": _iso_now(),
            }
        )
        available_ids = {
            str(entry.get("credential_id") or "").strip()
            for entry in self._project_available_credentials(project)
            if str(entry.get("credential_id") or "").strip()
        }
        if binding["mode"] in {PROJECT_CREDENTIAL_BINDING_MODE_SET, PROJECT_CREDENTIAL_BINDING_MODE_SINGLE}:
            filtered = [credential_id for credential_id in binding["credential_ids"] if credential_id in available_ids]
            if not filtered:
                raise HTTPException(status_code=400, detail="No valid credentials were provided for this project.")
            binding["credential_ids"] = filtered[:1] if binding["mode"] == PROJECT_CREDENTIAL_BINDING_MODE_SINGLE else filtered
        else:
            binding["credential_ids"] = []

        project["credential_binding"] = binding
        project["updated_at"] = _iso_now()
        state["projects"][project_id] = project
        self.save(state, reason="project_credential_binding_updated")
        self._emit_state_changed(reason="project_credential_binding_updated")
        return self.project_credential_binding_payload(project_id)

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
            LOGGER.debug("Project build snapshot command succeeded for project=%s snapshot=%s", project_id, snapshot_tag)
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
        create_request_id: str | None = None,
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
            "status": CHAT_STATUS_STOPPED,
            "status_reason": CHAT_STATUS_REASON_CHAT_CREATED,
            "last_status_transition_at": now,
            "start_error": "",
            "last_exit_code": None,
            "last_exit_at": "",
            "stop_requested_at": "",
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
            "agent_tools_token_hash": "",
            "agent_tools_token_issued_at": "",
            "create_request_id": _compact_whitespace(str(create_request_id or "")).strip(),
            "created_at": now,
            "updated_at": now,
        }
        state["chats"][chat_id] = chat
        self.save(state, reason=CHAT_STATUS_REASON_CHAT_CREATED)
        LOGGER.info(
            "Chat state transition chat_id=%s from=%s to=%s reason=%s",
            chat_id,
            "missing",
            CHAT_STATUS_STOPPED,
            CHAT_STATUS_REASON_CHAT_CREATED,
        )
        return chat

    def create_and_start_chat(
        self,
        project_id: str,
        agent_args: list[str] | None = None,
        agent_type: str | None = None,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        state = self.load()
        project = state["projects"].get(project_id)
        if project is None:
            raise HTTPException(status_code=404, detail="Project not found.")
        build_status = str(project.get("build_status") or "")
        if build_status != "ready":
            raise HTTPException(status_code=409, detail="Project image is still being built. Save settings and wait.")
        normalized_agent_args = [str(arg) for arg in (agent_args or []) if str(arg).strip()]
        resolved_agent_type = (
            self.default_chat_agent_type()
            if agent_type is None
            else _normalize_chat_agent_type(agent_type)
        )
        normalized_agent_args = _apply_default_model_for_agent(resolved_agent_type, normalized_agent_args)
        normalized_request_id = _compact_whitespace(str(request_id or "")).strip()
        if normalized_request_id:
            existing_chat = self._chat_for_create_request(
                state=state,
                project_id=project_id,
                request_id=normalized_request_id,
            )
            if existing_chat is not None:
                LOGGER.info(
                    "Reused existing chat for create request project_id=%s request_id=%s chat_id=%s",
                    project_id,
                    normalized_request_id,
                    existing_chat.get("id"),
                )
                return existing_chat
        create_chat_kwargs: dict[str, Any] = {
            "profile": "",
            "ro_mounts": list(project.get("default_ro_mounts") or []),
            "rw_mounts": list(project.get("default_rw_mounts") or []),
            "env_vars": list(project.get("default_env_vars") or []),
            "agent_args": normalized_agent_args,
            "agent_type": resolved_agent_type,
        }
        if normalized_request_id:
            create_chat_kwargs["create_request_id"] = normalized_request_id
        chat = self.create_chat(
            project_id,
            **create_chat_kwargs,
        )
        try:
            return self.start_chat(chat["id"])
        except Exception as exc:
            detail = self._chat_start_error_detail(exc)
            LOGGER.warning(
                "New chat failed to start chat_id=%s project_id=%s reason=%s detail=%s",
                chat["id"],
                project_id,
                "chat_start_failed_during_create",
                detail,
            )
            failed_chat = self.chat(chat["id"])
            if failed_chat is None or _normalize_chat_status(failed_chat.get("status")) != CHAT_STATUS_FAILED:
                failed_chat = self._mark_chat_start_failed(
                    chat["id"],
                    detail=detail,
                    reason="chat_start_failed_during_create",
                )
            if failed_chat is not None:
                return failed_chat
            raise

    @staticmethod
    def _chat_for_create_request(
        state: dict[str, Any],
        project_id: str,
        request_id: str,
    ) -> dict[str, Any] | None:
        normalized_project_id = str(project_id or "").strip()
        normalized_request_id = _compact_whitespace(str(request_id or "")).strip()
        if not normalized_project_id or not normalized_request_id:
            return None
        for chat in state.get("chats", {}).values():
            if not isinstance(chat, dict):
                continue
            if str(chat.get("project_id") or "").strip() != normalized_project_id:
                continue
            if _compact_whitespace(str(chat.get("create_request_id") or "")).strip() != normalized_request_id:
                continue
            return dict(chat)
        return None

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
            stop_requested_at = _iso_now()
            chat["stop_requested_at"] = stop_requested_at
            chat["status_reason"] = CHAT_STATUS_REASON_USER_CLOSED_TAB
            chat["updated_at"] = stop_requested_at
            local_state["chats"][chat_id] = chat
            self.save(local_state, reason=CHAT_STATUS_REASON_USER_CLOSED_TAB)
            _stop_process(pid)
        self._close_runtime(chat_id)

        workspace = Path(str(chat.get("workspace") or self.chat_dir / chat_id))
        if workspace.exists():
            self._delete_path(workspace)
        chat_artifact_storage = self._chat_artifact_storage_root(chat_id)
        if chat_artifact_storage.exists():
            self._delete_path(chat_artifact_storage)
        runtime_config_file = self._chat_runtime_config_path(chat_id)
        if runtime_config_file.exists():
            try:
                runtime_config_file.unlink()
            except OSError as exc:
                raise HTTPException(status_code=500, detail=f"Failed to remove chat runtime config: {runtime_config_file}") from exc

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
            exit_code: int | None = None
            if runtime:
                polled_exit_code = runtime.process.poll()
                if isinstance(polled_exit_code, int):
                    exit_code = polled_exit_code
                runtime.listeners.clear()
            try:
                os.close(master_fd)
            except OSError:
                pass
            for listener in listeners:
                self._queue_put(listener, None)
            if runtime is not None:
                self._record_chat_runtime_exit(
                    chat_id,
                    exit_code,
                    reason="chat_runtime_reader_completed",
                )

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

    def _delete_fs_entry(self, path: Path) -> None:
        if path.is_symlink():
            try:
                path.unlink()
                return
            except FileNotFoundError:
                return
            except OSError as exc:
                raise HTTPException(status_code=500, detail=f"Failed to delete symlink {path}: {exc}") from exc
        if not path.exists():
            return
        if path.is_dir():
            self._delete_path(path)
            return
        try:
            path.unlink()
        except OSError as exc:
            raise HTTPException(status_code=500, detail=f"Failed to delete file {path}: {exc}") from exc

    def _managed_chat_workspace_paths(self, state: dict[str, Any]) -> set[Path]:
        managed_paths: set[Path] = set()
        chat_root = self.chat_dir.resolve()
        chats = state.get("chats")
        if not isinstance(chats, dict):
            return managed_paths

        for chat_id, chat in chats.items():
            if not isinstance(chat, dict):
                continue
            workspace = Path(str(chat.get("workspace") or self.chat_dir / str(chat_id)))
            try:
                resolved_workspace = workspace.resolve()
                resolved_workspace.relative_to(chat_root)
            except (OSError, RuntimeError, ValueError):
                continue
            managed_paths.add(resolved_workspace)
        return managed_paths

    def _managed_chat_artifact_paths(self, state: dict[str, Any]) -> set[Path]:
        managed_paths: set[Path] = set()
        artifacts_root = self.chat_artifacts_dir.resolve()
        chats = state.get("chats")
        if not isinstance(chats, dict):
            return managed_paths

        for chat_id in chats.keys():
            artifact_dir = self._chat_artifact_storage_root(str(chat_id))
            try:
                resolved_artifact_dir = artifact_dir.resolve()
                resolved_artifact_dir.relative_to(artifacts_root)
            except (OSError, RuntimeError, ValueError):
                continue
            managed_paths.add(resolved_artifact_dir)
        return managed_paths

    def _managed_project_workspace_paths(self, state: dict[str, Any]) -> set[Path]:
        managed_paths: set[Path] = set()
        project_root = self.project_dir.resolve()
        projects = state.get("projects")
        if not isinstance(projects, dict):
            return managed_paths

        for project_id in projects.keys():
            workspace = self.project_workdir(str(project_id))
            try:
                resolved_workspace = workspace.resolve()
                resolved_workspace.relative_to(project_root)
            except (OSError, RuntimeError, ValueError):
                continue
            managed_paths.add(resolved_workspace)
        return managed_paths

    def _remove_orphan_children(self, root_dir: Path, managed_paths: set[Path]) -> int:
        if not root_dir.exists():
            return 0
        removed = 0
        for child in root_dir.iterdir():
            try:
                resolved_child = child.resolve()
            except (OSError, RuntimeError):
                resolved_child = child
            if resolved_child in managed_paths:
                continue
            self._delete_fs_entry(child)
            removed += 1
        return removed

    def _remove_orphan_log_entries(self, state: dict[str, Any]) -> int:
        if not self.log_dir.exists():
            return 0
        expected_log_names: set[str] = set()
        projects = state.get("projects")
        if isinstance(projects, dict):
            for project_id in projects.keys():
                expected_log_names.add(f"project-{project_id}.log")
        chats = state.get("chats")
        if isinstance(chats, dict):
            for chat_id in chats.keys():
                expected_log_names.add(f"{chat_id}.log")

        removed = 0
        for entry in self.log_dir.iterdir():
            if entry.name in expected_log_names and entry.is_file():
                continue
            self._delete_fs_entry(entry)
            removed += 1
        return removed

    def _reconcile_startup_chat_runtime_state(self, state: dict[str, Any]) -> tuple[int, int, bool]:
        chats = state.get("chats")
        if not isinstance(chats, dict):
            return 0, 0, False

        stopped_chat_processes = 0
        reconciled_chats = 0
        changed = False
        for chat_id, chat in chats.items():
            if not isinstance(chat, dict):
                continue
            pid = chat.get("pid")
            has_pid = isinstance(pid, int)
            process_running = bool(has_pid and _is_process_running(pid))
            if process_running and isinstance(pid, int):
                _stop_process(pid)
                stopped_chat_processes += 1

            normalized_status = _normalize_chat_status(chat.get("status"))
            status_requires_failure = normalized_status in {CHAT_STATUS_RUNNING, CHAT_STATUS_STARTING}
            if not has_pid and not status_requires_failure:
                continue

            if status_requires_failure:
                self._transition_chat_status(
                    chat_id,
                    chat,
                    CHAT_STATUS_FAILED,
                    CHAT_STATUS_REASON_STARTUP_RECONCILE_ORPHAN_PROCESS
                    if has_pid
                    else CHAT_STATUS_REASON_STARTUP_RECONCILE_PROCESS_MISSING,
                )
                if not str(chat.get("start_error") or "").strip():
                    chat["start_error"] = (
                        "Recovered from stale chat runtime state during startup."
                        if has_pid
                        else "Chat runtime process was missing during startup reconciliation."
                    )

            if has_pid:
                chat["pid"] = None
                chat["last_exit_code"] = _normalize_optional_int(chat.get("last_exit_code"))
                chat["last_exit_at"] = _iso_now()
            else:
                chat["last_exit_code"] = _normalize_optional_int(chat.get("last_exit_code"))
                if not str(chat.get("last_exit_at") or "").strip():
                    chat["last_exit_at"] = _iso_now()
            chat["artifact_publish_token_hash"] = ""
            chat["artifact_publish_token_issued_at"] = ""
            chat["agent_tools_token_hash"] = ""
            chat["agent_tools_token_issued_at"] = ""
            chat["stop_requested_at"] = ""
            chat["updated_at"] = _iso_now()
            state["chats"][chat_id] = chat
            changed = True
            reconciled_chats += 1
        return stopped_chat_processes, reconciled_chats, changed

    def startup_reconcile(self) -> dict[str, int]:
        state = self.load()
        stopped_chat_processes, reconciled_chats, state_changed = self._reconcile_startup_chat_runtime_state(state)
        if state_changed:
            self.save(state, reason="startup_reconcile")

        removed_orphan_chat_paths = self._remove_orphan_children(
            self.chat_dir,
            self._managed_chat_workspace_paths(state),
        )
        self._remove_orphan_children(
            self.chat_artifacts_dir,
            self._managed_chat_artifact_paths(state),
        )
        removed_orphan_project_paths = self._remove_orphan_children(
            self.project_dir,
            self._managed_project_workspace_paths(state),
        )
        removed_orphan_log_entries = self._remove_orphan_log_entries(state)
        removed_stale_docker_containers = _docker_remove_stale_containers(STARTUP_STALE_DOCKER_CONTAINER_PREFIXES)

        return {
            "stopped_chat_processes": stopped_chat_processes,
            "reconciled_chats": reconciled_chats,
            "removed_orphan_chat_paths": removed_orphan_chat_paths,
            "removed_orphan_project_paths": removed_orphan_project_paths,
            "removed_orphan_log_entries": removed_orphan_log_entries,
            "removed_stale_docker_containers": removed_stale_docker_containers,
        }

    def _startup_reconcile_worker(self) -> None:
        try:
            summary = self.startup_reconcile()
        except Exception:
            LOGGER.exception("Startup reconciliation failed.")
            return
        LOGGER.info(
            "Startup reconciliation completed: "
            "stopped_chat_processes=%d reconciled_chats=%d "
            "removed_orphan_chat_paths=%d removed_orphan_project_paths=%d "
            "removed_orphan_log_entries=%d removed_stale_docker_containers=%d",
            summary["stopped_chat_processes"],
            summary["reconciled_chats"],
            summary["removed_orphan_chat_paths"],
            summary["removed_orphan_project_paths"],
            summary["removed_orphan_log_entries"],
            summary["removed_stale_docker_containers"],
        )

    def schedule_startup_reconcile(self) -> None:
        with self._startup_reconcile_lock:
            if self._startup_reconcile_scheduled:
                return
            self._startup_reconcile_scheduled = True
            worker = Thread(target=self._startup_reconcile_worker, daemon=True, name="agent-hub-startup-reconcile")
            self._startup_reconcile_thread = worker
            worker.start()

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

        for path in [self.chat_dir, self.project_dir, self.log_dir, self.artifacts_dir]:
            if path.exists():
                self._delete_path(path)
            path.mkdir(parents=True, exist_ok=True)
        self.chat_artifacts_dir.mkdir(parents=True, exist_ok=True)
        self.session_artifacts_dir.mkdir(parents=True, exist_ok=True)

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
        git_env = self._github_git_env_for_repo(
            str(project.get("repo_url") or ""),
            project=project,
            context_key=f"chat_clone:{chat.get('id')}",
        )
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
        git_env = self._github_git_env_for_repo(
            str(project.get("repo_url") or ""),
            project=project,
            context_key=f"project_clone:{project.get('id')}",
        )
        _run(["git", "clone", project["repo_url"], str(workspace)], check=True, env=git_env)
        return workspace

    def _sync_checkout_to_remote(self, workspace: Path, project: dict[str, Any]) -> None:
        git_env = self._github_git_env_for_repo(
            str(project.get("repo_url") or ""),
            project=project,
            context_key=f"project_sync:{project.get('id')}",
        )
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
        base_value = _normalize_base_image_value(base_mode, project.get("base_image_value"))

        if base_mode == "tag":
            return "base-image", base_value
        if not base_value:
            raise HTTPException(
                status_code=400,
                detail="base_image_value is required when base_image_mode is 'repo_path'.",
            )

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
        normalized_base_mode = _normalize_base_image_mode(project.get("base_image_mode"))
        normalized_base_value = _normalize_base_image_value(
            normalized_base_mode,
            project.get("base_image_value"),
        )
        payload = json.dumps(
            {
                "snapshot_schema_version": _snapshot_schema_version(),
                "project_id": project.get("id"),
                "setup_script": str(project.get("setup_script") or ""),
                "base_mode": normalized_base_mode,
                "base_value": normalized_base_value,
                "default_ro_mounts": list(project.get("default_ro_mounts") or []),
                "default_rw_mounts": list(project.get("default_rw_mounts") or []),
                "default_env_vars": list(project.get("default_env_vars") or []),
                "agent_cli_runtime_inputs_fingerprint": _agent_cli_runtime_inputs_fingerprint(),
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
        on_output: Callable[[str], None] | None = None,
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
                if on_output is not None:
                    on_output(line)
            return snapshot_tag

        repo_url = str(project.get("repo_url") or "")
        cmd = self._prepare_agent_cli_command(
            workspace=workspace,
            container_project_name=_container_project_name(project.get("name") or project.get("id")),
            runtime_config_file=self.config_file,
            agent_type=DEFAULT_CHAT_AGENT_TYPE,
            agent_tools_url=f"{self.artifact_publish_base_url}/api/projects/{resolved_project_id}/agent-tools",
            agent_tools_token="snapshot-token",
            agent_tools_project_id=resolved_project_id,
            repo_url=repo_url,
            project=project,
            snapshot_tag=snapshot_tag,
            ro_mounts=project.get("default_ro_mounts"),
            rw_mounts=project.get("default_rw_mounts"),
            env_vars=project.get("default_env_vars"),
            setup_script=setup_script,
            prepare_snapshot_only=True,
            context_key=f"snapshot:{project.get('id')}",
        )
        if log_path is None:
            _run(cmd, check=True)
        else:
            emit_build_output: Callable[[str], None] | None = None
            if resolved_project_id or on_output is not None:

                def emit_build_output(chunk: str) -> None:
                    if resolved_project_id:
                        self._emit_project_build_log(resolved_project_id, chunk)
                    if on_output is not None:
                        on_output(chunk)

            _run_logged(
                cmd,
                log_path=log_path,
                check=True,
                on_output=emit_build_output,
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

    def _chat_container_outdated_state(
        self,
        *,
        chat: dict[str, Any],
        project: dict[str, Any],
        is_running: bool,
    ) -> tuple[bool, str]:
        if not is_running or not isinstance(project, dict):
            return False, ""

        latest_snapshot = str(project.get("setup_snapshot_image") or "").strip()
        expected_snapshot = self._project_setup_snapshot_tag(project)
        build_status = str(project.get("build_status") or "").strip().lower()
        if build_status != "ready" or not latest_snapshot or latest_snapshot != expected_snapshot:
            return False, ""

        active_snapshot = str(chat.get("setup_snapshot_image") or "").strip()
        if not active_snapshot or active_snapshot == latest_snapshot:
            return False, ""

        reason = (
            f"Running on setup snapshot '{active_snapshot}' while project is ready on '{latest_snapshot}'. "
            "Refresh to restart on the latest container and resume chat context."
        )
        return True, reason

    @staticmethod
    def _resume_agent_args(agent_type: str, agent_args: list[str]) -> list[str]:
        normalized_args = [str(arg) for arg in agent_args if str(arg).strip()]
        if agent_type == AGENT_TYPE_CLAUDE:
            if _has_cli_option(normalized_args, long_option="--continue") or _has_cli_option(
                normalized_args, long_option="--resume"
            ):
                return normalized_args
        if agent_type == AGENT_TYPE_GEMINI:
            if _has_cli_option(normalized_args, long_option="--resume", short_option="-r"):
                return normalized_args

        resume_args = list(AGENT_RESUME_ARGS_BY_TYPE.get(agent_type, ()))
        if not resume_args:
            return normalized_args
        return [*resume_args, *normalized_args]

    def state_payload(self) -> dict[str, Any]:
        state = self.load()
        project_map: dict[str, dict[str, Any]] = {}
        should_save = False
        for pid, project in state["projects"].items():
            project_copy = dict(project)
            normalized_base_mode = _normalize_base_image_mode(project_copy.get("base_image_mode"))
            normalized_base_value = _normalize_base_image_value(
                normalized_base_mode,
                project_copy.get("base_image_value"),
            )
            project_copy["base_image_mode"] = normalized_base_mode
            project_copy["base_image_value"] = normalized_base_value
            project_copy["default_ro_mounts"] = list(project_copy.get("default_ro_mounts") or [])
            project_copy["default_rw_mounts"] = list(project_copy.get("default_rw_mounts") or [])
            project_copy["default_env_vars"] = list(project_copy.get("default_env_vars") or [])
            project_copy["setup_snapshot_image"] = str(project_copy.get("setup_snapshot_image") or "")
            project_copy["build_status"] = str(project_copy.get("build_status") or "pending")
            project_copy["build_error"] = str(project_copy.get("build_error") or "")
            project_copy["build_started_at"] = str(project_copy.get("build_started_at") or "")
            project_copy["build_finished_at"] = str(project_copy.get("build_finished_at") or "")
            normalized_binding = _normalize_project_credential_binding(project_copy.get("credential_binding"))
            project_copy["credential_binding"] = normalized_binding
            if state["projects"].get(pid, {}).get("base_image_mode") != normalized_base_mode:
                state["projects"][pid]["base_image_mode"] = normalized_base_mode
                should_save = True
            if str(state["projects"].get(pid, {}).get("base_image_value") or "").strip() != normalized_base_value:
                state["projects"][pid]["base_image_value"] = normalized_base_value
                should_save = True
            if state["projects"].get(pid, {}).get("credential_binding") != normalized_binding:
                state["projects"][pid]["credential_binding"] = normalized_binding
                should_save = True
            log_path = self.project_build_log(pid)
            try:
                project_copy["has_build_log"] = log_path.exists() and log_path.stat().st_size > 0
            except OSError:
                project_copy["has_build_log"] = False
            project_map[pid] = project_copy
        chats = []
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
            chat_copy.pop("agent_tools_token_hash", None)
            chat_copy.pop("agent_tools_token_issued_at", None)
            chat_copy.pop("create_request_id", None)
            running = _is_process_running(pid)
            normalized_status = _normalize_chat_status(chat_copy.get("status"))
            if running:
                if normalized_status != CHAT_STATUS_RUNNING and chat_id in state["chats"]:
                    self._transition_chat_status(
                        chat_id,
                        state["chats"][chat_id],
                        CHAT_STATUS_RUNNING,
                        "chat_process_running_during_state_refresh",
                    )
                    should_save = True
                    persisted_chat = state["chats"][chat_id]
                    chat_copy["status"] = persisted_chat.get("status")
                    chat_copy["status_reason"] = persisted_chat.get("status_reason")
                    chat_copy["last_status_transition_at"] = persisted_chat.get("last_status_transition_at")
                    chat_copy["updated_at"] = persisted_chat.get("updated_at")
                chat_copy["status"] = CHAT_STATUS_RUNNING
            else:
                self._close_runtime(chat_id)
                was_running = normalized_status in {CHAT_STATUS_RUNNING, CHAT_STATUS_STARTING} or isinstance(pid, int)
                if was_running and chat_id in state["chats"]:
                    persisted_chat = state["chats"][chat_id]
                    self._transition_chat_status(
                        chat_id,
                        persisted_chat,
                        CHAT_STATUS_FAILED,
                        "chat_process_not_running_during_state_refresh",
                    )
                    if not str(persisted_chat.get("start_error") or "").strip():
                        persisted_chat["start_error"] = "Chat process exited unexpectedly."
                    persisted_chat["pid"] = None
                    persisted_chat["artifact_publish_token_hash"] = ""
                    persisted_chat["artifact_publish_token_issued_at"] = ""
                    persisted_chat["agent_tools_token_hash"] = ""
                    persisted_chat["agent_tools_token_issued_at"] = ""
                    persisted_chat["last_exit_code"] = _normalize_optional_int(persisted_chat.get("last_exit_code"))
                    if not str(persisted_chat.get("last_exit_at") or "").strip():
                        persisted_chat["last_exit_at"] = _iso_now()
                    persisted_chat["stop_requested_at"] = ""
                    state["chats"][chat_id] = persisted_chat
                    chat_copy["status"] = persisted_chat.get("status")
                    chat_copy["status_reason"] = persisted_chat.get("status_reason")
                    chat_copy["last_status_transition_at"] = persisted_chat.get("last_status_transition_at")
                    chat_copy["updated_at"] = persisted_chat.get("updated_at")
                    chat_copy["start_error"] = persisted_chat.get("start_error")
                    chat_copy["last_exit_code"] = persisted_chat.get("last_exit_code")
                    chat_copy["last_exit_at"] = persisted_chat.get("last_exit_at")
                    chat_copy["stop_requested_at"] = persisted_chat.get("stop_requested_at")
                    chat_copy["pid"] = None
                    should_save = True
                else:
                    chat_copy["status"] = normalized_status
                    if chat_copy.get("pid") is not None:
                        chat_copy["pid"] = None
                        if chat_id in state["chats"]:
                            state["chats"][chat_id]["pid"] = None
                            state["chats"][chat_id]["updated_at"] = _iso_now()
                            should_save = True
            chat_copy["is_running"] = running
            chat_copy["container_workspace"] = str(chat_copy.get("container_workspace") or "") or _container_workspace_path_for_project(
                project_name
            )
            chat_copy["project_name"] = project_name
            is_outdated, outdated_reason = self._chat_container_outdated_state(
                chat=chat_copy,
                project=project_for_chat,
                is_running=running,
            )
            chat_copy["container_outdated"] = is_outdated
            chat_copy["container_outdated_reason"] = outdated_reason
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

        if should_save:
            self.save(state, reason="state_payload_reconcile")

        state["chats"] = chats
        state["projects"] = list(project_map.values())
        state["settings"] = _normalize_hub_settings_payload(state.get("settings"))
        return state

    def start_chat(self, chat_id: str, *, resume: bool = False) -> dict[str, Any]:
        state = self.load()
        chat = state["chats"].get(chat_id)
        if chat is None:
            raise HTTPException(status_code=404, detail="Chat not found.")
        project = state["projects"].get(chat["project_id"])
        if project is None:
            raise HTTPException(status_code=404, detail="Parent project missing.")

        if _normalize_chat_status(chat.get("status")) == CHAT_STATUS_RUNNING and _is_process_running(chat.get("pid")):
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

        self._transition_chat_status(chat_id, chat, CHAT_STATUS_STARTING, "chat_start_requested")
        chat["start_error"] = ""
        chat["last_exit_code"] = None
        chat["last_exit_at"] = ""
        chat["stop_requested_at"] = ""
        chat["pid"] = None
        state["chats"][chat_id] = chat
        self.save(state, reason="chat_start_requested")

        try:
            workspace = self._ensure_chat_clone(chat, project)
            self._sync_checkout_to_remote(workspace, project)
            with self._chat_input_lock:
                self._chat_input_buffers[chat_id] = ""
                self._chat_input_ansi_carry[chat_id] = ""
            artifact_publish_token = _new_artifact_publish_token()
            agent_tools_token = _new_agent_tools_token()
            agent_tools_url = self._chat_agent_tools_url(chat_id)
            agent_tools_project_id = str(project.get("id") or "")
            agent_type = _normalize_chat_agent_type(chat.get("agent_type"))
            runtime_config_file = self._prepare_chat_runtime_config(
                chat_id,
                agent_type=agent_type,
                agent_tools_url=agent_tools_url,
                agent_tools_token=agent_tools_token,
                agent_tools_project_id=agent_tools_project_id,
                agent_tools_chat_id=chat_id,
            )
            agent_command = AGENT_COMMAND_BY_TYPE.get(agent_type, AGENT_COMMAND_BY_TYPE[DEFAULT_CHAT_AGENT_TYPE])
            chat["agent_type"] = agent_type
            container_workspace = _container_workspace_path_for_project(project.get("name") or project.get("id"))

            agent_args = [str(arg) for arg in (chat.get("agent_args") or []) if str(arg).strip()]
            if resume and agent_type == AGENT_TYPE_CODEX:
                # agent_cli resume mode and explicit args are mutually exclusive.
                agent_args = []
            elif resume:
                agent_args = self._resume_agent_args(agent_type, agent_args)

            cmd = self._prepare_agent_cli_command(
                workspace=workspace,
                container_project_name=_container_project_name(project.get("name") or project.get("id")),
                runtime_config_file=runtime_config_file,
                agent_type=agent_type,
                agent_tools_url=agent_tools_url,
                agent_tools_token=agent_tools_token,
                agent_tools_project_id=agent_tools_project_id,
                agent_tools_chat_id=chat_id,
                repo_url=str(project.get("repo_url") or ""),
                project=project,
                snapshot_tag=snapshot_tag,
                ro_mounts=chat.get("ro_mounts"),
                rw_mounts=chat.get("rw_mounts"),
                env_vars=chat.get("env_vars"),
                artifacts_url=self._chat_artifact_publish_url(chat_id),
                artifacts_token=artifact_publish_token,
                resume=resume,
                context_key=f"chat_start:{chat_id}",
                extra_args=agent_args,
            )

            proc = self._spawn_chat_process(chat_id, cmd)
        except Exception as exc:
            detail = self._chat_start_error_detail(exc)
            LOGGER.warning(
                "Chat failed to start chat_id=%s project_id=%s reason=%s detail=%s",
                chat_id,
                chat.get("project_id"),
                "chat_start_failed",
                detail,
            )
            self._mark_chat_start_failed(chat_id, detail=detail, reason="chat_start_failed")
            raise

        state = self.load()
        chat = state["chats"].get(chat_id)
        if chat is None:
            raise HTTPException(status_code=404, detail="Chat was removed before start completion.")
        self._transition_chat_status(chat_id, chat, CHAT_STATUS_RUNNING, "chat_start_succeeded")
        chat["start_error"] = ""
        chat["pid"] = proc.pid
        chat["setup_snapshot_image"] = snapshot_tag or ""
        chat["container_workspace"] = container_workspace
        chat["artifact_publish_token_hash"] = _hash_artifact_publish_token(artifact_publish_token)
        chat["artifact_publish_token_issued_at"] = _iso_now()
        chat["agent_tools_token_hash"] = _hash_agent_tools_token(agent_tools_token)
        chat["agent_tools_token_issued_at"] = _iso_now()
        chat["last_started_at"] = _iso_now()
        chat["stop_requested_at"] = ""
        state["chats"][chat_id] = chat
        self.save(state, reason="chat_start_succeeded")
        return dict(chat)

    def refresh_chat_container(self, chat_id: str) -> dict[str, Any]:
        state = self.load()
        chat = state["chats"].get(chat_id)
        if chat is None:
            raise HTTPException(status_code=404, detail="Chat not found.")
        project = state["projects"].get(chat["project_id"])
        if project is None:
            raise HTTPException(status_code=404, detail="Parent project missing.")

        running = bool(chat.get("status") == "running" and _is_process_running(chat.get("pid")))
        if not running:
            raise HTTPException(status_code=409, detail="Chat must be running to refresh its container.")

        is_outdated, _reason = self._chat_container_outdated_state(chat=chat, project=project, is_running=running)
        if not is_outdated:
            raise HTTPException(status_code=409, detail="Chat container is already up to date.")

        self.close_chat(chat_id)
        return self.start_chat(chat_id, resume=True)

    def close_chat(self, chat_id: str) -> dict[str, Any]:
        state = self.load()
        chat = state["chats"].get(chat_id)
        if chat is None:
            raise HTTPException(status_code=404, detail="Chat not found.")

        stop_requested_at = _iso_now()
        chat["stop_requested_at"] = stop_requested_at
        chat["status_reason"] = CHAT_STATUS_REASON_CHAT_CLOSE_REQUESTED
        chat["updated_at"] = stop_requested_at
        state["chats"][chat_id] = chat
        self.save(state, reason=CHAT_STATUS_REASON_CHAT_CLOSE_REQUESTED)
        pid = chat.get("pid")
        if isinstance(pid, int):
            _stop_process(pid)
        self._close_runtime(chat_id)
        with self._chat_input_lock:
            self._chat_input_buffers.pop(chat_id, None)
            self._chat_input_ansi_carry.pop(chat_id, None)

        self._transition_chat_status(chat_id, chat, CHAT_STATUS_STOPPED, CHAT_STATUS_REASON_CHAT_CLOSE_REQUESTED)
        chat["start_error"] = ""
        chat["pid"] = None
        chat["artifact_publish_token_hash"] = ""
        chat["artifact_publish_token_issued_at"] = ""
        chat["last_exit_code"] = None
        chat["last_exit_at"] = _iso_now()
        chat["stop_requested_at"] = ""
        state["chats"][chat_id] = chat
        self.save(state, reason=CHAT_STATUS_REASON_CHAT_CLOSE_REQUESTED)
        return dict(chat)


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
          <input id="project-base-image-value" placeholder="ubuntu:24.04" />
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
    const DEFAULT_BASE_IMAGE_TAG = 'ubuntu:24.04';

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
      return DEFAULT_BASE_IMAGE_TAG;
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
    state.schedule_startup_reconcile()

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

    @app.get("/api/settings")
    def api_settings() -> dict[str, Any]:
        return {"settings": state.settings_payload()}

    @app.patch("/api/settings")
    async def api_update_settings(request: Request) -> dict[str, Any]:
        payload = await request.json()
        if not isinstance(payload, dict):
            raise HTTPException(status_code=400, detail="Invalid JSON payload.")
        updated = state.update_settings(payload)
        return {"settings": updated}

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

    @app.post("/api/settings/auth/github-app/connect")
    async def api_connect_github_app(request: Request) -> dict[str, Any]:
        payload = await request.json()
        if not isinstance(payload, dict):
            raise HTTPException(status_code=400, detail="Invalid JSON payload.")
        return {"provider": state.connect_github_app(payload.get("installation_id"))}

    @app.post("/api/settings/auth/github-app/setup/start")
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

    @app.get("/api/settings/auth/github-app/setup/session")
    def api_github_app_setup_session() -> dict[str, Any]:
        return state.github_app_setup_session_payload()

    @app.get("/api/settings/auth/github-app/setup/callback", response_class=HTMLResponse)
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

    @app.post("/api/settings/auth/github-app/disconnect")
    def api_disconnect_github_app() -> dict[str, Any]:
        return {"provider": state.disconnect_github_app()}

    @app.get("/api/settings/auth/github-app/installations")
    def api_list_github_installations() -> dict[str, Any]:
        return state.list_github_app_installations()

    @app.post("/api/settings/auth/github-tokens/connect")
    async def api_connect_github_token(request: Request) -> dict[str, Any]:
        payload = await request.json()
        if not isinstance(payload, dict):
            raise HTTPException(status_code=400, detail="Invalid JSON payload.")
        return {
            "provider": state.connect_github_personal_access_token(
                payload.get("personal_access_token"),
                host=payload.get("host"),
            )
        }

    @app.delete("/api/settings/auth/github-tokens/{token_id}")
    def api_disconnect_github_personal_access_token(token_id: str) -> dict[str, Any]:
        return {"provider": state.disconnect_github_personal_access_token(token_id)}

    @app.post("/api/settings/auth/github-tokens/disconnect")
    def api_disconnect_github_personal_access_tokens() -> dict[str, Any]:
        return {"provider": state.disconnect_github_personal_access_tokens()}

    @app.post("/api/settings/auth/gitlab-tokens/connect")
    async def api_connect_gitlab_token(request: Request) -> dict[str, Any]:
        payload = await request.json()
        if not isinstance(payload, dict):
            raise HTTPException(status_code=400, detail="Invalid JSON payload.")
        return {
            "provider": state.connect_gitlab_personal_access_token(
                payload.get("personal_access_token"),
                host=payload.get("host"),
            )
        }

    @app.delete("/api/settings/auth/gitlab-tokens/{token_id}")
    def api_disconnect_gitlab_personal_access_token(token_id: str) -> dict[str, Any]:
        return {"provider": state.disconnect_gitlab_personal_access_token(token_id)}

    @app.post("/api/settings/auth/gitlab-tokens/disconnect")
    def api_disconnect_gitlab_personal_access_tokens() -> dict[str, Any]:
        return {"provider": state.disconnect_gitlab_personal_access_tokens()}

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
        agent_args = payload.get("agent_args")
        if agent_args is None:
            agent_args = []
        if not isinstance(agent_args, list):
            raise HTTPException(status_code=400, detail="agent_args must be an array.")
        agent_type = (
            _normalize_chat_agent_type(payload.get("agent_type"), strict=True)
            if "agent_type" in payload
            else AGENT_TYPE_CODEX
        )
        recommendation = await asyncio.to_thread(
            state.auto_configure_project,
            repo_url=payload.get("repo_url"),
            default_branch=payload.get("default_branch"),
            request_id=payload.get("request_id"),
            agent_type=agent_type,
            agent_args=[str(arg) for arg in agent_args if str(arg).strip()],
        )
        return {"recommendation": recommendation}

    @app.post("/api/projects/auto-configure/cancel")
    async def api_cancel_auto_configure_project(request: Request) -> dict[str, Any]:
        payload = await request.json()
        if not isinstance(payload, dict):
            raise HTTPException(status_code=400, detail="Invalid JSON payload.")
        return state.cancel_auto_configure_project(
            request_id=payload.get("request_id"),
        )

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
        credential_binding = _normalize_project_credential_binding(payload.get("credential_binding"))
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
            credential_binding=credential_binding,
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
        if "credential_binding" in payload:
            update["credential_binding"] = _normalize_project_credential_binding(payload.get("credential_binding"))
        if not update:
            raise HTTPException(status_code=400, detail="No patch values provided.")
        project = await asyncio.to_thread(state.update_project, project_id, update)
        return {"project": project}

    @app.get("/api/projects/{project_id}/credential-binding")
    def api_project_credential_binding(project_id: str) -> dict[str, Any]:
        return state.project_credential_binding_payload(project_id)

    @app.post("/api/projects/{project_id}/credential-binding")
    async def api_project_credential_binding_update(project_id: str, request: Request) -> dict[str, Any]:
        payload = await request.json()
        if not isinstance(payload, dict):
            raise HTTPException(status_code=400, detail="Invalid JSON payload.")
        return state.attach_project_credentials(
            project_id=project_id,
            mode=payload.get("mode"),
            credential_ids=payload.get("credential_ids"),
            source="settings_api",
        )

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
        request_id_raw = payload.get("request_id")
        request_id = _compact_whitespace(str(request_id_raw or "")).strip()
        agent_type = (
            _normalize_chat_agent_type(payload.get("agent_type"), strict=True)
            if "agent_type" in payload
            else state.default_chat_agent_type()
        )
        start_kwargs: dict[str, Any] = {
            "agent_args": [str(arg) for arg in agent_args],
            "agent_type": agent_type,
        }
        if request_id:
            start_kwargs["request_id"] = request_id
        chat = await asyncio.to_thread(
            state.create_and_start_chat,
            project_id,
            **start_kwargs,
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
            else state.default_chat_agent_type()
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

    @app.post("/api/chats/{chat_id}/refresh-container")
    def api_refresh_chat_container(chat_id: str) -> dict[str, Any]:
        return {"chat": state.refresh_chat_container(chat_id)}

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

        chat = state.chat(chat_id)
        if chat is None:
            raise HTTPException(status_code=404, detail="Chat not found.")
        state._require_artifact_publish_token(chat, token)
        workspace = state.chat_workdir(chat_id).resolve()
        if not workspace.exists():
            raise HTTPException(status_code=409, detail="Chat workspace is unavailable.")
        payload, staged_paths = await _parse_artifact_request_payload(
            request,
            context=f"/api/chats/{chat_id}/artifacts/publish",
            workspace=workspace,
        )
        try:
            artifact = state.publish_chat_artifact(
                chat_id=chat_id,
                token=token,
                submitted_path=payload.get("path"),
                name=payload.get("name"),
            )
        except HTTPException as exc:
            LOGGER.warning(
                "artifacts publish failed for chat_id=%s: %s",
                chat_id,
                exc.detail,
            )
            raise
        finally:
            _cleanup_uploaded_artifact_paths(staged_paths)
        return {"artifact": artifact}

    @app.get("/api/chats/{chat_id}/artifacts/{artifact_id}/download")
    def api_download_chat_artifact(chat_id: str, artifact_id: str) -> FileResponse:
        artifact_path, filename, media_type = state.resolve_chat_artifact_download(chat_id, artifact_id)
        return FileResponse(path=str(artifact_path), filename=filename, media_type=media_type)

    @app.get("/api/chats/{chat_id}/artifacts/{artifact_id}/preview")
    def api_preview_chat_artifact(chat_id: str, artifact_id: str) -> FileResponse:
        artifact_path, media_type = state.resolve_chat_artifact_preview(chat_id, artifact_id)
        return FileResponse(path=str(artifact_path), media_type=media_type)

    @app.get("/api/chats/{chat_id}/agent-tools/credentials")
    def api_agent_tools_list_credentials(chat_id: str, request: Request) -> dict[str, Any]:
        auth_header = str(request.headers.get("authorization") or "")
        token = ""
        if auth_header.lower().startswith("bearer "):
            token = auth_header[7:].strip()
        if not token:
            token = str(request.headers.get(AGENT_TOOLS_TOKEN_HEADER) or "").strip()
        chat = state.chat(chat_id)
        if chat is None:
            raise HTTPException(status_code=404, detail="Chat not found.")
        state._require_agent_tools_token(chat, token)
        return state.agent_tools_credentials_list_payload(chat_id)

    @app.post("/api/chats/{chat_id}/agent-tools/credentials/resolve")
    async def api_agent_tools_resolve_credentials(chat_id: str, request: Request) -> dict[str, Any]:
        auth_header = str(request.headers.get("authorization") or "")
        token = ""
        if auth_header.lower().startswith("bearer "):
            token = auth_header[7:].strip()
        if not token:
            token = str(request.headers.get(AGENT_TOOLS_TOKEN_HEADER) or "").strip()
        chat = state.chat(chat_id)
        if chat is None:
            raise HTTPException(status_code=404, detail="Chat not found.")
        state._require_agent_tools_token(chat, token)
        payload = await request.json()
        if payload is None:
            payload = {}
        if not isinstance(payload, dict):
            raise HTTPException(status_code=400, detail="Invalid JSON payload.")
        return state.resolve_agent_tools_credentials(
            chat_id=chat_id,
            mode=payload.get("mode"),
            credential_ids=payload.get("credential_ids"),
        )

    @app.post("/api/chats/{chat_id}/agent-tools/project-binding")
    async def api_agent_tools_attach_project_binding(chat_id: str, request: Request) -> dict[str, Any]:
        auth_header = str(request.headers.get("authorization") or "")
        token = ""
        if auth_header.lower().startswith("bearer "):
            token = auth_header[7:].strip()
        if not token:
            token = str(request.headers.get(AGENT_TOOLS_TOKEN_HEADER) or "").strip()
        chat = state.chat(chat_id)
        if chat is None:
            raise HTTPException(status_code=404, detail="Chat not found.")
        state._require_agent_tools_token(chat, token)
        payload = await request.json()
        if not isinstance(payload, dict):
            raise HTTPException(status_code=400, detail="Invalid JSON payload.")
        return state.attach_agent_tools_project_credentials(
            chat_id=chat_id,
            mode=payload.get("mode"),
            credential_ids=payload.get("credential_ids"),
        )

    @app.post("/api/chats/{chat_id}/agent-tools/artifacts/submit")
    async def api_agent_tools_submit_chat_artifact(chat_id: str, request: Request) -> dict[str, Any]:
        auth_header = str(request.headers.get("authorization") or "")
        token = ""
        if auth_header.lower().startswith("bearer "):
            token = auth_header[7:].strip()
        if not token:
            token = str(request.headers.get(AGENT_TOOLS_TOKEN_HEADER) or "").strip()

        chat = state.chat(chat_id)
        if chat is None:
            raise HTTPException(status_code=404, detail="Chat not found.")
        state._require_agent_tools_token(chat, token)
        workspace = state.chat_workdir(chat_id).resolve()
        if not workspace.exists():
            raise HTTPException(status_code=409, detail="Chat workspace is unavailable.")
        payload, staged_paths = await _parse_artifact_request_payload(
            request,
            context=f"/api/chats/{chat_id}/agent-tools/artifacts/submit",
            workspace=workspace,
        )
        try:
            artifact = state.submit_chat_artifact(
                chat_id=chat_id,
                token=token,
                submitted_path=payload.get("path"),
                name=payload.get("name"),
            )
        except HTTPException as exc:
            LOGGER.warning(
                "agent-tools artifact submit failed for chat_id=%s: %s",
                chat_id,
                exc.detail,
            )
            raise
        finally:
            _cleanup_uploaded_artifact_paths(staged_paths)
        return {"artifact": artifact}

    @app.get("/api/agent-tools/sessions/{session_id}/credentials")
    def api_agent_tools_session_list_credentials(session_id: str, request: Request) -> dict[str, Any]:
        auth_header = str(request.headers.get("authorization") or "")
        token = ""
        if auth_header.lower().startswith("bearer "):
            token = auth_header[7:].strip()
        if not token:
            token = str(request.headers.get(AGENT_TOOLS_TOKEN_HEADER) or "").strip()
        state.require_agent_tools_session_token(session_id, token)
        return state.agent_tools_session_credentials_list_payload(session_id)

    @app.post("/api/agent-tools/sessions/{session_id}/credentials/resolve")
    async def api_agent_tools_session_resolve_credentials(session_id: str, request: Request) -> dict[str, Any]:
        auth_header = str(request.headers.get("authorization") or "")
        token = ""
        if auth_header.lower().startswith("bearer "):
            token = auth_header[7:].strip()
        if not token:
            token = str(request.headers.get(AGENT_TOOLS_TOKEN_HEADER) or "").strip()
        state.require_agent_tools_session_token(session_id, token)
        payload = await request.json()
        if payload is None:
            payload = {}
        if not isinstance(payload, dict):
            raise HTTPException(status_code=400, detail="Invalid JSON payload.")
        return state.resolve_agent_tools_session_credentials(
            session_id=session_id,
            mode=payload.get("mode"),
            credential_ids=payload.get("credential_ids"),
        )

    @app.post("/api/agent-tools/sessions/{session_id}/project-binding")
    async def api_agent_tools_session_attach_project_binding(session_id: str, request: Request) -> dict[str, Any]:
        auth_header = str(request.headers.get("authorization") or "")
        token = ""
        if auth_header.lower().startswith("bearer "):
            token = auth_header[7:].strip()
        if not token:
            token = str(request.headers.get(AGENT_TOOLS_TOKEN_HEADER) or "").strip()
        state.require_agent_tools_session_token(session_id, token)
        payload = await request.json()
        if not isinstance(payload, dict):
            raise HTTPException(status_code=400, detail="Invalid JSON payload.")
        return state.attach_agent_tools_session_project_credentials(
            session_id=session_id,
            mode=payload.get("mode"),
            credential_ids=payload.get("credential_ids"),
        )

    @app.post("/api/agent-tools/sessions/{session_id}/artifacts/publish")
    async def api_publish_session_artifact(session_id: str, request: Request) -> dict[str, Any]:
        auth_header = str(request.headers.get("authorization") or "")
        token = ""
        if auth_header.lower().startswith("bearer "):
            token = auth_header[7:].strip()
        if not token:
            token = str(request.headers.get("x-agent-hub-artifact-token") or "").strip()

        session = state._agent_tools_session(session_id)
        state._require_session_artifact_publish_token(session, token)
        workspace = Path(str(session.get("workspace") or "")).resolve()
        if not workspace.exists():
            raise HTTPException(status_code=409, detail="Session workspace is unavailable.")
        payload, staged_paths = await _parse_artifact_request_payload(
            request,
            context=f"/api/agent-tools/sessions/{session_id}/artifacts/publish",
            workspace=workspace,
        )
        try:
            artifact = state.publish_session_artifact(
                session_id=session_id,
                token=token,
                submitted_path=payload.get("path"),
                name=payload.get("name"),
            )
        except HTTPException as exc:
            LOGGER.warning(
                "session artifact publish failed for session_id=%s: %s",
                session_id,
                exc.detail,
            )
            raise
        finally:
            _cleanup_uploaded_artifact_paths(staged_paths)
        return {"artifact": artifact}

    @app.post("/api/agent-tools/sessions/{session_id}/artifacts/submit")
    async def api_agent_tools_submit_session_artifact(session_id: str, request: Request) -> dict[str, Any]:
        auth_header = str(request.headers.get("authorization") or "")
        token = ""
        if auth_header.lower().startswith("bearer "):
            token = auth_header[7:].strip()
        if not token:
            token = str(request.headers.get(AGENT_TOOLS_TOKEN_HEADER) or "").strip()

        session = state.require_agent_tools_session_token(session_id, token)
        workspace = Path(str(session.get("workspace") or "")).resolve()
        if not workspace.exists():
            raise HTTPException(status_code=409, detail="Session workspace is unavailable.")
        payload, staged_paths = await _parse_artifact_request_payload(
            request,
            context=f"/api/agent-tools/sessions/{session_id}/artifacts/submit",
            workspace=workspace,
        )
        try:
            artifact = state.submit_session_artifact(
                session_id=session_id,
                token=token,
                submitted_path=payload.get("path"),
                name=payload.get("name"),
            )
        except HTTPException as exc:
            LOGGER.warning(
                "session artifact submit failed for session_id=%s: %s",
                session_id,
                exc.detail,
            )
            raise
        finally:
            _cleanup_uploaded_artifact_paths(staged_paths)
        return {"artifact": artifact}

    @app.get("/api/agent-tools/sessions/{session_id}/artifacts/{artifact_id}/download")
    def api_download_session_artifact(session_id: str, artifact_id: str) -> FileResponse:
        artifact_path, filename, media_type = state.resolve_session_artifact_download(session_id, artifact_id)
        return FileResponse(path=str(artifact_path), filename=filename, media_type=media_type)

    @app.get("/api/agent-tools/sessions/{session_id}/artifacts/{artifact_id}/preview")
    def api_preview_session_artifact(session_id: str, artifact_id: str) -> FileResponse:
        artifact_path, media_type = state.resolve_session_artifact_preview(session_id, artifact_id)
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
            try:
                await websocket.send_text(backlog)
            except WebSocketDisconnect:
                state._queue_put(listener, None)
                state.detach_terminal(chat_id, listener)
                return

        async def stream_output() -> None:
            while True:
                try:
                    chunk = await asyncio.to_thread(listener.get, True, 0.25)
                except queue.Empty:
                    continue
                if chunk is None:
                    break
                try:
                    await websocket.send_text(chunk)
                except WebSocketDisconnect:
                    break

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
