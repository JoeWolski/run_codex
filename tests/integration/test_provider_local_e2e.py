from __future__ import annotations

import base64
import json
import os
import subprocess
import tempfile
import unittest
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from unittest.mock import patch
from urllib.parse import urlsplit

import sys

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import agent_hub.server as hub_server


def _run(cmd: list[str], *, cwd: Path | None = None, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=str(cwd) if cwd is not None else None,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def _assert_ok(result: subprocess.CompletedProcess[str], *, context: str) -> None:
    if result.returncode == 0:
        return
    raise AssertionError(
        f"{context} failed with exit code {result.returncode}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )


@dataclass
class LocalForgeServer:
    repo_root: Path
    token: str
    username: str
    email: str
    display_name: str
    account_id: int

    def __post_init__(self) -> None:
        self._server: ThreadingHTTPServer | None = None
        self._thread: Thread | None = None

    @property
    def base_url(self) -> str:
        if self._server is None:
            raise RuntimeError("Local forge server is not started.")
        host, port = self._server.server_address
        del host
        return f"http://127.0.0.1:{int(port)}"

    def start(self) -> None:
        token = self.token
        username = self.username
        email = self.email
        display_name = self.display_name
        account_id = int(self.account_id)
        repo_root = str(self.repo_root)

        class Handler(BaseHTTPRequestHandler):
            server_version = "LocalForge/1.0"

            def log_message(self, format: str, *args: object) -> None:  # noqa: A003
                del format, args
                return

            def _send_json(self, status_code: int, payload: dict[str, object], extra_headers: dict[str, str] | None = None) -> None:
                body = json.dumps(payload).encode("utf-8")
                self.send_response(status_code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                if extra_headers:
                    for key, value in extra_headers.items():
                        self.send_header(key, value)
                self.end_headers()
                self.wfile.write(body)

            def _authorized_token(self) -> str:
                auth_header = str(self.headers.get("Authorization") or "").strip()
                if auth_header.lower().startswith("bearer "):
                    return auth_header[7:].strip()
                private_token = str(self.headers.get("PRIVATE-TOKEN") or "").strip()
                if private_token:
                    return private_token
                return ""

            def _parse_basic_auth(self) -> tuple[str, str]:
                header = str(self.headers.get("Authorization") or "").strip()
                if not header.lower().startswith("basic "):
                    return "", ""
                encoded = header[6:].strip()
                if not encoded:
                    return "", ""
                try:
                    decoded = base64.b64decode(encoded).decode("utf-8", errors="ignore")
                except Exception:
                    return "", ""
                if ":" not in decoded:
                    return decoded, ""
                user, password = decoded.split(":", 1)
                return user, password

            def _handle_user_api(self) -> None:
                provided = self._authorized_token()
                if provided != token:
                    self._send_json(401, {"message": "unauthorized"})
                    return
                self._send_json(
                    200,
                    {
                        "id": account_id,
                        "username": username,
                        "name": display_name,
                        "email": email,
                    },
                    extra_headers={"X-Gitlab-Scopes": "api"},
                )

            def _run_git_http_backend(self, path: str, query: str, body: bytes) -> None:
                auth_user, auth_password = self._parse_basic_auth()
                if auth_password != token:
                    unauthorized = b'Authentication required\n'
                    self.send_response(401)
                    self.send_header("WWW-Authenticate", 'Basic realm="local-forge"')
                    self.send_header("Content-Type", "text/plain; charset=utf-8")
                    self.send_header("Content-Length", str(len(unauthorized)))
                    self.end_headers()
                    self.wfile.write(unauthorized)
                    return

                env = os.environ.copy()
                env["GIT_PROJECT_ROOT"] = repo_root
                env["GIT_HTTP_EXPORT_ALL"] = "1"
                env["PATH_INFO"] = path
                env["REQUEST_METHOD"] = self.command
                env["QUERY_STRING"] = query
                env["CONTENT_TYPE"] = str(self.headers.get("Content-Type") or "")
                env["CONTENT_LENGTH"] = str(len(body))
                env["REMOTE_USER"] = auth_user
                env["REMOTE_ADDR"] = str(self.client_address[0] or "127.0.0.1")
                env["SERVER_PROTOCOL"] = "HTTP/1.1"
                env["SERVER_NAME"] = "127.0.0.1"
                env["SERVER_PORT"] = str(self.server.server_port)

                result = subprocess.run(
                    ["git", "http-backend"],
                    input=body,
                    capture_output=True,
                    check=False,
                    env=env,
                )
                payload = bytes(result.stdout or b"")
                header_blob = b""
                response_body = b""
                if b"\r\n\r\n" in payload:
                    header_blob, response_body = payload.split(b"\r\n\r\n", 1)
                elif b"\n\n" in payload:
                    header_blob, response_body = payload.split(b"\n\n", 1)
                else:
                    header_blob = payload
                    response_body = b""

                status_code = 200
                headers: list[tuple[str, str]] = []
                for raw_line in header_blob.decode("latin-1", errors="ignore").splitlines():
                    if not raw_line.strip():
                        continue
                    if ":" not in raw_line:
                        continue
                    key, value = raw_line.split(":", 1)
                    normalized_key = key.strip()
                    normalized_value = value.strip()
                    if normalized_key.lower() == "status":
                        try:
                            status_code = int(normalized_value.split(" ", 1)[0])
                        except ValueError:
                            status_code = 500
                        continue
                    headers.append((normalized_key, normalized_value))

                self.send_response(status_code)
                for key, value in headers:
                    self.send_header(key, value)
                if not any(str(k).lower() == "content-length" for k, _ in headers):
                    self.send_header("Content-Length", str(len(response_body)))
                self.end_headers()
                if response_body:
                    self.wfile.write(response_body)

            def _handle_git_request(self) -> None:
                parsed = urlsplit(self.path)
                body = b""
                if self.command in {"POST", "PUT", "PATCH"}:
                    try:
                        content_length = int(str(self.headers.get("Content-Length") or "0"))
                    except ValueError:
                        content_length = 0
                    if content_length > 0:
                        body = self.rfile.read(content_length)
                self._run_git_http_backend(parsed.path, parsed.query, body)

            def do_GET(self) -> None:  # noqa: N802
                parsed = urlsplit(self.path)
                if parsed.path == "/api/v4/user":
                    self._handle_user_api()
                    return
                if parsed.path.startswith("/repo.git"):
                    self._handle_git_request()
                    return
                self._send_json(404, {"detail": "Not found"})

            def do_POST(self) -> None:  # noqa: N802
                parsed = urlsplit(self.path)
                if parsed.path.startswith("/repo.git"):
                    self._handle_git_request()
                    return
                self._send_json(404, {"detail": "Not found"})

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def close(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=2.0)


def _seed_bare_repo(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    init_bare = _run(["git", "init", "--bare", str(path)])
    _assert_ok(init_bare, context="git init --bare")

    worktree = path.parent / "seed-worktree"
    worktree.mkdir(parents=True, exist_ok=True)
    init_work = _run(["git", "init"], cwd=worktree)
    _assert_ok(init_work, context="git init seed worktree")

    write_file = worktree / "README.md"
    write_file.write_text("seed\n", encoding="utf-8")
    _assert_ok(_run(["git", "add", "README.md"], cwd=worktree), context="git add seed")
    _assert_ok(
        _run(
            ["git", "-c", "user.name=Seed User", "-c", "user.email=seed@example.com", "commit", "-m", "seed"],
            cwd=worktree,
        ),
        context="git commit seed",
    )
    _assert_ok(_run(["git", "remote", "add", "origin", str(path)], cwd=worktree), context="git remote add seed")
    _assert_ok(_run(["git", "push", "origin", "HEAD:master"], cwd=worktree), context="git push seed")
    _assert_ok(_run(["git", "config", "http.receivepack", "true"], cwd=path), context="git config receivepack")


class LocalForgeEndToEndIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.tmp.name)
        self.config_file = self.tmp_path / "agent.config.toml"
        self.config_file.write_text("model = 'test'\n", encoding="utf-8")
        self.snapshot_patcher = patch.object(
            hub_server.HubState,
            "_prepare_project_snapshot_for_project",
            lambda state_obj, project, **_kwargs: state_obj._project_setup_snapshot_tag(project),
        )
        self.snapshot_patcher.start()
        self.schedule_patcher = patch.object(
            hub_server.HubState,
            "_schedule_project_build",
            lambda state_obj, project_id: state_obj._build_project_snapshot(project_id),
        )
        self.schedule_patcher.start()
        self.state = hub_server.HubState(self.tmp_path / "hub", self.config_file)

    def tearDown(self) -> None:
        state_obj = getattr(self, "state", None)
        if state_obj is not None:
            startup_thread = getattr(state_obj, "_startup_reconcile_thread", None)
            if startup_thread is not None and startup_thread.is_alive():
                startup_thread.join(timeout=2.0)
        self.schedule_patcher.stop()
        self.snapshot_patcher.stop()
        self.tmp.cleanup()

    def test_gitlab_pat_and_git_operations_work_against_local_forge(self) -> None:
        token = "glpat_local_test_token"
        repos_root = self.tmp_path / "forge-repos"
        bare_repo = repos_root / "repo.git"
        _seed_bare_repo(bare_repo)

        forge = LocalForgeServer(
            repo_root=repos_root,
            token=token,
            username="gitlab-user",
            email="gitlab-user@example.com",
            display_name="GitLab User",
            account_id=44,
        )
        forge.start()
        self.addCleanup(forge.close)

        forge_host = forge.base_url
        self.state.connect_gitlab_personal_access_token(token, host=forge_host)
        project = self.state.add_project(
            repo_url=f"{forge_host}/repo.git",
            default_branch="master",
        )
        chat = self.state.create_chat(
            project_id=project["id"],
            profile="",
            ro_mounts=[],
            rw_mounts=[],
            env_vars=[],
            agent_args=[],
            agent_type=hub_server.AGENT_TYPE_CODEX,
        )

        resolved = self.state.resolve_agent_tools_credentials(
            chat_id=chat["id"],
            mode=hub_server.PROJECT_CREDENTIAL_BINDING_MODE_AUTO,
        )
        self.assertEqual(resolved["mode"], hub_server.PROJECT_CREDENTIAL_BINDING_MODE_AUTO)
        self.assertEqual(len(resolved["credentials"]), 1)
        credential = resolved["credentials"][0]
        self.assertEqual(credential["provider"], hub_server.GIT_PROVIDER_GITLAB)
        self.assertEqual(credential["host"], urlsplit(forge_host).netloc)
        self.assertEqual(credential["scheme"], "http")

        remote_auth_url = f"{str(credential['credential_line']).rstrip('/')}/repo.git"

        ls_remote = _run(["git", "ls-remote", remote_auth_url])
        _assert_ok(ls_remote, context="git ls-remote local forge")
        self.assertIn("refs/heads/master", ls_remote.stdout)

        clone_dir = self.tmp_path / "clone-repo"
        clone = _run(["git", "clone", remote_auth_url, str(clone_dir)])
        _assert_ok(clone, context="git clone local forge")

        update_file = clone_dir / "README.md"
        update_file.write_text("seed\nupdate\n", encoding="utf-8")
        _assert_ok(_run(["git", "add", "README.md"], cwd=clone_dir), context="git add clone update")
        _assert_ok(
            _run(
                ["git", "-c", "user.name=Agent User", "-c", "user.email=agent@example.com", "commit", "-m", "update"],
                cwd=clone_dir,
            ),
            context="git commit clone update",
        )
        push = _run(["git", "push", "origin", "HEAD:master"], cwd=clone_dir)
        _assert_ok(push, context="git push local forge")

        rev_count = _run(["git", "rev-list", "--count", "master"], cwd=bare_repo)
        _assert_ok(rev_count, context="git rev-list bare repo")
        self.assertGreaterEqual(int(rev_count.stdout.strip() or "0"), 2)
