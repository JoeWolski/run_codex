#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import tempfile
import threading
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
TMP_ROOT = Path("/workspace/tmp/agent-hub-preflight")
LOCAL_REPO_TMP_ROOT = REPO_ROOT / ".tmp" / "agent-hub-preflight"
DAEMON_VISIBLE_DIR_ENV = "AGENT_HUB_DAEMON_VISIBLE_DIR"


def _run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, check=False, text=True, capture_output=True)


def _docker_daemon_ok() -> tuple[bool, str]:
    docker = _run(["docker", "info", "--format", "{{.ServerVersion}}"])
    if docker.returncode != 0:
        detail = docker.stderr.strip() or docker.stdout.strip() or "docker info failed"
        return False, detail
    return True, docker.stdout.strip()


def _detect_host_ip() -> str:
    host_ip = ""
    hostname_i = _run(["bash", "-lc", "hostname -I | awk '{print $1}'"])
    if hostname_i.returncode == 0:
        host_ip = hostname_i.stdout.strip()
    if host_ip:
        return host_ip

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 53))
        return str(sock.getsockname()[0]).strip()
    except OSError:
        return "127.0.0.1"
    finally:
        sock.close()


def _probe_file_mount(file_path: Path) -> dict[str, Any]:
    command = [
        "docker",
        "run",
        "--rm",
        "-v",
        f"{str(file_path)}:/etc/alpine-release",
        "alpine:3.20",
        "sh",
        "-lc",
        "if [ -f /etc/alpine-release ]; then echo file; elif [ -d /etc/alpine-release ]; then echo dir; else echo missing; fi",
    ]
    result = _run(command)
    kind = (result.stdout or "").strip()
    ok = result.returncode == 0 and kind == "file"
    return {
        "ok": ok,
        "kind": kind or "unknown",
        "returncode": result.returncode,
        "stderr": (result.stderr or "").strip(),
        "command": " ".join(command),
    }


def _mount_probe_roots() -> list[Path]:
    roots: list[Path] = []
    override = str(os.environ.get(DAEMON_VISIBLE_DIR_ENV) or "").strip()
    if override:
        roots.append(Path(override).expanduser())
    roots.extend([TMP_ROOT, LOCAL_REPO_TMP_ROOT])
    deduped: list[Path] = []
    seen: set[str] = set()
    for root in roots:
        key = str(root)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(root)
    return deduped


@dataclass
class _HealthServer:
    server: ThreadingHTTPServer
    thread: threading.Thread

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2.0)


