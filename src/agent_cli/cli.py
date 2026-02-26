from __future__ import annotations

import fcntl
import hashlib
import json
import os
import posixpath
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import tomllib
import urllib.parse
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path, PurePosixPath
from threading import Thread
from typing import Any, Iterable, Iterator, Tuple

import click

from agent_cli import providers as agent_providers


DEFAULT_BASE_IMAGE = "ubuntu:24.04"
DEFAULT_SETUP_RUNTIME_IMAGE = "agent-ubuntu2204-setup:latest"
DEFAULT_RUNTIME_IMAGE = "agent-ubuntu2204-codex:latest"
CLAUDE_RUNTIME_IMAGE = "agent-ubuntu2204-claude:latest"
GEMINI_RUNTIME_IMAGE = "agent-ubuntu2204-gemini:latest"
DEFAULT_DOCKERFILE = "docker/agent_cli/Dockerfile"
DEFAULT_AGENT_COMMAND = "codex"
DEFAULT_CONTAINER_HOME = "/workspace"
AGENT_PROVIDER_NONE = "none"
AGENT_PROVIDER_CODEX = "codex"
AGENT_PROVIDER_CLAUDE = "claude"
AGENT_PROVIDER_GEMINI = "gemini"
DEFAULT_CODEX_APPROVAL_POLICY = "never"
DEFAULT_CODEX_SANDBOX_MODE = "danger-full-access"
DEFAULT_CLAUDE_PERMISSION_MODE = "bypassPermissions"
DEFAULT_CLAUDE_MODEL = "opus"
DEFAULT_GEMINI_APPROVAL_MODE = "yolo"
GEMINI_CONTEXT_FILE_NAME = "GEMINI.md"
GEMINI_SETTINGS_FILE_NAME = "settings.json"
SYSTEM_PROMPT_FILE_NAME = "SYSTEM_PROMPT.md"
DOCKER_SOCKET_PATH = "/var/run/docker.sock"
TMP_DIR_TMPFS_SPEC = "/tmp:mode=1777,exec"
DEFAULT_RUNTIME_TERM = "xterm-256color"
DEFAULT_RUNTIME_COLORTERM = "truecolor"
GIT_CREDENTIALS_SOURCE_PATH = "/tmp/agent_hub_git_credentials_source"
GIT_CREDENTIALS_FILE_PATH = "/tmp/agent_hub_git_credentials"
AGENT_HUB_SECRETS_DIR_NAME = "secrets"
AGENT_HUB_GIT_CREDENTIALS_DIR_NAME = "git_credentials"
AGENT_HUB_DATA_DIR_ENV = "AGENT_HUB_DATA_DIR"
AGENT_TOOLS_URL_ENV = "AGENT_HUB_AGENT_TOOLS_URL"
AGENT_TOOLS_TOKEN_ENV = "AGENT_HUB_AGENT_TOOLS_TOKEN"
AGENT_TOOLS_PROJECT_ID_ENV = "AGENT_HUB_AGENT_TOOLS_PROJECT_ID"
AGENT_TOOLS_CHAT_ID_ENV = "AGENT_HUB_AGENT_TOOLS_CHAT_ID"
AGENT_TOOLS_TOKEN_HEADER = "x-agent-hub-agent-tools-token"
AGENT_TOOLS_MCP_RUNTIME_DIR_NAME = "agent_hub"
AGENT_TOOLS_MCP_RUNTIME_FILE_NAME = "agent_tools_mcp.py"
RECURSIVE_WORKSPACE_CHMOD_ENABLED = "1"
RECURSIVE_WORKSPACE_CHMOD_DISABLED = "0"
AGENT_TOOLS_MCP_CONTAINER_SCRIPT_PATH = str(
    PurePosixPath(DEFAULT_CONTAINER_HOME)
    / ".codex"
    / AGENT_TOOLS_MCP_RUNTIME_DIR_NAME
    / AGENT_TOOLS_MCP_RUNTIME_FILE_NAME
)
GIT_CREDENTIAL_DEFAULT_SCHEME = "https"
GIT_CREDENTIAL_ALLOWED_SCHEMES = {"http", "https"}
RUNTIME_IMAGE_BUILD_LOCK_DIR = Path(tempfile.gettempdir()) / "agent-cli-image-build-locks"


def _cli_arg_matches_option(arg: str, *, long_option: str, short_option: str | None = None) -> bool:
    if arg == long_option or arg.startswith(f"{long_option}="):
        return True
    if short_option and (arg == short_option or arg.startswith(f"{short_option}=")):
        return True
    return False


def _has_cli_option(args: Iterable[str], *, long_option: str, short_option: str | None = None) -> bool:
    return any(_cli_arg_matches_option(arg, long_option=long_option, short_option=short_option) for arg in args)


def _has_codex_config_override(args: Iterable[str], *, key: str) -> bool:
    parsed_args = [str(arg) for arg in args]
    for index, arg in enumerate(parsed_args):
        if not _cli_arg_matches_option(arg, long_option="--config", short_option="-c"):
            continue
        if arg in {"--config", "-c"}:
            if index + 1 >= len(parsed_args):
                continue
            config_assignment = parsed_args[index + 1]
        else:
            _, _, config_assignment = arg.partition("=")
        config_key, _, _ = config_assignment.partition("=")
        if config_key.strip() == key:
            return True
    return False


def _resolved_runtime_term(env: dict[str, str] | None = None) -> str:
    source = os.environ if env is None else env
    candidate = str(source.get("TERM", "")).strip()
    if not candidate or candidate.lower() == "dumb":
        return DEFAULT_RUNTIME_TERM
    return candidate


def _resolved_runtime_colorterm(env: dict[str, str] | None = None) -> str:
    source = os.environ if env is None else env
    candidate = str(source.get("COLORTERM", "")).strip()
    if not candidate:
        return DEFAULT_RUNTIME_COLORTERM
    return candidate


def _toml_basic_string_literal(value: str) -> str:
    return json.dumps(str(value or ""))


def _normalize_string_list(raw_value: object) -> list[str]:
    if not isinstance(raw_value, list):
        return []
    seen: set[str] = set()
    cleaned: list[str] = []
    for item in raw_value:
        value = str(item).strip()
        if not value or value in seen:
            continue
        cleaned.append(value)
        seen.add(value)
    return cleaned


def _read_system_prompt(system_prompt_path: Path) -> str:
    try:
        return system_prompt_path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError) as exc:
        raise click.ClickException(f"Unable to read system prompt file {system_prompt_path}: {exc}") from exc


def _shared_prompt_context_from_config(config_path: Path, *, core_system_prompt: str) -> str:
    sections: list[str] = []
    if core_system_prompt:
        sections.append(core_system_prompt)

    try:
        raw = config_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return "\n\n".join(section for section in sections if section)

    parsed: dict[str, Any] | None = None
    parse_error: Exception | None = None
    parse_strategies = (json.loads, tomllib.loads)

    for parser in parse_strategies:
        try:
            candidate = parser(raw)
        except (json.JSONDecodeError, tomllib.TOMLDecodeError) as exc:
            parse_error = exc
            continue

        if isinstance(candidate, dict):
            parsed = candidate
            break

        parse_error = ValueError(f"{parser.__name__} parsed a non-dict root value")

    if parsed is None:
        click.echo(
            f"Warning: unable to parse shared prompt context from {config_path}: {parse_error}",
            err=True,
        )
        return "\n\n".join(section for section in sections if section)

    project_doc_auto_load = parsed.get("project_doc_auto_load") is True
    doc_fallback_files = _normalize_string_list(parsed.get("project_doc_fallback_filenames"))
    doc_extra_files = _normalize_string_list(parsed.get("project_doc_auto_load_extra_filenames"))
    project_doc_max_bytes = parsed.get("project_doc_max_bytes")

    project_doc_files = _normalize_string_list(doc_fallback_files + doc_extra_files)
    if project_doc_auto_load and project_doc_files:
        doc_lines = "\n".join(f"- {name}" for name in project_doc_files)
        doc_section = (
            "Before you start coding, read these repository files if they exist and treat them as authoritative context:\n"
            f"{doc_lines}"
        )
        if isinstance(project_doc_max_bytes, int) and project_doc_max_bytes > 0:
            doc_section += f"\nLimit each file read to about {project_doc_max_bytes} bytes."
        sections.append(doc_section)

    return "\n\n".join(section for section in sections if section)


def _sync_gemini_shared_context_file(*, host_gemini_dir: Path, shared_prompt_context: str) -> None:
    context_file = host_gemini_dir / GEMINI_CONTEXT_FILE_NAME
    updated_context = str(shared_prompt_context or "").strip()
    updated = f"{updated_context}\n" if updated_context else ""

    existing = ""
    if context_file.exists():
        try:
            existing = context_file.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            click.echo(f"Warning: unable to read Gemini context file {context_file}: {exc}", err=True)
            return

    if existing == updated:
        return

    try:
        if updated:
            context_file.parent.mkdir(parents=True, exist_ok=True)
            context_file.write_text(updated, encoding="utf-8")
        elif context_file.exists():
            context_file.unlink()
    except OSError as exc:
        click.echo(f"Warning: unable to update Gemini context file {context_file}: {exc}", err=True)


