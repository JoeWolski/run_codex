from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Iterable, Tuple

import click


DEFAULT_BASE_IMAGE = "nvidia/cuda:12.2.2-cudnn8-devel-ubuntu22.04"
DEFAULT_RUNTIME_IMAGE = "agent-ubuntu2204:latest"
DEFAULT_DOCKERFILE = "docker/Dockerfile"
DEFAULT_AGENT_COMMAND = "codex"
RESUME_COMMAND = f"if {DEFAULT_AGENT_COMMAND} resume --last; then :; else exec {DEFAULT_AGENT_COMMAND}; fi"


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
    container_home: str | None,
    agent_home_path: str | None,
    config_file: str,
    openai_api_key: str | None,
    credentials_file: str,
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
    (host_agent_home / ".codex").mkdir(parents=True, exist_ok=True)
    (host_agent_home / "projects").mkdir(parents=True, exist_ok=True)

    api_key = openai_api_key
    if not api_key:
        api_key = _read_openai_api_key(_to_absolute(credentials_file, cwd))

    if not api_key:
        click.echo("OPENAI_API_KEY not set. Starting without API key.", err=True)

    snapshot_tag = (snapshot_image_tag or "").strip()
    cached_snapshot_exists = bool(snapshot_tag) and _docker_image_exists(snapshot_tag)
    should_build_runtime_image = not cached_snapshot_exists
    if cached_snapshot_exists:
        click.echo(f"Using cached setup snapshot image '{snapshot_tag}'")

    if should_build_runtime_image:
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

        click.echo(f"Building runtime image '{DEFAULT_RUNTIME_IMAGE}' from {DEFAULT_DOCKERFILE}")
        _run(
            [
                "docker",
                "build",
                "-f",
                str(_repo_root() / DEFAULT_DOCKERFILE),
                "--build-arg",
                f"BASE_IMAGE={selected_base_image}",
                "-t",
                DEFAULT_RUNTIME_IMAGE,
                str(_repo_root()),
            ],
            cwd=_repo_root(),
        )

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

    command = [DEFAULT_AGENT_COMMAND]
    if no_alt_screen:
        command.append("--no-alt-screen")
    if container_args:
        command.extend(container_args)
    elif resume:
        command.extend(["bash", "-lc", RESUME_COMMAND])

    run_args = [
        "--init",
        "--gpus",
        "all",
        "--workdir",
        container_project_path,
        "--volume",
        f"{host_agent_home}:{container_home_path}",
        "--volume",
        f"{project_path}:{container_project_path}",
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

    if api_key:
        run_args.extend(["--env", f"OPENAI_API_KEY={api_key}"])

    for env_entry in parsed_env_vars:
        run_args.extend(["--env", env_entry])

    for mount in ro_mount_flags + rw_mount_flags:
        run_args.extend(["--volume", mount])

    runtime_image = DEFAULT_RUNTIME_IMAGE
    if snapshot_tag:
        should_build_snapshot = not cached_snapshot_exists
        if should_build_snapshot:
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
                DEFAULT_RUNTIME_IMAGE,
                "-lc",
                (
                    "set -e\n"
                    "git config --system --add safe.directory '*' || true\n"
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
                        f'CMD ["{DEFAULT_AGENT_COMMAND}"]',
                        container_name,
                        snapshot_tag,
                    ]
                )
            finally:
                _docker_rm_force(container_name)
        runtime_image = snapshot_tag
    elif prepare_snapshot_only:
        raise click.ClickException("--prepare-snapshot-only requires --snapshot-image-tag")

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
