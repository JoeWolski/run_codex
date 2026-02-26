from __future__ import annotations

import json
import os
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


class _AckServer:
    def __init__(self) -> None:
        self.payloads: list[dict[str, Any]] = []
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler_factory())
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def _handler_factory(self):
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802
                if self.path != "/ack":
                    self.send_response(404)
                    self.end_headers()
                    return
                try:
                    length = int(str(self.headers.get("Content-Length") or "0"))
                except ValueError:
                    length = 0
                raw = self.rfile.read(length) if length > 0 else b"{}"
                parsed = json.loads(raw.decode("utf-8", errors="ignore") or "{}")
                if isinstance(parsed, dict):
                    outer.payloads.append(parsed)
                body = b'{"ack":true}'
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, fmt: str, *args: object) -> None:  # noqa: A003
                del fmt, args
                return

        return Handler

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{int(self.server.server_address[1])}"

    def start(self) -> None:
        self.thread.start()

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2.0)



def test_entrypoint_emits_ready_ack_before_exec(tmp_path: Path) -> None:
    server = _AckServer()
    server.start()
    try:
        env = os.environ.copy()
        env["AGENT_HUB_AGENT_TOOLS_URL"] = server.base_url
        env["AGENT_HUB_AGENT_TOOLS_TOKEN"] = "token-1"
        env["AGENT_HUB_READY_ACK_GUID"] = "guid-1"
        home = tmp_path / "home"
        home.mkdir(parents=True, exist_ok=True)
        env["HOME"] = str(home)
        env["LOCAL_HOME"] = str(home)

        command = [
            "python",
            str(Path(__file__).resolve().parents[2] / "docker" / "agent_cli" / "docker-entrypoint.py"),
            "/bin/sh",
            "-lc",
            "exit 0",
        ]
        result = subprocess.run(command, check=False, text=True, capture_output=True, env=env)
        assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"

        assert server.payloads, "entrypoint did not emit /ack request"
        payload = server.payloads[-1]
        assert payload.get("guid") == "guid-1"
        assert payload.get("stage") == "container_bootstrapped"
        meta = payload.get("meta")
        assert isinstance(meta, dict)
        assert meta.get("entrypoint") == "docker/agent_cli/docker-entrypoint.py"
    finally:
        server.stop()