def _repo_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / "pyproject.toml").exists():
            return parent
    return Path(__file__).resolve().parent.parent.parent


def _default_config_file() -> Path:
    config_file = _repo_root() / "config" / "agent.config.toml"
    if config_file.exists():
        return config_file

    fallback = Path.cwd() / "config" / "agent.config.toml"
    if fallback.exists():
        return fallback

    return config_file


def _default_system_prompt_file() -> Path:
    prompt_file = _repo_root() / SYSTEM_PROMPT_FILE_NAME
    if prompt_file.exists():
        return prompt_file

    fallback = Path.cwd() / SYSTEM_PROMPT_FILE_NAME
    if fallback.exists():
        return fallback

    return prompt_file


def _default_credentials_file() -> Path:
    return _repo_root() / ".credentials"


def _default_agent_hub_data_dir() -> Path:
    return Path.home() / ".local" / "share" / "agent-hub"


def _default_agent_hub_git_credentials_dir() -> Path:
    return _default_agent_hub_data_dir() / AGENT_HUB_SECRETS_DIR_NAME / AGENT_HUB_GIT_CREDENTIALS_DIR_NAME


def _split_host_port(host: str) -> tuple[str, int | None]:
    candidate = str(host or "").strip().lower()
    if not candidate:
        return "", None
    if ":" not in candidate:
        return candidate, None
    hostname, port_text = candidate.rsplit(":", 1)
    if not hostname or not port_text.isdigit():
        raise click.ClickException(f"Invalid git credential host: {host}")
    port = int(port_text)
    if port <= 0 or port > 65535:
        raise click.ClickException(f"Invalid git credential host: {host}")
    return hostname, port


def _normalize_git_credential_scheme(raw_value: str) -> str:
    scheme = str(raw_value or "").strip().lower()
    if not scheme:
        return GIT_CREDENTIAL_DEFAULT_SCHEME
    if scheme not in GIT_CREDENTIAL_ALLOWED_SCHEMES:
        raise click.ClickException(f"Invalid git credential scheme: {raw_value}")
    return scheme


def _parse_git_credential_store_host(credential_line: str) -> tuple[str, str] | None:
    candidate = str(credential_line or "").strip()
    if not candidate:
        return None
    try:
        parsed = urllib.parse.urlsplit(candidate)
    except ValueError:
        return None
    host = str(parsed.hostname or "").strip().lower()
    if not host:
        return None
    scheme = _normalize_git_credential_scheme(parsed.scheme)
    if parsed.port:
        host = f"{host}:{parsed.port}"
    try:
        return _normalize_git_credential_host(host), scheme
    except click.ClickException:
        return None


def _discover_agent_hub_git_credentials() -> tuple[Path | None, str, str]:
    credentials_dir = _default_agent_hub_git_credentials_dir()
    if not credentials_dir.is_dir():
        return None, "", ""

    candidates: list[Path] = []
    try:
        for path in credentials_dir.iterdir():
            if not path.is_file():
                continue
            candidates.append(path)
    except OSError:
        return None, "", ""
    if not candidates:
        return None, "", ""

    def _sort_key(path: Path) -> tuple[float, str]:
        try:
            mtime = float(path.stat().st_mtime)
        except OSError:
            mtime = 0.0
        return (mtime, path.name)

    candidates.sort(key=_sort_key, reverse=True)
    for credentials_path in candidates:
        try:
            with credentials_path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    parsed = _parse_git_credential_store_host(line)
                    if parsed is None:
                        continue
                    host, scheme = parsed
                    return credentials_path.resolve(), host, scheme
        except (OSError, UnicodeError):
            continue
    return None, "", ""


def _resolved_agent_hub_data_dir() -> Path:
    candidate = str(os.environ.get(AGENT_HUB_DATA_DIR_ENV) or "").strip()
    if candidate:
        return Path(candidate).expanduser().resolve()
    return _default_agent_hub_data_dir()