def _start_health_server() -> _HealthServer:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            if self.path != "/health":
                self.send_response(404)
                self.end_headers()
                return
            payload = b"ok\n"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format: str, *args: object) -> None:  # noqa: A003
            del format, args
            return

    server = ThreadingHTTPServer(("0.0.0.0", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return _HealthServer(server=server, thread=thread)


def _probe_container_target(host: str, port: int) -> dict[str, Any]:
    command = [
        "docker",
        "run",
        "--rm",
        "alpine:3.20",
        "sh",
        "-lc",
        f"wget -q -T 2 -O - http://{host}:{port}/health >/dev/null",
    ]
    result = _run(command)
    return {
        "host": host,
        "port": int(port),
        "ok": result.returncode == 0,
        "returncode": result.returncode,
        "stderr": (result.stderr or "").strip(),
        "command": " ".join(command),
    }


def _summary_text(payload: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append("Integration preflight summary")
    lines.append(f"- docker daemon: {'ok' if payload['docker']['ok'] else 'failed'}")
    if payload["docker"]["ok"]:
        lines.append(f"- docker server version: {payload['docker']['detail']}")
    else:
        lines.append(f"- docker error: {payload['docker']['detail']}")

    file_probe = payload["file_mount_probe"]
    lines.append(
        f"- file mount probe: {'ok' if file_probe['ok'] else 'failed'} "
        f"(kind={file_probe['kind']})"
    )
    selected_root = str(payload.get("selected_mount_probe_root") or "").strip()
    if selected_root:
        lines.append(f"  selected probe root: {selected_root}")
    if not file_probe["ok"]:
        lines.append("  first-try fix: move runtime config/system prompt inputs to a daemon-visible path.")
        lines.append("  verification: docker run -v <file>:/etc/alpine-release ... test -f /etc/alpine-release")

    network = payload["network_probe"]
    lines.append(f"- container->host probe port: {network['port']}")
    for target in network["targets"]:
        lines.append(f"  - {target['host']}: {'ok' if target['ok'] else 'failed'}")
    lines.append("")
    lines.append("Recommended hub flags:")
    lines.append("  --host 0.0.0.0")
    lines.append(
        f"  --artifact-publish-base-url http://{payload['recommended_host_for_containers']}:{payload['hub_port']}"
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Preflight checks for deterministic integration runtime setup.")
    parser.add_argument("--json", action="store_true", help="Emit JSON payload.")
    parser.add_argument("--hub-port", type=int, default=8876, help="Expected hub port for recommended launch flags.")
    parser.add_argument(
        "--fail-on-warning",
        action="store_true",
        help="Return non-zero if host networking probe does not find a reachable target.",
    )
    args = parser.parse_args()

    docker_ok, docker_detail = _docker_daemon_ok()
    host_ip = _detect_host_ip()
    file_probe: dict[str, Any] = {
        "ok": False,
        "kind": "unknown",
        "returncode": 1,
        "stderr": "docker not available",
        "command": "",
    }
    file_probe_attempts: list[dict[str, Any]] = []
    network_targets: list[dict[str, Any]] = []
    port = 0
    selected_probe_root = ""
    if docker_ok:
        for probe_root in _mount_probe_roots():
            try:
                probe_root.mkdir(parents=True, exist_ok=True)
            except OSError:
                pass
            probe_path: Path | None = None
            try:
                with tempfile.NamedTemporaryFile(
                    mode="w",
                    encoding="utf-8",
                    prefix="mount-probe-",
                    suffix=".txt",
                    dir=str(probe_root),
                    delete=False,
                ) as handle:
                    handle.write("probe\n")
                    probe_path = Path(handle.name)
                attempt = _probe_file_mount(probe_path)
                attempt["probe_root"] = str(probe_root)
                attempt["probe_path"] = str(probe_path)
            except Exception as exc:
                attempt = {
                    "ok": False,
                    "kind": "unknown",
                    "returncode": 1,
                    "stderr": str(exc),
                    "command": "",
                    "probe_root": str(probe_root),
                    "probe_path": str(probe_path) if probe_path is not None else "",
                }
            finally:
                if probe_path is not None:
                    try:
                        probe_path.unlink()
                    except OSError:
                        pass
            file_probe_attempts.append(attempt)
            file_probe = attempt
            if bool(attempt.get("ok")):
                selected_probe_root = str(probe_root)
                break

        health_server = _start_health_server()
        try:
            port = int(health_server.server.server_address[1])
            network_targets.append(_probe_container_target("host.docker.internal", port))
            network_targets.append(_probe_container_target(host_ip, port))
        finally:
            health_server.close()

    preferred_target = next((entry for entry in network_targets if entry.get("ok")), None)
    recommended_host = str(preferred_target["host"]) if preferred_target else host_ip
    payload = {
        "docker": {
            "ok": docker_ok,
            "detail": docker_detail,
        },
        "file_mount_probe": file_probe,
        "file_mount_probe_attempts": file_probe_attempts,
        "selected_mount_probe_root": selected_probe_root,
        "network_probe": {
            "port": int(port),
            "targets": network_targets,
        },
        "recommended_host_for_containers": recommended_host,
        "recommended_flags": {
            "host": "0.0.0.0",
            "artifact_publish_base_url": f"http://{recommended_host}:{int(args.hub_port)}",
            "data_dir_hint": "/workspace/tmp/agent-hub-data",
            "input_dir_hint": "/workspace/tmp/agent-hub-inputs",
        },
        "hub_port": int(args.hub_port),
    }

    if args.json:
        print(json.dumps(payload, sort_keys=True))
    else:
        print(_summary_text(payload))

    failed = not docker_ok or not file_probe.get("ok", False)
    if args.fail_on_warning:
        failed = failed or not preferred_target
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
