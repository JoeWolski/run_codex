from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Iterable, Tuple

import click


DEFAULT_BASE_IMAGE = "nvidia/cuda:12.2.2-cudnn8-devel-ubuntu22.04"
DEFAULT_SETUP_RUNTIME_IMAGE = "agent-ubuntu2204-setup:latest"
DEFAULT_RUNTIME_IMAGE = "agent-ubuntu2204-codex:latest"
CLAUDE_RUNTIME_IMAGE = "agent-ubuntu2204-claude:latest"
DEFAULT_DOCKERFILE = "docker/agent_cli/Dockerfile"
DEFAULT_AGENT_COMMAND = "codex"
AGENT_PROVIDER_NONE = "none"
AGENT_PROVIDER_CODEX = "codex"
AGENT_PROVIDER_CLAUDE = "claude"
DEFAULT_CODEX_APPROVAL_POLICY = "never"
DEFAULT_CODEX_SANDBOX_MODE = "danger-full-access"
DEFAULT_CLAUDE_PERMISSION_MODE = "bypassPermissions"
DOCKER_SOCKET_PATH = "/var/run/docker.sock"
GIT_CREDENTIALS_SOURCE_PATH = "/tmp/agent_hub_git_credentials_source"
GIT_CREDENTIALS_FILE_PATH = "/tmp/agent_hub_git_credentials"


def _cli_arg_matches_option(arg: str, *, long_option: str, short_option: str | None = None) -> bool:
    if arg == long_option or arg.startswith(f"{long_option}="):
        return True
    if short_option and (arg == short_option or arg.startswith(f"{short_option}=")):
        return True
    return False


def _has_cli_option(args: Iterable[str], *, long_option: str, short_option: str | None = None) -> bool:
    return any(_cli_arg_matches_option(arg, long_option=long_option, short_option=short_option) for arg in args)


def _codex_default_runtime_flags(*, no_alt_screen: bool, explicit_args: Iterable[str]) -> list[str]:
    parsed_args = [str(arg) for arg in explicit_args]
    flags: list[str] = []
    bypass_all = _has_cli_option(
        parsed_args,
        long_option="--dangerously-bypass-approvals-and-sandbox",
    )
    if not bypass_all:
        if not _has_cli_option(parsed_args, long_option="--ask-for-approval", short_option="-a"):
            flags.extend(["--ask-for-approval", DEFAULT_CODEX_APPROVAL_POLICY])
        if not _has_cli_option(parsed_args, long_option="--sandbox", short_option="-s"):
            flags.extend(["--sandbox", DEFAULT_CODEX_SANDBOX_MODE])

    if no_alt_screen:
        flags.append("--no-alt-screen")
    return flags


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


def _shared_prompt_context_from_config(config_path: Path) -> str:
    try:
        raw = config_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return ""

    try:
        parsed = tomllib.loads(raw)
    except tomllib.TOMLDecodeError as exc:
        click.echo(
            f"Warning: unable to parse shared prompt context from {config_path}: {exc}",
            err=True,
        )
        return ""

    if not isinstance(parsed, dict):
        return ""

    developer_instructions = str(parsed.get("developer_instructions") or "").strip()
    project_doc_auto_load = parsed.get("project_doc_auto_load") is True
    doc_fallback_files = _normalize_string_list(parsed.get("project_doc_fallback_filenames"))
    doc_extra_files = _normalize_string_list(parsed.get("project_doc_auto_load_extra_filenames"))
    project_doc_max_bytes = parsed.get("project_doc_max_bytes")

    sections: list[str] = []
    if developer_instructions:
        sections.append(developer_instructions)

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


def _claude_default_runtime_flags(*, explicit_args: Iterable[str], shared_prompt_context: str) -> list[str]:
    parsed_args = [str(arg) for arg in explicit_args]
    flags: list[str] = []
    if not _has_cli_option(parsed_args, long_option="--dangerously-skip-permissions") and not _has_cli_option(
        parsed_args, long_option="--permission-mode"
    ):
        flags.extend(["--permission-mode", DEFAULT_CLAUDE_PERMISSION_MODE])

    has_explicit_system_prompt = _has_cli_option(parsed_args, long_option="--append-system-prompt") or _has_cli_option(
        parsed_args, long_option="--append-system-prompt-file"
    )
    if shared_prompt_context and not has_explicit_system_prompt:
        flags.extend(["--append-system-prompt", shared_prompt_context])

    return flags