def _write_private_text_file(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def _strip_mcp_server_table(config_text: str, server_name: str) -> str:
    if not config_text:
        return ""
    escaped_name = re.escape(server_name)
    pattern = re.compile(r"(?ms)^\[mcp_servers\." + escaped_name + r"(?:\.[^\]]+)?\]\n.*?(?=^\[|\Z)")
    stripped = re.sub(pattern, "", config_text)
    return stripped.rstrip() + "\n"


def _env_var_keys(entries: Iterable[str]) -> set[str]:
    keys: set[str] = set()
    for entry in entries:
        key, _sep, _value = str(entry).partition("=")
        normalized = key.strip()
        if normalized:
            keys.add(normalized)
    return keys


def _agent_tools_env_from_entries(entries: Iterable[str]) -> dict[str, str]:
    env_values: dict[str, str] = {}
    for entry in entries:
        key, sep, value = str(entry).partition("=")
        normalized_key = key.strip()
        if not normalized_key or not sep:
            continue
        if normalized_key in {
            AGENT_TOOLS_URL_ENV,
            AGENT_TOOLS_TOKEN_ENV,
            AGENT_TOOLS_PROJECT_ID_ENV,
            AGENT_TOOLS_CHAT_ID_ENV,
        }:
            env_values[normalized_key] = value.strip()

    return {
        AGENT_TOOLS_URL_ENV: env_values.get(AGENT_TOOLS_URL_ENV, ""),
        AGENT_TOOLS_TOKEN_ENV: env_values.get(AGENT_TOOLS_TOKEN_ENV, ""),
        AGENT_TOOLS_PROJECT_ID_ENV: env_values.get(AGENT_TOOLS_PROJECT_ID_ENV, ""),
        AGENT_TOOLS_CHAT_ID_ENV: env_values.get(AGENT_TOOLS_CHAT_ID_ENV, ""),
    }


def _git_origin_repo_url(project_path: Path) -> str:
    result = subprocess.run(
        ["git", "-C", str(project_path), "remote", "get-url", "origin"],
        check=False,
        text=True,
        capture_output=True,
    )
    if result.returncode != 0:
        return ""
    return str(result.stdout or "").strip()


@dataclass
class _AgentToolsRuntimeBridge:
    runtime_config_path: Path
    env_vars: list[str]
    state: object | None
    session_id: str
    server: ThreadingHTTPServer | None
    thread: Thread | None
    cleanup_runtime_config: bool = True

    def close(self) -> None:
        if self.server is not None:
            try:
                self.server.shutdown()
            except Exception:
                pass
            try:
                self.server.server_close()
            except Exception:
                pass
        if self.thread is not None and self.thread.is_alive():
            self.thread.join(timeout=2.0)

        sessions_lock = getattr(self.state, "_agent_tools_sessions_lock", None) if self.state is not None else None
        sessions = getattr(self.state, "_agent_tools_sessions", None) if self.state is not None else None
        if self.session_id and sessions_lock is not None and isinstance(sessions, dict):
            try:
                with sessions_lock:
                    sessions.pop(self.session_id, None)
            except Exception:
                pass
        if self.cleanup_runtime_config:
            try:
                self.runtime_config_path.unlink(missing_ok=True)
            except OSError:
                pass


def _resolve_existing_project_context(state: object, repo_url: str) -> tuple[str, dict[str, object]]:
    from agent_hub import server as hub_server

    target_repo = str(repo_url or "").strip()
    if not target_repo:
        return "", hub_server._normalize_project_credential_binding(None)

    target_host = hub_server._git_repo_host(target_repo)
    target_owner = hub_server._git_repo_owner(target_repo)
    target_name = hub_server._extract_repo_name(target_repo).lower().strip()
    if not target_host or not target_name:
        return "", hub_server._normalize_project_credential_binding(None)

    state_payload = state.load()
    projects = state_payload.get("projects")
    if not isinstance(projects, dict):
        return "", hub_server._normalize_project_credential_binding(None)

    for project_id, project in projects.items():
        if not isinstance(project, dict):
            continue
        project_repo = str(project.get("repo_url") or "").strip()
        if not project_repo:
            continue
        host = hub_server._git_repo_host(project_repo)
        owner = hub_server._git_repo_owner(project_repo)
        name = hub_server._extract_repo_name(project_repo).lower().strip()
        if host == target_host and owner == target_owner and name == target_name:
            binding = hub_server._normalize_project_credential_binding(project.get("credential_binding"))
            return str(project_id), binding

    return "", hub_server._normalize_project_credential_binding(None)


def _build_agent_tools_runtime_config(
    *,
    config_path: Path,
    host_codex_dir: Path,
    agent_tools_env: dict[str, str],
    agent_provider: agent_providers.AgentProvider,
    container_home: str,
    agent_tools_config_path: Path | None = None,
) -> Path:
    from agent_hub import server as hub_server

    source_script = hub_server._agent_tools_mcp_source_path()
    try:
        script_text = source_script.read_text(encoding="utf-8")
    except OSError as exc:
        raise click.ClickException(f"Failed to read agent_tools MCP script {source_script}: {exc}") from exc

    runtime_script = host_codex_dir / AGENT_TOOLS_MCP_RUNTIME_DIR_NAME / AGENT_TOOLS_MCP_RUNTIME_FILE_NAME
    if runtime_script.exists():
        try:
            existing_script = runtime_script.read_text(encoding="utf-8")
        except OSError:
            existing_script = ""
    else:
        existing_script = ""
    if existing_script != script_text:
        try:
            _write_private_text_file(runtime_script, script_text)
        except OSError as exc:
            raise click.ClickException(f"Failed to materialize agent_tools MCP script {runtime_script}: {exc}") from exc

    if isinstance(agent_provider, (agent_providers.ClaudeProvider, agent_providers.GeminiProvider)):
        base_config_path = agent_tools_config_path or config_path
        runtime_config_path = base_config_path if agent_tools_config_path is not None else (
            host_codex_dir / f"agent-tools-runtime-{uuid.uuid4().hex}.json"
        )
    else:
        base_config_path = config_path
        runtime_config_path = host_codex_dir / f"agent-tools-runtime-{uuid.uuid4().hex}.toml"

    try:
        base_config = base_config_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise click.ClickException(f"Failed to read config file {base_config_path}: {exc}") from exc

    if (
        isinstance(agent_provider, (agent_providers.ClaudeProvider, agent_providers.GeminiProvider))
        and agent_tools_config_path is not None
    ):
        base_config_text = base_config.strip()
        if base_config_text:
            try:
                parsed_config = json.loads(base_config_text)
            except json.JSONDecodeError as exc:
                raise click.ClickException(
                    f"Failed to parse JSON config file {base_config_path}: {exc}"
                ) from exc
            if not isinstance(parsed_config, dict):
                raise click.ClickException(f"{base_config_path} must contain a JSON object for MCP config merging.")

    agent_tools_url = str(agent_tools_env.get(AGENT_TOOLS_URL_ENV) or "").strip()
    agent_tools_token = str(agent_tools_env.get(AGENT_TOOLS_TOKEN_ENV) or "").strip()
    if not agent_tools_url:
        raise click.ClickException(f"Missing required {AGENT_TOOLS_URL_ENV} for agent_tools MCP runtime config.")
    if not agent_tools_token:
        raise click.ClickException(f"Missing required {AGENT_TOOLS_TOKEN_ENV} for agent_tools MCP runtime config.")

    normalized_container_home = str(container_home or "").strip() or DEFAULT_CONTAINER_HOME
    mcp_script_path = str(
        PurePosixPath(normalized_container_home)
        / ".codex"
        / AGENT_TOOLS_MCP_RUNTIME_DIR_NAME
        / AGENT_TOOLS_MCP_RUNTIME_FILE_NAME
    )

    merged_config = agent_provider.build_mcp_config(
        base_config_text=base_config,
        mcp_env={
            AGENT_TOOLS_URL_ENV: agent_tools_url,
            AGENT_TOOLS_TOKEN_ENV: agent_tools_token,
            AGENT_TOOLS_PROJECT_ID_ENV: str(agent_tools_env.get(AGENT_TOOLS_PROJECT_ID_ENV) or '').strip(),
            AGENT_TOOLS_CHAT_ID_ENV: str(agent_tools_env.get(AGENT_TOOLS_CHAT_ID_ENV) or '').strip(),
        },
        script_path=mcp_script_path,
    )

    ext = ".json" if isinstance(agent_provider, (agent_providers.ClaudeProvider, agent_providers.GeminiProvider)) else ".toml"
    if runtime_config_path.suffix != ext:
        runtime_config_path = runtime_config_path.with_suffix(ext)

    try:
        _write_private_text_file(runtime_config_path, merged_config)
    except OSError as exc:
        raise click.ClickException(f"Failed to write runtime config {runtime_config_path}: {exc}") from exc
    return runtime_config_path


def _start_agent_tools_runtime_bridge(
    *,
    project_path: Path,
    host_codex_dir: Path,
    config_path: Path,
    system_prompt_path: Path,
    agent_tools_config_path: Path | None,
    parsed_env_vars: list[str],
    agent_provider: agent_providers.AgentProvider,
    container_home: str,
) -> _AgentToolsRuntimeBridge | None:
    preserve_agent_tools_config = bool(
        isinstance(agent_provider, (agent_providers.ClaudeProvider, agent_providers.GeminiProvider))
        and agent_tools_config_path is not None
    )

    if AGENT_TOOLS_URL_ENV in _env_var_keys(parsed_env_vars) or AGENT_TOOLS_TOKEN_ENV in _env_var_keys(parsed_env_vars):
        keys = _env_var_keys(parsed_env_vars)
        if AGENT_TOOLS_URL_ENV not in keys or AGENT_TOOLS_TOKEN_ENV not in keys:
            raise click.ClickException(
                f"{AGENT_TOOLS_URL_ENV} and {AGENT_TOOLS_TOKEN_ENV} must be provided together when using --env-var."
            )
        runtime_config_path = _build_agent_tools_runtime_config(
            config_path=config_path,
            host_codex_dir=host_codex_dir,
            agent_tools_env=_agent_tools_env_from_entries(parsed_env_vars),
            agent_provider=agent_provider,
            container_home=container_home,
            agent_tools_config_path=agent_tools_config_path if isinstance(
                agent_provider,
                (agent_providers.ClaudeProvider, agent_providers.GeminiProvider),
            ) else None,
        )
        return _AgentToolsRuntimeBridge(
            runtime_config_path=runtime_config_path,
            env_vars=[],
            state=None,
            session_id="",
            server=None,
            thread=None,
            cleanup_runtime_config=not preserve_agent_tools_config,
        )

    from agent_hub import server as hub_server

    class _CliHubState(hub_server.HubState):
        def _reconcile_project_build_state(self) -> None:  # type: ignore[override]
            return

    data_dir = _resolved_agent_hub_data_dir()
    hub_state = _CliHubState(
        data_dir=data_dir,
        config_file=config_path,
        system_prompt_file=system_prompt_path,
        artifact_publish_base_url="http://127.0.0.1",
    )
    repo_url = _git_origin_repo_url(project_path)
    project_id = ""
    session_id = ""
    runtime_config_path: Path | None = None
    server: ThreadingHTTPServer | None = None
    thread: Thread | None = None
    try:
        project_id, credential_binding = _resolve_existing_project_context(hub_state, repo_url)
        session_id, session_token = hub_state._create_agent_tools_session(
            project_id=project_id,
            repo_url=repo_url,
            credential_binding=credential_binding,
        )

        class _BridgeHandler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, format: str, *args: object) -> None:  # noqa: A003
                del format, args
                return

            def _send_json(self, status_code: int, payload: dict[str, object]) -> None:
                encoded = json.dumps(payload).encode("utf-8")
                self.send_response(status_code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)

            def _read_payload(self) -> dict[str, object]:
                try:
                    content_length = int(str(self.headers.get("Content-Length") or "0"))
                except ValueError:
                    content_length = 0
                if content_length <= 0:
                    return {}
                body = self.rfile.read(content_length).decode("utf-8", errors="ignore")
                if not body.strip():
                    return {}
                try:
                    parsed = json.loads(body)
                except json.JSONDecodeError as exc:
                    raise click.ClickException(f"Invalid JSON payload: {exc}") from exc
                if not isinstance(parsed, dict):
                    raise click.ClickException("Invalid JSON payload.")
                return parsed

            def _request_token(self) -> str:
                auth_header = str(self.headers.get("Authorization") or "")
                if auth_header.lower().startswith("bearer "):
                    return auth_header[7:].strip()
                return str(self.headers.get(AGENT_TOOLS_TOKEN_HEADER) or "").strip()

            def _authorize(self) -> None:
                hub_state.require_agent_tools_session_token(session_id, self._request_token())

            @staticmethod
            def _http_detail(exc: Exception) -> tuple[int, str]:
                status_code = int(getattr(exc, "status_code", 500))
                detail = str(getattr(exc, "detail", str(exc)))
                return status_code, detail

            def do_GET(self) -> None:  # noqa: N802
                path = urllib.parse.urlsplit(self.path).path
                if path != "/credentials":
                    self._send_json(404, {"detail": "Not found."})
                    return
                try:
                    self._authorize()
                    payload = hub_state.agent_tools_session_credentials_list_payload(session_id)
                except Exception as exc:
                    status_code, detail = self._http_detail(exc)
                    self._send_json(status_code, {"detail": detail})
                    return
                self._send_json(200, payload)

            def do_POST(self) -> None:  # noqa: N802
                path = urllib.parse.urlsplit(self.path).path
                if path not in {"/credentials/resolve", "/project-binding", "/artifacts/submit"}:
                    self._send_json(404, {"detail": "Not found."})
                    return
                try:
                    self._authorize()
                    payload = self._read_payload()
                    if path == "/credentials/resolve":
                        mode = payload.get("mode")
                        credential_ids = payload.get("credential_ids")
                        response = hub_state.resolve_agent_tools_session_credentials(
                            session_id=session_id,
                            mode=mode,
                            credential_ids=credential_ids,
                        )
                    elif path == "/project-binding":
                        mode = payload.get("mode")
                        credential_ids = payload.get("credential_ids")
                        response = hub_state.attach_agent_tools_session_project_credentials(
                            session_id=session_id,
                            mode=mode,
                            credential_ids=credential_ids,
                        )
                    else:
                        response = {
                            "artifact": hub_state.submit_session_artifact(
                                session_id=session_id,
                                token=self._request_token(),
                                submitted_path=payload.get("path"),
                                name=payload.get("name"),
                            )
                        }
                except Exception as exc:
                    status_code, detail = self._http_detail(exc)
                    self._send_json(status_code, {"detail": detail})
                    return
                self._send_json(200, response)

        server = ThreadingHTTPServer(("0.0.0.0", 0), _BridgeHandler)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        bridge_port = int(server.server_address[1])
        env_vars = [
            f"{AGENT_TOOLS_URL_ENV}=http://host.docker.internal:{bridge_port}",
            f"{AGENT_TOOLS_TOKEN_ENV}={session_token}",
            f"{AGENT_TOOLS_PROJECT_ID_ENV}={project_id}",
            f"{AGENT_TOOLS_CHAT_ID_ENV}=agent_cli:{session_id}",
        ]
        runtime_config_path = _build_agent_tools_runtime_config(
            config_path=config_path,
            host_codex_dir=host_codex_dir,
            agent_tools_env=_agent_tools_env_from_entries(env_vars),
            agent_provider=agent_provider,
            container_home=container_home,
            agent_tools_config_path=agent_tools_config_path if isinstance(
                agent_provider,
                (agent_providers.ClaudeProvider, agent_providers.GeminiProvider),
            ) else None,
        )
        return _AgentToolsRuntimeBridge(
            runtime_config_path=runtime_config_path,
            env_vars=env_vars,
            state=hub_state,
            session_id=session_id,
            server=server,
            thread=thread,
            cleanup_runtime_config=not preserve_agent_tools_config,
        )
    except Exception:
        if server is not None:
            try:
                server.shutdown()
            except Exception:
                pass
            try:
                server.server_close()
            except Exception:
                pass
        if thread is not None and thread.is_alive():
            thread.join(timeout=2.0)
        if session_id:
            sessions_lock = getattr(hub_state, "_agent_tools_sessions_lock", None)
            sessions = getattr(hub_state, "_agent_tools_sessions", None)
            remove_session = getattr(hub_state, "_remove_agent_tools_session", None)
            if callable(remove_session):
                try:
                    remove_session(session_id)
                except Exception:
                    pass
            elif sessions_lock is not None and isinstance(sessions, dict):
                try:
                    with sessions_lock:
                        sessions.pop(session_id, None)
                except Exception:
                    pass
        if runtime_config_path is not None:
            if not preserve_agent_tools_config:
                try:
                    runtime_config_path.unlink(missing_ok=True)
                except OSError:
                    pass
        raise


