from __future__ import annotations

import abc
import json
import re
from pathlib import Path
from typing import Iterable

def _strip_mcp_server_toml(config_text: str, server_name: str) -> str:
    if not config_text:
        return ""
    escaped_name = re.escape(server_name)
    pattern = re.compile(r"(?ms)^\[mcp_servers\." + escaped_name + r"(?:\.[^\]]+)?\]\n.*?(?=^\[|\Z)")
    stripped = re.sub(pattern, "", config_text)
    return stripped.rstrip() + "\n"

class AgentProvider(abc.ABC):
    @property
    @abc.abstractmethod
    def name(self) -> str:
        """The internal identifier for the agent provider (e.g., 'codex', 'claude', 'gemini')."""
        pass

    @abc.abstractmethod
    def default_runtime_flags(
        self,
        *,
        explicit_args: Iterable[str],
        shared_prompt_context: str,
        no_alt_screen: bool,
    ) -> list[str]:
        """Returns the default flags required to run the agent."""
        pass

    @abc.abstractmethod
    def resume_shell_command(
        self,
        *,
        no_alt_screen: bool,
        runtime_flags: Iterable[str] = ()
    ) -> str:
        """Returns the bash shell command string used to resume the agent's last session."""
        pass

    @abc.abstractmethod
    def get_mcp_config_mount_target(self, container_home: str) -> str:
        """Returns the absolute path where the MCP config file should be mounted inside the container."""
        pass

    @abc.abstractmethod
    def build_mcp_config(
        self,
        base_config_text: str,
        mcp_env: dict[str, str],
        script_path: str,
    ) -> str:
        """Injects the 'agent_tools' MCP server configuration into the base config text."""
        pass

    def sync_shared_context_file(self, host_agent_home: Path, shared_prompt_context: str) -> None:
        """Synchronizes any file-based shared prompt context required by the agent."""
        pass

class CodexProvider(AgentProvider):
    @property
    def name(self) -> str:
        return "codex"

    def default_runtime_flags(
        self,
        *,
        explicit_args: Iterable[str],
        shared_prompt_context: str,
        no_alt_screen: bool,
    ) -> list[str]:
        parsed_args = [str(arg) for arg in explicit_args]
        flags: list[str] = []
        
        def has_cli_option(args: list[str], *, long_option: str, short_option: str | None = None) -> bool:
            return any(arg == long_option or arg.startswith(f"{long_option}=") or (short_option and (arg == short_option or arg.startswith(f"{short_option}="))) for arg in args)
            
        def has_codex_config_override(args: list[str], *, key: str) -> bool:
            for index, arg in enumerate(args):
                if arg in {"--config", "-c"}:
                    if index + 1 >= len(args):
                        continue
                    config_assignment = args[index + 1]
                elif arg.startswith("--config=") or arg.startswith("-c="):
                    _, _, config_assignment = arg.partition("=")
                else:
                    continue
                config_key, _, _ = config_assignment.partition("=")
                if config_key.strip() == key:
                    return True
            return False

        bypass_all = has_cli_option(parsed_args, long_option="--dangerously-bypass-approvals-and-sandbox")
        if not bypass_all:
            if not has_cli_option(parsed_args, long_option="--ask-for-approval", short_option="-a"):
                flags.extend(["--ask-for-approval", "never"])
            if not has_cli_option(parsed_args, long_option="--sandbox", short_option="-s"):
                flags.extend(["--sandbox", "danger-full-access"])

        if shared_prompt_context and not has_codex_config_override(parsed_args, key="developer_instructions"):
            flags.extend(
                [
                    "--config",
                    f"developer_instructions={json.dumps(str(shared_prompt_context or ''))}",
                ]
            )

        if no_alt_screen:
            flags.append("--no-alt-screen")
        return flags

    def resume_shell_command(
        self,
        *,
        no_alt_screen: bool,
        runtime_flags: Iterable[str] = ()
    ) -> str:
        import shlex
        command_parts = ["codex"]
        command_parts.extend(str(flag) for flag in runtime_flags)
        resolved = " ".join(shlex.quote(part) for part in command_parts)
        return f"if {resolved} resume --last; then :; else exec {resolved}; fi"

    def get_mcp_config_mount_target(self, container_home: str) -> str:
        return f"{container_home}/.codex/config.toml"

    def build_mcp_config(
        self,
        base_config_text: str,
        mcp_env: dict[str, str],
        script_path: str,
    ) -> str:
        merged_config = _strip_mcp_server_toml(base_config_text, "agent_tools")
        merged_config += (
            "\n[mcp_servers.agent_tools]\n"
            'command = "python3"\n'
            f"args = [{json.dumps(script_path)}]\n"
            "startup_timeout_sec = 20\n"
            "tool_timeout_sec = 120\n"
            "\n[mcp_servers.agent_tools.env]\n"
        )
        for k, v in mcp_env.items():
            merged_config += f"{k} = {json.dumps(v)}\n"
        return merged_config