def _resume_shell_command(*, no_alt_screen: bool, agent_command: str, codex_runtime_flags: Iterable[str] = ()) -> str:
    resolved_command = str(agent_command or DEFAULT_AGENT_COMMAND).strip() or DEFAULT_AGENT_COMMAND
    command_parts = [resolved_command]
    if resolved_command == DEFAULT_AGENT_COMMAND:
        command_parts.extend(str(flag) for flag in codex_runtime_flags)
    elif no_alt_screen:
        command_parts.append("--no-alt-screen")
    resolved = " ".join(command_parts)
    return f"if {resolved} resume --last; then :; else exec {resolved}; fi"


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


def _default_credentials_file() -> Path:
    return _repo_root() / ".credentials"


def _default_user() -> str:
    try:
        return os.getlogin()
    except OSError:
        import pwd

        return pwd.getpwuid(os.getuid()).pw_name


def _default_group_name() -> str:
    import grp

    return grp.getgrgid(os.getgid()).gr_name


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
    return AGENT_PROVIDER_NONE


def _default_runtime_image_for_provider(agent_provider: str) -> str:
    if agent_provider == AGENT_PROVIDER_CLAUDE:
        return CLAUDE_RUNTIME_IMAGE
    if agent_provider == AGENT_PROVIDER_CODEX:
        return DEFAULT_RUNTIME_IMAGE
    return DEFAULT_SETUP_RUNTIME_IMAGE


def _snapshot_runtime_image_for_provider(snapshot_tag: str, agent_provider: str) -> str:
    return f"agent-runtime-{_sanitize_tag_component(agent_provider)}-{_short_hash(snapshot_tag)}"


def _build_runtime_image(*, base_image: str, target_image: str, agent_provider: str) -> None:
    click.echo(
        f"Building runtime image '{target_image}' from {DEFAULT_DOCKERFILE} "
        f"(base={base_image}, provider={agent_provider})"
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
            "-t",
            target_image,
            str(_repo_root()),
        ],
        cwd=_repo_root(),
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