def _default_group_name() -> str:
    import grp

    return grp.getgrgid(os.getgid()).gr_name


def _gid_for_group_name(group_name: str) -> int:
    import grp

    normalized = str(group_name or "").strip()
    if not normalized:
        raise click.ClickException("Group name must not be empty")
    try:
        return int(grp.getgrnam(normalized).gr_gid)
    except KeyError as exc:
        raise click.ClickException(f"Unknown group name: {normalized}") from exc


def _default_supplementary_gids() -> str:
    gids = sorted({gid for gid in os.getgroups() if gid != os.getgid()})
    return ",".join(str(gid) for gid in gids)


def _default_supplementary_groups() -> str:
    import grp

    groups: list[str] = []
    for gid in sorted({gid for gid in os.getgroups() if gid != os.getgid()}):
        try:
            groups.append(grp.getgrgid(gid).gr_name)
        except KeyError:
            groups.append(str(gid))
    return ",".join(groups)


def _to_absolute(value: str, cwd: Path) -> Path:
    path = Path(value).expanduser()
    return path if path.is_absolute() else (cwd / path).resolve()


def _short_hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()[:12]


def _sanitize_tag_component(value: str) -> str:
    sanitized = re.sub(r"[^a-z0-9_.-]", "-", value.lower())
    sanitized = sanitized.strip("-")
    return sanitized or "base"


def _run(cmd: Iterable[str], cwd: Path | None = None) -> None:
    try:
        subprocess.run(list(cmd), cwd=str(cwd) if cwd else None, check=True)
    except subprocess.CalledProcessError as exc:
        raise click.ClickException(f"Command failed with exit code {exc.returncode}: {' '.join(cmd)}")