class ClaudeProvider(AgentProvider):
    @property
    def name(self) -> str:
        return "claude"

    def default_runtime_flags(
        self,
        *,
        explicit_args: Iterable[str],
        shared_prompt_context: str,
        no_alt_screen: bool,
    ) -> list[str]:
        parsed_args = [str(arg) for arg in explicit_args]
        flags: list[str] = []
        
        def has_cli_option(args: list[str], *, long_option: str, short_option: str | None = None) -> bool:
            return any(arg == long_option or arg.startswith(f"{long_option}=") or (short_option and (arg == short_option or arg.startswith(f"{short_option}="))) for arg in args)

        if not has_cli_option(parsed_args, long_option="--model", short_option="-m"):
            flags.extend(["--model", "opus"])
        if not has_cli_option(parsed_args, long_option="--dangerously-skip-permissions") and not has_cli_option(
            parsed_args, long_option="--permission-mode"
        ):
            flags.extend(["--permission-mode", "bypassPermissions"])

        has_explicit_system_prompt = has_cli_option(parsed_args, long_option="--append-system-prompt") or has_cli_option(
            parsed_args, long_option="--append-system-prompt-file"
        )
        if shared_prompt_context and not has_explicit_system_prompt:
            flags.extend(["--append-system-prompt", shared_prompt_context])

        return flags

    def resume_shell_command(
        self,
        *,
        no_alt_screen: bool,
        runtime_flags: Iterable[str] = ()
    ) -> str:
        import shlex
        command_parts = ["claude"]
        if no_alt_screen:
            command_parts.append("--no-alt-screen")
        resolved = " ".join(shlex.quote(part) for part in command_parts)
        return f"if {resolved} --continue; then :; else exec {resolved}; fi"

    def get_mcp_config_mount_target(self, container_home: str) -> str:
        return f"{container_home}/.claude.json"

    def build_mcp_config(
        self,
        base_config_text: str,
        mcp_env: dict[str, str],
        script_path: str,
    ) -> str:
        try:
            config = json.loads(base_config_text) if base_config_text.strip() else {}
        except json.JSONDecodeError:
            config = {}
        
        if not isinstance(config, dict):
            config = {}

        if "mcpServers" not in config or not isinstance(config["mcpServers"], dict):
            config["mcpServers"] = {}

        config["mcpServers"]["agent_tools"] = {
            "command": "python3",
            "args": [script_path],
            "env": mcp_env
        }
        return json.dumps(config, indent=2)

class GeminiProvider(AgentProvider):
    @property
    def name(self) -> str:
        return "gemini"

    def default_runtime_flags(
        self,
        *,
        explicit_args: Iterable[str],
        shared_prompt_context: str,
        no_alt_screen: bool,
    ) -> list[str]:
        parsed_args = [str(arg) for arg in explicit_args]
        flags: list[str] = []
        
        def has_cli_option(args: list[str], *, long_option: str, short_option: str | None = None) -> bool:
            return any(arg == long_option or arg.startswith(f"{long_option}=") or (short_option and (arg == short_option or arg.startswith(f"{short_option}="))) for arg in args)

        if not has_cli_option(parsed_args, long_option="--approval-mode") and not has_cli_option(
            parsed_args, long_option="--yolo"
        ):
            flags.extend(["--approval-mode", "yolo"])
        return flags

    def resume_shell_command(
        self,
        *,
        no_alt_screen: bool,
        runtime_flags: Iterable[str] = ()
    ) -> str:
        import shlex
        command_parts = ["gemini"]
        if no_alt_screen:
            command_parts.append("--no-alt-screen")
        resolved = " ".join(shlex.quote(part) for part in command_parts)
        return f"if {resolved} --resume; then :; else exec {resolved}; fi"

    def get_mcp_config_mount_target(self, container_home: str) -> str:
        return f"{container_home}/.gemini/config.toml"

    def build_mcp_config(
        self,
        base_config_text: str,
        mcp_env: dict[str, str],
        script_path: str,
    ) -> str:
        merged_config = _strip_mcp_server_toml(base_config_text, "agent_tools")
        merged_config += (
            "\n[mcp_servers.agent_tools]\n"
            'command = "python3"\n'
            f"args = [{json.dumps(script_path)}]\n"
            "startup_timeout_sec = 20\n"
            "tool_timeout_sec = 120\n"
            "\n[mcp_servers.agent_tools.env]\n"
        )
        for k, v in mcp_env.items():
            merged_config += f"{k} = {json.dumps(v)}\n"
        return merged_config

    def sync_shared_context_file(self, host_agent_home: Path, shared_prompt_context: str) -> None:
        import click
        context_file = host_agent_home / "GEMINI.md"
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

def get_provider(name: str) -> AgentProvider:
    providers = {
        "codex": CodexProvider(),
        "claude": ClaudeProvider(),
        "gemini": GeminiProvider(),
    }
    return providers.get(name, CodexProvider())