def _normalize_git_credential_host(raw_value: str) -> str:
    host = str(raw_value or "").strip().lower()
    if not host:
        raise click.ClickException("Git credential host is required.")
    if not re.fullmatch(r"[a-z0-9.-]+", host):
        raise click.ClickException(f"Invalid git credential host: {raw_value}")
    return host


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
    help="Agent executable launched inside the container (for example codex or claude)",
)
@click.option("--container-home", default=None, help="Container home path for mapped user")
@click.option("--agent-home-path", default=None, help="Host path for persistent agent state")
@click.option(
    "--config-file",
    default=str(_default_config_file()),
    show_default=True,
    help="Host agent config file mounted into container",
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
@click.option("--resume", is_flag=True, default=False, help="Resume last session")
@click.argument("container_args", nargs=-1)
def main(
    project: str,
    agent_command: str,
    container_home: str | None,
    agent_home_path: str | None,
    config_file: str,
    openai_api_key: str | None,
    credentials_file: str,
    git_credential_file: str | None,
    git_credential_host: str | None,
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

    git_credential_path: Path | None = None
    git_credential_host_value = ""

    if git_credential_file:
        git_credential_path = _to_absolute(git_credential_file, cwd)
        if not git_credential_path.is_file():
            raise click.ClickException(f"Git credential file does not exist: {git_credential_path}")
    if git_credential_host:
        git_credential_host_value = _normalize_git_credential_host(git_credential_host)
    if bool(git_credential_path) != bool(git_credential_host_value):
        raise click.ClickException(
            "--git-credential-file and --git-credential-host must be provided together"
        )

    user = local_user or _default_user()
    group = local_group or _default_group_name()
    uid = local_uid if local_uid is not None else os.getuid()
    gid = local_gid if local_gid is not None else os.getgid()

    supp_gids = _normalize_csv(
        local_supplementary_gids
        if local_supplementary_gids is not None
        else _default_supplementary_gids()
    )
    supp_groups = _normalize_csv(
        local_supplementary_groups
        if local_supplementary_groups is not None
        else _default_supplementary_groups()
    )

    container_home_path = container_home or f"/home/{user}"
    container_project_name = project_path.name
    container_project_path = f"{container_home_path}/projects/{container_project_name}"

    host_agent_home = Path(agent_home_path or (Path.home() / ".agent-home" / user)).resolve()
    host_codex_dir = host_agent_home / ".codex"
    host_claude_dir = host_agent_home / ".claude"
    host_claude_json_file = host_agent_home / ".claude.json"
    host_claude_config_dir = host_agent_home / ".config" / "claude"
    host_codex_dir.mkdir(parents=True, exist_ok=True)
    host_claude_dir.mkdir(parents=True, exist_ok=True)
    host_claude_json_file.touch(exist_ok=True)
    host_claude_config_dir.mkdir(parents=True, exist_ok=True)
    (host_agent_home / "projects").mkdir(parents=True, exist_ok=True)
    selected_agent_command = _normalize_agent_command(agent_command)

    api_key = openai_api_key
    if not api_key:
        api_key = _read_openai_api_key(_to_absolute(credentials_file, cwd))

    if not api_key:
        click.echo("OPENAI_API_KEY not set. Starting without API key.", err=True)

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

    for mount in ro_mounts:
        host, container = _parse_mount(mount, "--ro-mount")
        ro_mount_flags.append(f"{host}:{container}:ro")

    for mount in rw_mounts:
        host, container = _parse_mount(mount, "--rw-mount")
        rw_mount_flags.append(f"{host}:{container}")

    parsed_env_vars: list[str] = []
    for entry in env_vars:
        parsed_env_vars.append(_parse_env_var(entry, "--env-var"))

    explicit_container_args = [str(arg) for arg in container_args]
    codex_runtime_flags = _codex_default_runtime_flags(
        no_alt_screen=no_alt_screen,
        explicit_args=explicit_container_args,
    )
    shared_prompt_context = ""
    if selected_agent_command == AGENT_PROVIDER_CLAUDE:
        shared_prompt_context = _shared_prompt_context_from_config(config_path)

    command = [selected_agent_command]
    if selected_agent_command == DEFAULT_AGENT_COMMAND:
        command.extend(codex_runtime_flags)
    elif selected_agent_command == AGENT_PROVIDER_CLAUDE:
        command.extend(
            _claude_default_runtime_flags(
                explicit_args=explicit_container_args,
                shared_prompt_context=shared_prompt_context,
            )
        )

    if container_args:
        command.extend(explicit_container_args)
    elif resume:
        if selected_agent_command != DEFAULT_AGENT_COMMAND:
            raise click.ClickException("--resume is currently only supported when --agent-command is codex.")
        command = [
            "bash",
            "-lc",
            _resume_shell_command(
                no_alt_screen=no_alt_screen,
                agent_command=selected_agent_command,
                codex_runtime_flags=codex_runtime_flags,
            ),
        ]

    run_args = [
        "--init",
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
        f"{config_path}:{container_home_path}/.codex/config.toml:ro",
        "--env",
        f"LOCAL_USER={user}",
        "--env",
        f"LOCAL_GROUP={group}",
        "--env",
        f"LOCAL_UID={uid}",
        "--env",
        f"LOCAL_GID={gid}",
        "--env",
        f"LOCAL_SUPP_GIDS={supp_gids}",
        "--env",
        f"LOCAL_SUPP_GROUPS={supp_groups}",
        "--env",
        f"LOCAL_HOME={container_home_path}",
        "--env",
        f"LOCAL_UMASK={local_umask}",
        "--env",
        f"HOME={container_home_path}",
        "--env",
        f"CONTAINER_HOME={container_home_path}",
        "--env",
        f"PATH={container_home_path}/.codex/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
        "--env",
        "NVIDIA_VISIBLE_DEVICES=all",
        "--env",
        "NVIDIA_DRIVER_CAPABILITIES=all",
        "--env",
        f"CONTAINER_PROJECT_PATH={container_project_path}",
    ]

    if sys.platform.startswith("linux"):
        run_args.extend(["--add-host", "host.docker.internal:host-gateway"])

    if api_key:
        run_args.extend(["--env", f"OPENAI_API_KEY={api_key}"])

    if git_credential_path is not None and git_credential_host_value:
        https_prefix = f"https://{git_credential_host_value}/"
        run_args.extend(["--volume", f"{git_credential_path}:{GIT_CREDENTIALS_SOURCE_PATH}:ro"])
        run_args.extend(["--env", "GIT_TERMINAL_PROMPT=0"])
        run_args.extend(["--env", f"AGENT_HUB_GIT_CREDENTIALS_SOURCE={GIT_CREDENTIALS_SOURCE_PATH}"])
        run_args.extend(["--env", f"AGENT_HUB_GIT_CREDENTIALS_FILE={GIT_CREDENTIALS_FILE_PATH}"])
        run_args.extend(["--env", f"AGENT_HUB_GIT_CREDENTIAL_HOST={git_credential_host_value}"])
        run_args.extend(["--env", "GIT_CONFIG_COUNT=3"])
        run_args.extend(["--env", "GIT_CONFIG_KEY_0=credential.helper"])
        run_args.extend(["--env", f"GIT_CONFIG_VALUE_0=store --file={GIT_CREDENTIALS_FILE_PATH}"])
        run_args.extend(["--env", f"GIT_CONFIG_KEY_1=url.{https_prefix}.insteadOf"])
        run_args.extend(["--env", f"GIT_CONFIG_VALUE_1=git@{git_credential_host_value}:"])
        run_args.extend(["--env", f"GIT_CONFIG_KEY_2=url.{https_prefix}.insteadOf"])
        run_args.extend(["--env", f"GIT_CONFIG_VALUE_2=ssh://git@{git_credential_host_value}/"])

    for env_entry in parsed_env_vars:
        run_args.extend(["--env", env_entry])

    for mount in ro_mount_flags + rw_mount_flags:
        run_args.extend(["--volume", mount])

    runtime_image = _default_runtime_image_for_provider(selected_agent_provider)
    if snapshot_tag:
        should_build_snapshot = not cached_snapshot_exists
        if should_build_snapshot:
            _build_runtime_image(
                base_image=ensure_selected_base_image(),
                target_image=DEFAULT_SETUP_RUNTIME_IMAGE,
                agent_provider=AGENT_PROVIDER_NONE,
            )
            script = (setup_script or "").strip() or ":"
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
                DEFAULT_SETUP_RUNTIME_IMAGE,
                "-lc",
                (
                    "set -e\n"
                    "git config --system --add safe.directory '*' || true\n"
                    'if [ -n "${AGENT_HUB_GIT_CREDENTIALS_SOURCE:-}" ] && [ -f "${AGENT_HUB_GIT_CREDENTIALS_SOURCE}" ]; then\n'
                    '  cp "${AGENT_HUB_GIT_CREDENTIALS_SOURCE}" "${AGENT_HUB_GIT_CREDENTIALS_FILE:-/tmp/agent_hub_git_credentials}" || true\n'
                    '  chmod 600 "${AGENT_HUB_GIT_CREDENTIALS_FILE:-/tmp/agent_hub_git_credentials}" || true\n'
                    "fi\n"
                    + script
                    + "\n"
                    + 'chown -R "${LOCAL_UID}:${LOCAL_GID}" "${CONTAINER_PROJECT_PATH}" || true\n'
                ),
            ]
            _docker_rm_force(container_name)
            try:
                _run(setup_cmd)
                _run(
                    [
                        "docker",
                        "commit",
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
        if not prepare_snapshot_only and selected_agent_provider in {AGENT_PROVIDER_CODEX, AGENT_PROVIDER_CLAUDE}:
            provider_snapshot_runtime_image = _snapshot_runtime_image_for_provider(
                snapshot_tag,
                selected_agent_provider,
            )
            if not _docker_image_exists(provider_snapshot_runtime_image):
                _build_runtime_image(
                    base_image=snapshot_tag,
                    target_image=provider_snapshot_runtime_image,
                    agent_provider=selected_agent_provider,
                )
            runtime_image = provider_snapshot_runtime_image
    elif prepare_snapshot_only:
        raise click.ClickException("--prepare-snapshot-only requires --snapshot-image-tag")
    else:
        _build_runtime_image(
            base_image=ensure_selected_base_image(),
            target_image=runtime_image,
            agent_provider=selected_agent_provider,
        )

    if prepare_snapshot_only:
        return

    cmd = [
        "docker",
        "run",
        "--rm",
        "-i",
        "-t",
        *run_args,
        runtime_image,
        *command,
    ]

    _run(cmd)


if __name__ == "__main__":
    main()