def _docker_image_exists(tag: str) -> bool:
    result = subprocess.run(
        ["docker", "image", "inspect", tag],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return result.returncode == 0


def _docker_rm_force(container_name: str) -> None:
    subprocess.run(
        ["docker", "rm", "-f", container_name],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


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
            raise click.ClickException(f"Invalid supplemental GID: {token!r}")
        gid = int(token, 10)
        if gid in seen:
            continue
        gids.append(gid)
        seen.add(gid)
    return gids


def _group_names_to_gid_csv(value: str | None) -> str:
    if value is None:
        return ""
    names = _normalize_csv(value)
    if not names:
        return ""
    gids = [str(_gid_for_group_name(name)) for name in names.split(",") if name]
    return _normalize_csv(",".join(gids))


def _docker_socket_gid() -> int | None:
    try:
        return int(os.stat(DOCKER_SOCKET_PATH).st_gid)
    except OSError:
        return None


def _parse_mount(spec: str, label: str) -> Tuple[str, str]:
    if ":" not in spec:
        raise click.ClickException(f"Invalid {label}: {spec} (expected /host/path:/container/path)")
    host, container = spec.split(":", 1)
    if not host or not container:
        raise click.ClickException(f"Invalid {label}: {spec} (expected /host/path:/container/path)")
    if not container.startswith("/"):
        raise click.ClickException(f"Invalid container path in {label}: {container} (must be absolute)")

    host_path = Path(host).expanduser()
    if not host_path.exists():
        raise click.ClickException(f"Host path in {label} does not exist: {host}")

    return str(host_path), container


def _normalize_container_project_name(raw_value: str | None, fallback_name: str) -> str:
    candidate = str(raw_value or "").strip() or str(fallback_name or "").strip()
    if not candidate:
        raise click.ClickException("Unable to resolve container project directory name.")
    if "/" in candidate or candidate in {".", ".."}:
        raise click.ClickException(
            f"Invalid container project directory name: {candidate!r} "
            "(must be a single path component)."
        )
    return candidate


def _normalize_container_path(raw_path: str) -> PurePosixPath:
    normalized = posixpath.normpath(str(raw_path or "").strip())
    if not normalized.startswith("/"):
        raise click.ClickException(f"Invalid container path: {raw_path} (must be absolute)")
    return PurePosixPath(normalized)


def _container_path_is_within(path: PurePosixPath, root: PurePosixPath) -> bool:
    if path == root:
        return True
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _reject_mount_inside_project_path(*, spec: str, label: str, container_project_path: PurePosixPath) -> None:
    if ":" not in spec:
        return
    _host, container = spec.split(":", 1)
    container_path = _normalize_container_path(container)
    if _container_path_is_within(container_path, container_project_path):
        raise click.ClickException(
            f"Invalid {label}: {spec}. Container path '{container_path}' is inside the project mount path "
            f"'{container_project_path}', which can cause Docker to create root-owned directories in the checkout. "
            "Mount shared/system paths outside the checkout (for example /workspace/.cache/sccache)."
        )


def _path_metadata(path: Path) -> str:
    try:
        info = path.stat()
    except OSError as exc:
        return f"stat_error={exc}"
    permissions = stat.S_IMODE(info.st_mode)
    return f"uid={info.st_uid} gid={info.st_gid} mode=0o{permissions:03o}"


def _rw_mount_preflight_error(
    *,
    host_path: Path,
    container_path: str,
    reason: str,
    runtime_uid: int,
    runtime_gid: int,
    failing_path: Path | None = None,
) -> None:
    offending = failing_path or host_path
    raise click.ClickException(
        "RW mount preflight failed for "
        f"{host_path} -> {container_path}: {reason}. "
        f"offending_path={offending} ({_path_metadata(offending)}); "
        f"mount_root={host_path} ({_path_metadata(host_path)}); "
        f"runtime_uid_gid={runtime_uid}:{runtime_gid}"
    )


def _ensure_rw_mount_owner(root: Path, container_path: str, runtime_uid: int, runtime_gid: int) -> None:
    try:
        owner_uid = int(root.stat().st_uid)
    except OSError as exc:
        _rw_mount_preflight_error(
            host_path=root,
            container_path=container_path,
            reason=f"cannot stat mount root owner ({exc})",
            runtime_uid=runtime_uid,
            runtime_gid=runtime_gid,
            failing_path=root,
        )
    if owner_uid != runtime_uid:
        _rw_mount_preflight_error(
            host_path=root,
            container_path=container_path,
            reason=f"mount root owner uid does not match runtime uid ({owner_uid} != {runtime_uid})",
            runtime_uid=runtime_uid,
            runtime_gid=runtime_gid,
            failing_path=root,
        )


def _probe_rw_directory(root: Path, container_path: str, runtime_uid: int, runtime_gid: int) -> None:
    _ensure_rw_mount_owner(root, container_path, runtime_uid, runtime_gid)
    if not os.access(root, os.W_OK | os.X_OK):
        _rw_mount_preflight_error(
            host_path=root,
            container_path=container_path,
            reason="mount root directory is not writable/executable by current runtime user",
            runtime_uid=runtime_uid,
            runtime_gid=runtime_gid,
            failing_path=root,
        )
    try:
        fd, probe_path = tempfile.mkstemp(prefix=".agent_cli_rw_probe_", dir=str(root))
        os.close(fd)
        os.unlink(probe_path)
    except OSError as exc:
        _rw_mount_preflight_error(
            host_path=root,
            container_path=container_path,
            reason=f"cannot create and remove probe file in mount root ({exc})",
            runtime_uid=runtime_uid,
            runtime_gid=runtime_gid,
            failing_path=root,
        )


def _validate_rw_mount(host_path: Path, container_path: str, runtime_uid: int, runtime_gid: int) -> None:
    if not host_path.exists():
        _rw_mount_preflight_error(
            host_path=host_path,
            container_path=container_path,
            reason="host path does not exist",
            runtime_uid=runtime_uid,
            runtime_gid=runtime_gid,
            failing_path=host_path,
        )
    if host_path.is_dir():
        _probe_rw_directory(host_path, container_path, runtime_uid, runtime_gid)
        return
    if host_path.is_file():
        _ensure_rw_mount_owner(host_path, container_path, runtime_uid, runtime_gid)
        if not os.access(host_path, os.W_OK):
            _rw_mount_preflight_error(
                host_path=host_path,
                container_path=container_path,
                reason="file mount path is not writable",
                runtime_uid=runtime_uid,
                runtime_gid=runtime_gid,
                failing_path=host_path,
            )
        try:
            with host_path.open("ab"):
                pass
        except OSError as exc:
            _rw_mount_preflight_error(
                host_path=host_path,
                container_path=container_path,
                reason=f"cannot open file in append mode ({exc})",
                runtime_uid=runtime_uid,
                runtime_gid=runtime_gid,
                failing_path=host_path,
            )
        return
    _rw_mount_preflight_error(
        host_path=host_path,
        container_path=container_path,
        reason="mount path must be a regular file or directory",
        runtime_uid=runtime_uid,
        runtime_gid=runtime_gid,
        failing_path=host_path,
    )


def _build_snapshot_setup_shell_script(setup_script: str) -> str:
    normalized_script = (setup_script or "").strip() or ":"
    return (
        "set -e\n"
        "set -o pipefail\n"
        "printf '%s\\n' '[agent_cli] snapshot bootstrap: preparing writable /workspace/tmp'\n"
        "mkdir -p /workspace/tmp\n"
        "chmod 777 /workspace/tmp\n"
        "printf '%s\\n' '[agent_cli] snapshot bootstrap: configuring git safe.directory'\n"
        "git config --global --add safe.directory '*'\n"
        'if [ -n "${AGENT_HUB_GIT_CREDENTIALS_SOURCE:-}" ]; then\n'
        '  if [ ! -f "${AGENT_HUB_GIT_CREDENTIALS_SOURCE}" ]; then\n'
        "    printf '%s\\n' '[agent_cli] snapshot bootstrap failed: AGENT_HUB_GIT_CREDENTIALS_SOURCE is set but file is missing' >&2\n"
        "    printf '%s\\n' \"[agent_cli] missing path: ${AGENT_HUB_GIT_CREDENTIALS_SOURCE}\" >&2\n"
        "    exit 96\n"
        "  fi\n"
        '  credential_target="${AGENT_HUB_GIT_CREDENTIALS_FILE:-/tmp/agent_hub_git_credentials}"\n'
        "  printf '%s\\n' \"[agent_cli] snapshot bootstrap: copying git credentials to ${credential_target}\"\n"
        '  cp "${AGENT_HUB_GIT_CREDENTIALS_SOURCE}" "${credential_target}"\n'
        '  chmod 600 "${credential_target}"\n'
        "fi\n"
        "printf '%s\\n' '[agent_cli] snapshot bootstrap: running project setup script'\n"
        + normalized_script
        + "\n"
    )


def _parse_env_var(spec: str, label: str) -> str:
    if "=" not in spec:
        raise click.ClickException(f"Invalid {label}: {spec} (expected KEY=VALUE)")
    key, value = spec.split("=", 1)
    key = key.strip()
    if not key:
        raise click.ClickException(f"Invalid {label}: {spec} (empty key)")
    if any(ch.isspace() for ch in key):
        raise click.ClickException(f"Invalid {label}: {spec} (key must not contain whitespace)")
    return f"{key}={value}"


def _normalize_agent_command(raw_value: str | None) -> str:
    value = str(raw_value or DEFAULT_AGENT_COMMAND).strip()
    if not value:
        return DEFAULT_AGENT_COMMAND
    if not re.fullmatch(r"[A-Za-z0-9._-]+", value):
        raise click.ClickException(
            f"Invalid --agent-command value: {raw_value!r} (allowed characters: letters, numbers, . _ -)"
        )
    return value


def _agent_provider_for_command(agent_command: str) -> str:
    command = str(agent_command or "").strip().lower()
    if command == "codex":
        return AGENT_PROVIDER_CODEX
    if command == "claude":
        return AGENT_PROVIDER_CLAUDE
    if command == "gemini":
        return AGENT_PROVIDER_GEMINI
    return AGENT_PROVIDER_NONE


def _default_runtime_image_for_provider(agent_provider: str) -> str:
    if agent_provider == AGENT_PROVIDER_CLAUDE:
        return CLAUDE_RUNTIME_IMAGE
    if agent_provider == AGENT_PROVIDER_GEMINI:
        return GEMINI_RUNTIME_IMAGE
    if agent_provider == AGENT_PROVIDER_CODEX:
        return DEFAULT_RUNTIME_IMAGE
    return DEFAULT_SETUP_RUNTIME_IMAGE


def _snapshot_runtime_image_for_provider(snapshot_tag: str, agent_provider: str) -> str:
    return f"agent-runtime-{_sanitize_tag_component(agent_provider)}-{_short_hash(snapshot_tag)}"


def _snapshot_setup_runtime_image_for_snapshot(snapshot_tag: str) -> str:
    normalized_snapshot_tag = str(snapshot_tag or "").strip()
    if not normalized_snapshot_tag:
        raise click.ClickException("Snapshot tag is required to resolve setup runtime image.")
    return f"agent-runtime-setup-{_short_hash(normalized_snapshot_tag)}"


def _runtime_image_build_lock_path(target_image: str) -> Path:
    digest = hashlib.sha256(str(target_image or "").encode("utf-8")).hexdigest()
    return RUNTIME_IMAGE_BUILD_LOCK_DIR / f"{digest}.lock"


@contextmanager
def _runtime_image_build_lock(target_image: str) -> Iterator[None]:
    lock_path = _runtime_image_build_lock_path(target_image)
    try:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock_handle = lock_path.open("a+", encoding="utf-8")
    except OSError as exc:
        raise click.ClickException(
            f"Failed to initialize runtime image build lock for '{target_image}' at {lock_path}: {exc}"
        ) from exc
    try:
        try:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
        except OSError as exc:
            raise click.ClickException(
                f"Failed to acquire runtime image build lock for '{target_image}' at {lock_path}: {exc}"
            ) from exc
        yield
    finally:
        try:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        lock_handle.close()


def _build_runtime_image(
    *,
    base_image: str,
    target_image: str,
    agent_provider: str,
    recursive_workspace_chmod: bool = True,
) -> None:
    click.echo(
        f"Building runtime image '{target_image}' from {DEFAULT_DOCKERFILE} "
        f"(base={base_image}, provider={agent_provider})"
    )
    recursive_workspace_chmod_value = (
        RECURSIVE_WORKSPACE_CHMOD_ENABLED
        if recursive_workspace_chmod
        else RECURSIVE_WORKSPACE_CHMOD_DISABLED
    )
    _run(
        [
            "docker",
            "build",
            "-f",
            str(_repo_root() / DEFAULT_DOCKERFILE),
            "--build-arg",
            f"BASE_IMAGE={base_image}",
            "--build-arg",
            f"AGENT_PROVIDER={agent_provider}",
            "--build-arg",
            f"RECURSIVE_WORKSPACE_CHMOD={recursive_workspace_chmod_value}",
            "-t",
            target_image,
            str(_repo_root()),
        ],
        cwd=_repo_root(),
    )


def _ensure_runtime_image_built_if_missing(
    *,
    base_image: str,
    target_image: str,
    agent_provider: str,
    recursive_workspace_chmod: bool = True,
) -> None:
    if _docker_image_exists(target_image):
        return
    with _runtime_image_build_lock(target_image):
        if _docker_image_exists(target_image):
            return
        _build_runtime_image(
            base_image=base_image,
            target_image=target_image,
            agent_provider=agent_provider,
            recursive_workspace_chmod=recursive_workspace_chmod,
        )


def _read_openai_api_key(path: Path) -> str | None:
    if not path.exists():
        return None

    for line in path.read_text().splitlines():
        match = re.match(r"^\s*OPENAI_API_KEY\s*=\s*(.+?)\s*$", line)
        if not match:
            continue
        value = match.group(1).strip().strip('"').strip("'")
        if value:
            return value
    return None


def _ensure_claude_json_file(path: Path) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise click.ClickException(f"Unable to create parent directory for Claude config file {path}: {exc}") from exc

    if path.exists():
        if not path.is_file():
            raise click.ClickException(f"Claude config path exists but is not a file: {path}")
        try:
            raw = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise click.ClickException(f"Unable to read Claude config file {path}: {exc}") from exc
        stripped_raw = raw.strip()
        if stripped_raw:
            try:
                parsed = json.loads(stripped_raw)
                if not isinstance(parsed, dict):
                    raise click.ClickException(f"Claude config file {path} must be a JSON object")
                return
            except json.JSONDecodeError as exc:
                raise click.ClickException(f"Claude config file {path} must be valid JSON: {exc}") from exc

        try:
            path.write_text("{}\n", encoding="utf-8")
        except OSError as exc:
            raise click.ClickException(f"Unable to initialize Claude config file {path}: {exc}") from exc
        return
    try:
        path.write_text("{}\n", encoding="utf-8")
    except OSError as exc:
        raise click.ClickException(f"Unable to initialize Claude config file {path}: {exc}") from exc


def _ensure_gemini_settings_file(path: Path) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise click.ClickException(f"Unable to create parent directory for Gemini settings file {path}: {exc}") from exc

    if path.exists():
        if not path.is_file():
            raise click.ClickException(f"Gemini settings path exists but is not a file: {path}")
        try:
            raw = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise click.ClickException(f"Unable to read Gemini settings file {path}: {exc}") from exc

        stripped_raw = raw.strip()
        if stripped_raw:
            try:
                parsed = json.loads(stripped_raw)
                if not isinstance(parsed, dict):
                    raise click.ClickException(f"Gemini settings file {path} must be a JSON object")
                return
            except json.JSONDecodeError as exc:
                raise click.ClickException(f"Gemini settings file {path} must be valid JSON: {exc}") from exc
        try:
            path.write_text("{}", encoding="utf-8")
        except OSError as exc:
            raise click.ClickException(f"Unable to initialize Gemini settings file {path}: {exc}") from exc
        return
    try:
        path.write_text("{}", encoding="utf-8")
    except OSError as exc:
        raise click.ClickException(f"Unable to initialize Gemini settings file {path}: {exc}") from exc


def _normalize_git_credential_host(raw_value: str) -> str:
    candidate = str(raw_value or "").strip().lower()
    if not candidate:
        raise click.ClickException("Git credential host is required.")
    host = candidate
    if "://" in candidate:
        parsed = urllib.parse.urlsplit(candidate)
        scheme = _normalize_git_credential_scheme(parsed.scheme)
        del scheme
        if parsed.username or parsed.password:
            raise click.ClickException(f"Invalid git credential host: {raw_value}")
        if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
            raise click.ClickException(f"Invalid git credential host: {raw_value}")
        host_name = str(parsed.hostname or "").strip().lower()
        if not host_name:
            raise click.ClickException(f"Invalid git credential host: {raw_value}")
        host = f"{host_name}:{parsed.port}" if parsed.port else host_name

    host_name, port = _split_host_port(host)
    if not re.fullmatch(r"[a-z0-9.-]+", host_name):
        raise click.ClickException(f"Invalid git credential host: {raw_value}")
    return f"{host_name}:{port}" if port else host_name


def _resolve_base_image(
    base_docker_path: str | None,
    base_docker_context: str | None,
    base_dockerfile: str | None,
    project_dir: Path,
    cwd: Path,
) -> tuple[str, Path, Path] | tuple[None, None, None]:
    resolved_context: Path | None = None
    resolved_dockerfile: Path | None = None

    if base_docker_path:
        path = _to_absolute(base_docker_path, cwd)
        if path.is_dir():
            resolved_context = path
            resolved_dockerfile = path / "Dockerfile"
        elif path.is_file():
            resolved_dockerfile = path
            resolved_context = path.parent
        else:
            raise click.ClickException(
                f"Invalid --base path: {base_docker_path}. "
                "Expected an existing Dockerfile path or a directory containing a Dockerfile."
            )
    elif base_docker_context or base_dockerfile:
        if base_docker_context:
            resolved_context = _to_absolute(base_docker_context, cwd)
            if not resolved_context.is_dir():
                raise click.ClickException(
                    f"Invalid --base-docker-context: {base_docker_context} (must be an existing directory)"
                )

        if base_dockerfile:
            if Path(base_dockerfile).is_absolute():
                resolved_dockerfile = _to_absolute(base_dockerfile, cwd)
            elif resolved_context is not None:
                resolved_dockerfile = resolved_context / base_dockerfile
            else:
                raise click.ClickException(
                    "--base-docker-context is required when --base-dockerfile is relative"
                )
        elif resolved_context is not None:
            resolved_dockerfile = resolved_context / "Dockerfile"

    if resolved_dockerfile is None:
        return None, None, None

    if not resolved_dockerfile.is_file():
        raise click.ClickException(f"Base Dockerfile not found: {resolved_dockerfile}")

    if resolved_context is None:
        resolved_context = resolved_dockerfile.parent

    tag = (
        f"agent-base-{_sanitize_tag_component(project_dir.name)}-"
        f"{_sanitize_tag_component(resolved_context.name)}-"
        f"{_short_hash(str(resolved_dockerfile))}"
    )
    return tag, resolved_context, resolved_dockerfile


@click.command(help="Launch the containerized agent environment")
@click.option("--project", default=".", show_default=True)
@click.option(
    "--agent-command",
    default=DEFAULT_AGENT_COMMAND,
    show_default=True,
    help="Agent executable launched inside the container (for example codex, claude, or gemini)",
)
@click.option("--container-home", default=None, help="Container home path for mapped user")
@click.option(
    "--container-project-name",
    default=None,
    help="Container-side project directory name under --container-home (defaults to host project directory name).",
)
@click.option("--agent-home-path", default=None, help="Host path for persistent agent state")
@click.option(
    "--config-file",
    default=str(_default_config_file()),
    show_default=True,
    help="Host agent config file mounted into container",
)
@click.option(
    "--system-prompt-file",
    default=str(_default_system_prompt_file()),
    show_default=True,
    help="Core system prompt markdown file used across Codex, Claude, and Gemini sessions.",
)
@click.option("--openai-api-key", default=None, show_default=False, help="API key to pass into container")
@click.option(
    "--credentials-file",
    default=str(_default_credentials_file()),
    show_default=True,
    help="Fallback credentials file to read OPENAI_API_KEY",
)
@click.option(
    "--git-credential-file",
    default=None,
    help="Host git credential store file mounted for authenticated git operations in the container",
)
@click.option(
    "--git-credential-host",
    default=None,
    help="Git host matched by the credential file (for example github.com)",
)
@click.option(
    "--git-credential-scheme",
    default=None,
    help="Git credential scheme matched by the credential file (http or https).",
)
@click.option(
    "--base",
    "base_docker_path",
    default=None,
    help="Dockerfile path or directory containing a Dockerfile",
)
@click.option("--base-docker-context", default=None, help="Base Dockerfile context directory")
@click.option("--base-dockerfile", default=None, help="Base Dockerfile (relative to context or absolute)")
@click.option("--base-image", default=DEFAULT_BASE_IMAGE, show_default=True)
@click.option("--base-image-tag", default=None, help="Tag for generated base image")
@click.option("--local-user", default=None)
@click.option("--local-group", default=None)
@click.option("--local-uid", default=None, type=int)
@click.option("--local-gid", default=None, type=int)
@click.option("--local-supplementary-gids", default=None, help="Comma-separated supplemental GIDs")
@click.option("--local-supplementary-groups", default=None, help="Comma-separated supplemental group names")
@click.option("--local-umask", default="0022")
@click.option("--ro-mount", "ro_mounts", multiple=True, help="Host:container read-only mount")
@click.option("--rw-mount", "rw_mounts", multiple=True, help="Host:container read-write mount")
@click.option("--env-var", "env_vars", multiple=True, help="Additional environment variable KEY=VALUE")
@click.option(
    "--setup-script",
    default=None,
    help="Multiline setup commands run sequentially in the container project directory.",
)
@click.option(
    "--snapshot-image-tag",
    default=None,
    help="Project setup snapshot image tag. If present, this image is reused or built once from setup script.",
)
@click.option(
    "--prepare-snapshot-only",
    is_flag=True,
    default=False,
    help="Build/reuse snapshot image and exit without starting the agent.",
)
@click.option(
    "--no-alt-screen",
    is_flag=True,
    default=False,
    help="Pass --no-alt-screen to codex when launching the agent.",
)
@click.option(
    "--tty/--no-tty",
    "allocate_tty",
    default=True,
    show_default=True,
    help="Allocate a pseudo-TTY for docker run.",
)
@click.option("--resume", is_flag=True, default=False, help="Resume last session")
@click.argument("container_args", nargs=-1)
def main(
    project: str,
    agent_command: str,
    container_home: str | None,
    container_project_name: str | None,
    agent_home_path: str | None,
    config_file: str,
    system_prompt_file: str,
    openai_api_key: str | None,
    credentials_file: str,
    git_credential_file: str | None,
    git_credential_host: str | None,
    git_credential_scheme: str | None,
    base_docker_path: str | None,
    base_docker_context: str | None,
    base_dockerfile: str | None,
    base_image: str,
    base_image_tag: str | None,
    local_user: str | None,
    local_group: str | None,
    local_uid: int | None,
    local_gid: int | None,
    local_supplementary_gids: str | None,
    local_supplementary_groups: str | None,
    local_umask: str,
    ro_mounts: tuple[str, ...],
    rw_mounts: tuple[str, ...],
    env_vars: tuple[str, ...],
    setup_script: str | None,
    snapshot_image_tag: str | None,
    prepare_snapshot_only: bool,
    no_alt_screen: bool,
    allocate_tty: bool,
    resume: bool,
    container_args: tuple[str, ...],
) -> None:
    if shutil.which("docker") is None:
        raise click.ClickException("docker command not found in PATH")

    cwd = Path.cwd().resolve()
    project_path = _to_absolute(project, cwd)
    if not project_path.is_dir():
        raise click.ClickException(f"Project path does not exist: {project_path}")

    config_path = _to_absolute(config_file, cwd)
    if not config_path.is_file():
        fallback = _default_config_file()
        if fallback.is_file():
            config_path = fallback
        else:
            raise click.ClickException(f"Agent config file does not exist: {config_path}")
    if not config_path.is_file():
        raise click.ClickException(f"Agent config file does not exist: {config_path}")

    system_prompt_path = _to_absolute(system_prompt_file, cwd)
    if not system_prompt_path.is_file():
        fallback = _default_system_prompt_file()
        if fallback.is_file():
            system_prompt_path = fallback
        else:
            raise click.ClickException(f"System prompt file does not exist: {system_prompt_path}")
    if not system_prompt_path.is_file():
        raise click.ClickException(f"System prompt file does not exist: {system_prompt_path}")
    core_system_prompt = _read_system_prompt(system_prompt_path)

    git_credential_path: Path | None = None
    git_credential_host_value = ""
    git_credential_scheme_value = ""

    if git_credential_file:
        git_credential_path = _to_absolute(git_credential_file, cwd)
        if not git_credential_path.is_file():
            raise click.ClickException(f"Git credential file does not exist: {git_credential_path}")
    if git_credential_host:
        git_credential_host_value = _normalize_git_credential_host(git_credential_host)
    if git_credential_scheme:
        git_credential_scheme_value = _normalize_git_credential_scheme(git_credential_scheme)

    if git_credential_path is None and not git_credential_host_value:
        discovered_path, discovered_host, discovered_scheme = _discover_agent_hub_git_credentials()
        if discovered_path is not None and discovered_host:
            git_credential_path = discovered_path
            git_credential_host_value = discovered_host
            git_credential_scheme_value = discovered_scheme or GIT_CREDENTIAL_DEFAULT_SCHEME

    if bool(git_credential_path) != bool(git_credential_host_value):
        raise click.ClickException(
            "--git-credential-file and --git-credential-host must be provided together"
        )
    if git_credential_host_value and not git_credential_scheme_value:
        git_credential_scheme_value = GIT_CREDENTIAL_DEFAULT_SCHEME
    if git_credential_scheme_value and not git_credential_host_value:
        raise click.ClickException(
            "--git-credential-scheme requires --git-credential-host"
        )

    uid = local_uid if local_uid is not None else os.getuid()
    user = str(local_user or "").strip() or f"uid-{uid}"
    if local_gid is not None:
        gid = local_gid
    elif local_group:
        gid = _gid_for_group_name(local_group)
    else:
        gid = os.getgid()

    if local_supplementary_gids is not None:
        supp_gids_csv = _normalize_csv(local_supplementary_gids)
    elif local_supplementary_groups is not None:
        supp_gids_csv = _group_names_to_gid_csv(local_supplementary_groups)
    else:
        supp_gids_csv = _default_supplementary_gids()
    supplemental_group_ids = [supp_gid for supp_gid in _parse_gid_csv(supp_gids_csv) if supp_gid != gid]
    docker_socket_gid = _docker_socket_gid()
    if (
        docker_socket_gid is not None
        and docker_socket_gid != gid
        and docker_socket_gid not in supplemental_group_ids
    ):
        supplemental_group_ids.append(docker_socket_gid)

    container_home_path = str(container_home or DEFAULT_CONTAINER_HOME).strip() or DEFAULT_CONTAINER_HOME
    if not container_home_path.startswith("/"):
        raise click.ClickException(f"Invalid --container-home: {container_home_path} (must be absolute)")
    resolved_container_project_name = _normalize_container_project_name(container_project_name, project_path.name)
    container_project_path = str(_normalize_container_path(str(PurePosixPath(container_home_path) / resolved_container_project_name)))
    container_project_root = _normalize_container_path(container_project_path)

    host_agent_home = Path(agent_home_path or (Path.home() / ".agent-home" / user)).resolve()
    host_codex_dir = host_agent_home / ".codex"
    host_claude_dir = host_agent_home / ".claude"
    host_claude_json_file = host_agent_home / ".claude.json"
    host_claude_config_dir = host_agent_home / ".config" / "claude"
    host_gemini_dir = host_agent_home / ".gemini"
    host_gemini_settings_file = host_gemini_dir / GEMINI_SETTINGS_FILE_NAME
    host_codex_dir.mkdir(parents=True, exist_ok=True)
    host_claude_dir.mkdir(parents=True, exist_ok=True)
    _ensure_claude_json_file(host_claude_json_file)
    _ensure_gemini_settings_file(host_gemini_settings_file)
    host_claude_config_dir.mkdir(parents=True, exist_ok=True)
    host_gemini_dir.mkdir(parents=True, exist_ok=True)
    (host_agent_home / "projects").mkdir(parents=True, exist_ok=True)
    selected_agent_command = _normalize_agent_command(agent_command)

    api_key = openai_api_key
    if not api_key:
        api_key = _read_openai_api_key(_to_absolute(credentials_file, cwd))

    selected_agent_provider = _agent_provider_for_command(selected_agent_command)
    snapshot_tag = (snapshot_image_tag or "").strip()
    cached_snapshot_exists = bool(snapshot_tag) and _docker_image_exists(snapshot_tag)
    if cached_snapshot_exists:
        click.echo(f"Using cached setup snapshot image '{snapshot_tag}'")

    selected_base_image = ""
    selected_base_image_resolved = False

    def ensure_selected_base_image() -> str:
        nonlocal selected_base_image, selected_base_image_resolved
        if selected_base_image_resolved:
            return selected_base_image

        selected_base_image = base_image
        if base_docker_path or base_docker_context or base_dockerfile:
            _, resolved_context, resolved_dockerfile = _resolve_base_image(
                base_docker_path,
                base_docker_context,
                base_dockerfile,
                project_path,
                cwd,
            )
            if resolved_dockerfile is None or resolved_context is None:
                raise click.ClickException("Unable to resolve a valid base docker source")

            tag = base_image_tag or (
                f"agent-base-{_sanitize_tag_component(project_path.name)}-"
                f"{_sanitize_tag_component(resolved_context.name)}-"
                f"{_short_hash(str(resolved_dockerfile))}"
            )

            click.echo(f"Building base image '{tag}' from {resolved_dockerfile}")
            _run(["docker", "build", "-f", str(resolved_dockerfile), "-t", tag, str(resolved_context)])
            selected_base_image = tag

        selected_base_image_resolved = True
        return selected_base_image

    ro_mount_flags: list[str] = []
    rw_mount_flags: list[str] = []
    rw_mount_specs: list[tuple[Path, str]] = []

    for mount in ro_mounts:
        _reject_mount_inside_project_path(spec=mount, label="--ro-mount", container_project_path=container_project_root)
        host, container = _parse_mount(mount, "--ro-mount")
        ro_mount_flags.append(f"{host}:{container}:ro")

    for mount in rw_mounts:
        _reject_mount_inside_project_path(spec=mount, label="--rw-mount", container_project_path=container_project_root)
        host, container = _parse_mount(mount, "--rw-mount")
        rw_mount_flags.append(f"{host}:{container}")
        rw_mount_specs.append((Path(host), container))

    parsed_env_vars: list[str] = []
    for entry in env_vars:
        parsed_env_vars.append(_parse_env_var(entry, "--env-var"))

    explicit_container_args = [str(arg) for arg in container_args]
    shared_prompt_context = _shared_prompt_context_from_config(
        config_path,
        core_system_prompt=core_system_prompt,
    )
    
    agent_provider = agent_providers.get_provider(selected_agent_provider)
    agent_provider.sync_shared_context_file(
        host_agent_home=host_agent_home / f".{agent_provider.name}",
        shared_prompt_context=shared_prompt_context,
    )

    runtime_flags = agent_provider.default_runtime_flags(
        explicit_args=explicit_container_args,
        shared_prompt_context=shared_prompt_context,
        no_alt_screen=no_alt_screen,
    )

    command = [selected_agent_command]
    command.extend(runtime_flags)

    if container_args:
        command.extend(explicit_container_args)
    elif resume:
        command = [
            "bash",
            "-lc",
            agent_provider.resume_shell_command(
                no_alt_screen=no_alt_screen,
                runtime_flags=runtime_flags,
            ),
        ]

    config_mount_target = f"{container_home_path}/.codex/config.toml:ro"
    mcp_config_mount_target = agent_provider.get_mcp_config_mount_target(container_home_path)
    mcp_config_mount_mode = ":ro"
    if isinstance(agent_provider, (agent_providers.ClaudeProvider, agent_providers.GeminiProvider)):
        mcp_config_mount_mode = ""
    mcp_config_mount_entry = (
        f"{host_gemini_settings_file}:{mcp_config_mount_target}{mcp_config_mount_mode}"
        if isinstance(agent_provider, agent_providers.GeminiProvider)
        else None
    )
    config_mount_entry = f"{config_path}:{config_mount_target}"
    run_args = [
        "--init",
        "--user",
        f"{uid}:{gid}",
        "--gpus",
        "all",
        "--workdir",
        container_project_path,
        "--volume",
        f"{project_path}:{container_project_path}",
        "--volume",
        f"{DOCKER_SOCKET_PATH}:{DOCKER_SOCKET_PATH}",
        "--volume",
        f"{host_codex_dir}:{container_home_path}/.codex",
        "--volume",
        f"{host_claude_dir}:{container_home_path}/.claude",
        "--volume",
        f"{host_claude_json_file}:{container_home_path}/.claude.json",
        "--volume",
        f"{host_claude_config_dir}:{container_home_path}/.config/claude",
        "--volume",
        f"{host_gemini_dir}:{container_home_path}/.gemini",
        "--volume",
        config_mount_entry,
        *(
            ["--volume", mcp_config_mount_entry]
            if mcp_config_mount_entry is not None
            else []
        ),
        "--env",
        f"LOCAL_UMASK={local_umask}",
        "--env",
        f"HOME={container_home_path}",
        "--env",
        f"CONTAINER_HOME={container_home_path}",
        "--env",
        f"PATH={container_home_path}/.codex/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
        "--env",
        f"TERM={_resolved_runtime_term()}",
        "--env",
        f"COLORTERM={_resolved_runtime_colorterm()}",
        "--env",
        "NVIDIA_VISIBLE_DEVICES=all",
        "--env",
        "NVIDIA_DRIVER_CAPABILITIES=all",
        "--env",
        f"CONTAINER_PROJECT_PATH={container_project_path}",
        "--env",
        f"UV_PROJECT_ENVIRONMENT={container_project_path}/.venv",
    ]

    for supplemental_gid in supplemental_group_ids:
        run_args.extend(["--group-add", str(supplemental_gid)])

    if sys.platform.startswith("linux"):
        run_args.extend(["--add-host", "host.docker.internal:host-gateway"])

    if api_key:
        run_args.extend(["--env", f"OPENAI_API_KEY={api_key}"])

    if git_credential_path is not None and git_credential_host_value:
        git_prefix = f"{git_credential_scheme_value}://{git_credential_host_value}/"
        git_credential_ssh_host = git_credential_host_value.split(":", 1)[0]
        run_args.extend(["--volume", f"{git_credential_path}:{GIT_CREDENTIALS_SOURCE_PATH}:ro"])
        run_args.extend(["--env", "GIT_TERMINAL_PROMPT=0"])
        run_args.extend(["--env", f"AGENT_HUB_GIT_CREDENTIALS_SOURCE={GIT_CREDENTIALS_SOURCE_PATH}"])
        run_args.extend(["--env", f"AGENT_HUB_GIT_CREDENTIALS_FILE={GIT_CREDENTIALS_FILE_PATH}"])
        run_args.extend(["--env", f"AGENT_HUB_GIT_CREDENTIAL_HOST={git_credential_host_value}"])
        run_args.extend(["--env", f"AGENT_HUB_GIT_CREDENTIAL_SCHEME={git_credential_scheme_value}"])
        run_args.extend(["--env", "GIT_CONFIG_COUNT=3"])
        run_args.extend(["--env", "GIT_CONFIG_KEY_0=credential.helper"])
        run_args.extend(["--env", f"GIT_CONFIG_VALUE_0=store --file={GIT_CREDENTIALS_FILE_PATH}"])
        run_args.extend(["--env", f"GIT_CONFIG_KEY_1=url.{git_prefix}.insteadOf"])
        run_args.extend(["--env", f"GIT_CONFIG_VALUE_1=git@{git_credential_ssh_host}:"])
        run_args.extend(["--env", f"GIT_CONFIG_KEY_2=url.{git_prefix}.insteadOf"])
        run_args.extend(["--env", f"GIT_CONFIG_VALUE_2=ssh://git@{git_credential_ssh_host}/"])

    for env_entry in parsed_env_vars:
        run_args.extend(["--env", env_entry])

    for mount in ro_mount_flags + rw_mount_flags:
        run_args.extend(["--volume", mount])

    runtime_image = _default_runtime_image_for_provider(selected_agent_provider)
    if snapshot_tag:
        setup_runtime_image = _snapshot_setup_runtime_image_for_snapshot(snapshot_tag)
        should_build_snapshot = not cached_snapshot_exists
        if should_build_snapshot:
            click.echo(
                f"Running RW mount preflight checks for setup snapshot '{snapshot_tag}'",
                err=True,
            )
            for host_path, container_path in rw_mount_specs:
                _validate_rw_mount(host_path, container_path, runtime_uid=uid, runtime_gid=gid)
            _ensure_runtime_image_built_if_missing(
                base_image=ensure_selected_base_image(),
                target_image=setup_runtime_image,
                agent_provider=AGENT_PROVIDER_NONE,
                recursive_workspace_chmod=True,
            )
            script = (setup_script or "").strip() or ":"
            setup_bootstrap_script = _build_snapshot_setup_shell_script(script)
            click.echo(f"Building setup snapshot image '{snapshot_tag}'")
            container_name = (
                f"agent-setup-{_sanitize_tag_component(project_path.name)}-"
                f"{_short_hash(snapshot_tag + script)}"
            )
            setup_cmd = [
                "docker",
                "run",
                "--name",
                container_name,
                "--entrypoint",
                "bash",
                *run_args,
                setup_runtime_image,
                "-lc",
                setup_bootstrap_script,
            ]
            _docker_rm_force(container_name)
            try:
                _run(setup_cmd)
                _run(
                    [
                        "docker",
                        "commit",
                        "--change",
                        "USER root",
                        "--change",
                        f"WORKDIR {DEFAULT_CONTAINER_HOME}",
                        "--change",
                        'ENTRYPOINT ["/usr/local/bin/docker-entrypoint.py"]',
                        "--change",
                        'CMD ["bash"]',
                        container_name,
                        snapshot_tag,
                    ]
                )
            finally:
                _docker_rm_force(container_name)
        runtime_image = snapshot_tag
        if not prepare_snapshot_only and selected_agent_provider in {
            AGENT_PROVIDER_CODEX,
            AGENT_PROVIDER_CLAUDE,
            AGENT_PROVIDER_GEMINI,
        }:
            provider_snapshot_runtime_image = _snapshot_runtime_image_for_provider(
                snapshot_tag,
                selected_agent_provider,
            )
            _ensure_runtime_image_built_if_missing(
                base_image=snapshot_tag,
                target_image=provider_snapshot_runtime_image,
                agent_provider=selected_agent_provider,
                recursive_workspace_chmod=False,
            )
            runtime_image = provider_snapshot_runtime_image
    elif prepare_snapshot_only:
        raise click.ClickException("--prepare-snapshot-only requires --snapshot-image-tag")
    else:
        _build_runtime_image(
            base_image=ensure_selected_base_image(),
            target_image=runtime_image,
            agent_provider=selected_agent_provider,
            recursive_workspace_chmod=True,
        )

    if prepare_snapshot_only:
        return

    runtime_bridge: _AgentToolsRuntimeBridge | None = None
    runtime_run_args = list(run_args)
    try:
        runtime_bridge = _start_agent_tools_runtime_bridge(
            project_path=project_path,
            host_codex_dir=host_codex_dir,
            config_path=config_path,
            system_prompt_path=system_prompt_path,
            agent_tools_config_path=(
                host_claude_json_file
                if isinstance(agent_provider, agent_providers.ClaudeProvider)
                else host_gemini_settings_file
                if isinstance(agent_provider, agent_providers.GeminiProvider)
                else None
            ),
            parsed_env_vars=parsed_env_vars,
            agent_provider=agent_provider,
            container_home=container_home_path,
        )
        if runtime_bridge is not None:
            runtime_run_args = list(run_args)
            runtime_mount = f"{runtime_bridge.runtime_config_path}:{mcp_config_mount_target}{mcp_config_mount_mode}"
            mcp_mount_target = mcp_config_mount_target

            replaced_mount = False
            for index in range(len(runtime_run_args) - 1):
                if runtime_run_args[index] != "--volume":
                    continue
                current_mount = str(runtime_run_args[index + 1])
                # Docker mount syntax here is host:container[:mode]; host paths are absolute POSIX paths.
                parts = current_mount.split(":")
                if len(parts) < 2:
                    continue
                current_target = parts[1]
                if current_target != mcp_mount_target:
                    continue
                runtime_run_args[index + 1] = runtime_mount
                replaced_mount = True
                break

            if not replaced_mount:
                runtime_run_args.extend(["--volume", runtime_mount])
            for runtime_env in runtime_bridge.env_vars:
                runtime_run_args.extend(["--env", runtime_env])

        cmd = [
            "docker",
            "run",
            "--rm",
            "-i",
            *(["-t"] if allocate_tty else []),
            "--tmpfs",
            TMP_DIR_TMPFS_SPEC,
            *runtime_run_args,
            runtime_image,
            *command,
        ]

        _run(cmd)
    finally:
        if runtime_bridge is not None:
            runtime_bridge.close()


if __name__ == "__main__":
    main()
