from __future__ import annotations

import importlib.util
import json
import os
import queue
import signal
import subprocess
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest.mock import AsyncMock, call, patch
from types import SimpleNamespace

from click import ClickException
from click.testing import CliRunner
from fastapi import HTTPException
from fastapi.testclient import TestClient

import sys

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
DOCKER_ENTRYPOINT = ROOT / "docker" / "agent_cli" / "docker-entrypoint.py"
AGENT_CLI_DOCKERFILE = ROOT / "docker" / "agent_cli" / "Dockerfile"
AGENT_HUB_DOCKERFILE = ROOT / "docker" / "agent_hub" / "Dockerfile"
DEVELOPMENT_DOCKERFILE = ROOT / "docker" / "development" / "Dockerfile"
DEVELOPMENT_VERIFY_SCRIPT = ROOT / "docker" / "development" / "verify-demo-tooling.sh"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import agent_hub.server as hub_server
import agent_cli.cli as image_cli


TEST_GITHUB_INSTALLATION_ID = 424242
TEST_GITHUB_INSTALLATION_PAYLOAD = {
    "id": TEST_GITHUB_INSTALLATION_ID,
    "account": {
        "login": "acme-org",
        "type": "Organization",
    },
    "repository_selection": "selected",
}
TEST_GITHUB_MANIFEST_CONVERSION_PAYLOAD = {
    "id": 777777,
    "slug": "agent-hub-configured-app",
    "pem": (
        "-----BEGIN PRIVATE KEY-----\n"
        "MIIEvQIBADANBgkqhkiG9w0BAQEFAASCBKcwggSjAgEAAoIBAQDgManifestForTests\n"
        "-----END PRIVATE KEY-----\n"
    ),
}
TEST_GITHUB_PERSONAL_ACCESS_TOKEN = "github_pat_abcdefghijklmnopqrstuvwxyz1234567890"
TEST_GITHUB_PERSONAL_ACCESS_TOKEN_SECOND = "github_pat_abcdefghijklmnopqrstuvwxyz0987654321"
TEST_GITHUB_PERSONAL_ACCESS_VERIFICATION = {
    "account_login": "agentuser",
    "account_name": "Agent User",
    "account_email": "agentuser@example.com",
    "account_id": "10101",
    "token_scopes": "repo,read:org",
}


class HubStateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.tmp.name)
        self.config_file = self.tmp_path / "config.toml"
        self.config_file.write_text("model = 'test'\n", encoding="utf-8")
        self.github_env_patcher = patch.dict(
            os.environ,
            {
                hub_server.GITHUB_APP_ID_ENV: "",
                hub_server.GITHUB_APP_SLUG_ENV: "",
                hub_server.GITHUB_APP_PRIVATE_KEY_ENV: "",
                hub_server.GITHUB_APP_PRIVATE_KEY_FILE_ENV: "",
            },
            clear=False,
        )
        self.github_env_patcher.start()
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
        self.state.github_app_settings = hub_server.GithubAppSettings(
            app_id="123456",
            app_slug="agent-hub-tests",
            private_key=(
                "-----BEGIN PRIVATE KEY-----\n"
                "MIIEvQIBADANBgkqhkiG9w0BAQEFAASCBKcwggSjAgEAAoIBAQDgFakeForTests\n"
                "-----END PRIVATE KEY-----\n"
            ),
            web_base_url="https://github.com",
            api_base_url="https://api.github.com",
        )
        self.state.github_app_settings_error = ""
        self.host_ro = self.tmp_path / "host_ro"
        self.host_rw = self.tmp_path / "host_rw"
        self.host_ro.mkdir(parents=True, exist_ok=True)
        self.host_rw.mkdir(parents=True, exist_ok=True)

    def _connect_github_app(self) -> dict[str, object]:
        with patch.object(
            hub_server.HubState,
            "_github_api_request",
            return_value=(200, json.dumps(TEST_GITHUB_INSTALLATION_PAYLOAD)),
        ), patch.object(
            hub_server.HubState,
            "_github_installation_token",
            return_value=("ghs_test_installation_token", "2030-01-01T00:00:00Z"),
        ):
            status = self.state.connect_github_app(TEST_GITHUB_INSTALLATION_ID)
        with self.state._github_token_lock:
            self.state._github_token_cache = {
                "installation_id": TEST_GITHUB_INSTALLATION_ID,
                "token": "ghs_test_installation_token",
                "expires_at": "2030-01-01T00:00:00Z",
            }
        return status

    def _connect_github_pat(self, host: str = "github.com", owner_scopes: list[str] | None = None) -> dict[str, object]:
        with patch.object(
            hub_server.HubState,
            "_verify_github_personal_access_token",
            return_value=dict(TEST_GITHUB_PERSONAL_ACCESS_VERIFICATION),
        ):
            status = self.state.connect_github_personal_access_token(
                TEST_GITHUB_PERSONAL_ACCESS_TOKEN,
                host=host,
                owner_scopes=owner_scopes,
            )
        return status

    def _current_github_setup_state_token(self) -> str:
        with self.state._github_setup_lock:
            session = self.state._github_setup_session
            self.assertIsNotNone(session)
            assert session is not None
            return str(session.state)

    def tearDown(self) -> None:
        self.github_env_patcher.stop()
        self.snapshot_patcher.stop()
        self.schedule_patcher.stop()
        self.tmp.cleanup()

    def test_project_defaults_are_persisted(self) -> None:
        project = self.state.add_project(
            repo_url="https://example.com/org/repo.git",
            name="demo",
            default_branch="main",
            setup_script="echo hi",
            base_image_mode="tag",
            base_image_value="ubuntu:24.04",
            default_ro_mounts=[f"{self.host_ro}:/data_ro"],
            default_rw_mounts=[f"{self.host_rw}:/data_rw"],
            default_env_vars=["FOO=bar"],
        )
        payload = self.state.state_payload()
        loaded = next(item for item in payload["projects"] if item["id"] == project["id"])
        self.assertEqual(loaded["default_ro_mounts"], [f"{self.host_ro}:/data_ro"])
        self.assertEqual(loaded["default_rw_mounts"], [f"{self.host_rw}:/data_rw"])
        self.assertEqual(loaded["default_env_vars"], ["FOO=bar"])
        self.assertEqual(loaded["base_image_mode"], "tag")
        self.assertEqual(loaded["setup_snapshot_image"], self.state._project_setup_snapshot_tag(project))
        self.assertEqual(loaded["build_status"], "ready")

    def test_project_setup_snapshot_tag_includes_runtime_input_fingerprint(self) -> None:
        project = self.state.add_project(
            repo_url="https://example.com/org/repo.git",
            default_branch="main",
            setup_script="echo setup",
        )

        with patch("agent_hub.server._agent_cli_runtime_inputs_fingerprint", return_value="runtime-fingerprint-a"):
            tag_a = self.state._project_setup_snapshot_tag(project)
        with patch("agent_hub.server._agent_cli_runtime_inputs_fingerprint", return_value="runtime-fingerprint-b"):
            tag_b = self.state._project_setup_snapshot_tag(project)

        self.assertNotEqual(tag_a, tag_b)

    def test_state_payload_reports_project_build_log_availability(self) -> None:
        project = self.state.add_project(
            repo_url="https://example.com/org/repo.git",
            default_branch="main",
        )
        payload = self.state.state_payload()
        loaded = next(item for item in payload["projects"] if item["id"] == project["id"])
        self.assertIn("has_build_log", loaded)
        self.assertFalse(loaded["has_build_log"])

        log_path = self.state.project_build_log(project["id"])
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text("snapshot build output\n", encoding="utf-8")

        updated_payload = self.state.state_payload()
        updated = next(item for item in updated_payload["projects"] if item["id"] == project["id"])
        self.assertTrue(updated["has_build_log"])

    def test_settings_default_agent_type_persists_and_is_exposed(self) -> None:
        initial_settings = self.state.settings_payload()
        self.assertEqual(initial_settings["default_agent_type"], hub_server.DEFAULT_CHAT_AGENT_TYPE)
        initial_state_payload = self.state.state_payload()
        self.assertEqual(initial_state_payload["settings"]["default_agent_type"], hub_server.DEFAULT_CHAT_AGENT_TYPE)

        updated_settings = self.state.update_settings({"default_agent_type": "claude"})
        self.assertEqual(updated_settings["default_agent_type"], "claude")

        reloaded = self.state.load()
        self.assertEqual(reloaded["settings"]["default_agent_type"], "claude")
        updated_state_payload = self.state.state_payload()
        self.assertEqual(updated_state_payload["settings"]["default_agent_type"], "claude")

    def test_agent_capabilities_cache_loads_on_startup(self) -> None:
        cache_payload = {
            "version": 1,
            "updated_at": "2026-01-02T03:04:05Z",
            "discovery_in_progress": True,
            "discovery_started_at": "2026-01-02T03:00:00Z",
            "discovery_finished_at": "",
            "agents": [
                {
                    "agent_type": "codex",
                    "label": "Codex",
                    "models": ["default", "gpt-6-codex"],
                    "reasoning_modes": ["default", "low", "high"],
                    "updated_at": "2026-01-02T03:04:05Z",
                    "last_error": "",
                },
                {
                    "agent_type": "claude",
                    "label": "Claude",
                    "models": ["default", "sonnet-4"],
                    "reasoning_modes": ["default"],
                    "updated_at": "2026-01-02T03:04:05Z",
                    "last_error": "",
                },
                {
                    "agent_type": "gemini",
                    "label": "Gemini CLI",
                    "models": ["default", "gemini-2.5-pro"],
                    "reasoning_modes": ["default"],
                    "updated_at": "2026-01-02T03:04:05Z",
                    "last_error": "",
                },
            ],
        }
        cache_file = self.state.data_dir / hub_server.AGENT_CAPABILITIES_CACHE_FILE_NAME
        cache_file.write_text(json.dumps(cache_payload, indent=2), encoding="utf-8")

        reloaded = hub_server.HubState(self.state.data_dir, self.config_file)
        payload = reloaded.agent_capabilities_payload()
        self.assertFalse(payload["discovery_in_progress"])
        codex = next(agent for agent in payload["agents"] if agent["agent_type"] == "codex")
        self.assertEqual(codex["models"], ["default", "gpt-6-codex"])
        self.assertEqual(codex["reasoning_modes"], ["default", "low", "high"])

    def test_agent_capabilities_discovery_updates_cached_modes(self) -> None:
        def fake_probe(cmd: list[str], _timeout: float) -> tuple[int, str]:
            if cmd == ["codex", "--help"]:
                return (
                    0,
                    """
Codex CLI
  --model <MODEL>
      Possible values: gpt-6-codex, gpt-6-codex-spark
  --reasoning-effort <LEVEL>
      Possible values:
      - low
      - medium
      - high
""",
                )
            if cmd == ["claude", "--help"]:
                return (
                    0,
                    """
Claude Code
  --model <MODEL>
      choices: sonnet-4, opus-4
  --effort <EFFORT>
      Available values: low | medium | high
""",
                )
            if cmd == ["gemini", "--help"]:
                return (
                    0,
                    """
Gemini CLI
  --model <MODEL> [possible values: auto, pro, flash, flash-lite, gemini-2.5-pro]
  --thinking-level <LEVEL>
      Possible values:
      - low
      - medium
      - high
""",
                )
            return 127, ""

        with patch("agent_hub.server._run_agent_capability_probe", side_effect=fake_probe):
            started_payload = self.state.start_agent_capabilities_discovery()
            self.assertTrue(started_payload["discovery_in_progress"])
            worker = self.state._agent_capabilities_discovery_thread
            self.assertIsNotNone(worker)
            assert worker is not None
            worker.join(timeout=3.0)
            self.assertFalse(worker.is_alive())

        payload = self.state.agent_capabilities_payload()
        self.assertFalse(payload["discovery_in_progress"])
        self.assertTrue(payload["discovery_finished_at"])
        codex = next(agent for agent in payload["agents"] if agent["agent_type"] == "codex")
        claude = next(agent for agent in payload["agents"] if agent["agent_type"] == "claude")
        gemini = next(agent for agent in payload["agents"] if agent["agent_type"] == "gemini")
        self.assertEqual(codex["models"], ["default", "gpt-6-codex", "gpt-6-codex-spark"])
        self.assertEqual(codex["reasoning_modes"], ["default", "low", "medium", "high"])
        self.assertEqual(claude["models"], ["default", "sonnet-4", "opus-4"])
        self.assertEqual(claude["reasoning_modes"], ["default", "low", "medium", "high"])
        self.assertEqual(gemini["models"], ["default", "auto", "pro", "flash", "flash-lite", "gemini-2.5-pro"])
        self.assertEqual(gemini["reasoning_modes"], ["default", "low", "medium", "high"])
        self.assertTrue((self.state.data_dir / hub_server.AGENT_CAPABILITIES_CACHE_FILE_NAME).exists())

    def test_agent_capabilities_discovery_uses_help_output_only_without_fallback(self) -> None:
        self.state._agent_capabilities = hub_server._normalize_agent_capabilities_payload(
            {
                "version": 1,
                "updated_at": "2026-01-09T12:00:00Z",
                "discovery_in_progress": False,
                "discovery_started_at": "",
                "discovery_finished_at": "2026-01-09T12:00:00Z",
                "agents": [
                    {
                        "agent_type": "codex",
                        "label": "Codex",
                        "models": ["default", "gpt-legacy-codex"],
                        "reasoning_modes": ["default", "high"],
                        "updated_at": "2026-01-09T12:00:00Z",
                        "last_error": "",
                    },
                    {
                        "agent_type": "claude",
                        "label": "Claude",
                        "models": ["default", "legacy-sonnet"],
                        "reasoning_modes": ["default", "high"],
                        "updated_at": "2026-01-09T12:00:00Z",
                        "last_error": "",
                    },
                    {
                        "agent_type": "gemini",
                        "label": "Gemini CLI",
                        "models": ["default", "legacy-gemini"],
                        "reasoning_modes": ["default", "high"],
                        "updated_at": "2026-01-09T12:00:00Z",
                        "last_error": "",
                    },
                ],
            }
        )
        calls: list[list[str]] = []

        def fake_probe(cmd: list[str], _timeout: float) -> tuple[int, str]:
            calls.append(list(cmd))
            if cmd == ["codex", "--help"]:
                return 0, "--model <MODEL>\npossible values: gpt-7-codex"
            if cmd == ["claude", "--help"]:
                return 0, "--model <MODEL>\nchoices: sonnet-4"
            if cmd == ["gemini", "--help"]:
                return 0, "--model <MODEL>\nchoices: pro, flash"
            return 127, ""

        with patch("agent_hub.server._run_agent_capability_probe", side_effect=fake_probe):
            started_payload = self.state.start_agent_capabilities_discovery()
            self.assertTrue(started_payload["discovery_in_progress"])
            worker = self.state._agent_capabilities_discovery_thread
            self.assertIsNotNone(worker)
            assert worker is not None
            worker.join(timeout=3.0)
            self.assertFalse(worker.is_alive())

        payload = self.state.agent_capabilities_payload()
        self.assertEqual(
            calls,
            [
                ["codex", "--help"],
                ["claude", "--help"],
                ["gemini", "--help"],
            ],
        )
        codex = next(agent for agent in payload["agents"] if agent["agent_type"] == "codex")
        claude = next(agent for agent in payload["agents"] if agent["agent_type"] == "claude")
        gemini = next(agent for agent in payload["agents"] if agent["agent_type"] == "gemini")
        self.assertEqual(codex["models"], ["default", "gpt-7-codex"])
        self.assertEqual(codex["reasoning_modes"], ["default"])
        self.assertEqual(claude["models"], ["default", "sonnet-4"])
        self.assertEqual(claude["reasoning_modes"], ["default"])
        self.assertEqual(gemini["models"], ["default", "pro", "flash"])
        self.assertEqual(gemini["reasoning_modes"], ["default"])

    def test_reasoning_candidate_extractor_requires_supported_modes_context(self) -> None:
        self.assertEqual(
            hub_server._extract_reasoning_candidates_from_output(
                "Default reasoning effort: low",
                hub_server.AGENT_TYPE_CODEX,
            ),
            [],
        )
        self.assertEqual(
            hub_server._extract_reasoning_candidates_from_output(
                "Supported reasoning modes: low medium high",
                hub_server.AGENT_TYPE_CODEX,
            ),
            ["default", "low", "medium", "high"],
        )
        self.assertEqual(
            hub_server._extract_reasoning_candidates_from_output(
                json.dumps({"model_reasoning_effort": "low"}),
                hub_server.AGENT_TYPE_CODEX,
            ),
            [],
        )
        self.assertEqual(
            hub_server._extract_reasoning_candidates_from_output(
                json.dumps({"supported_reasoning_modes": ["minimal", "low", "medium"]}),
                hub_server.AGENT_TYPE_CODEX,
            ),
            ["default", "minimal", "low", "medium"],
        )
        self.assertEqual(
            hub_server._extract_reasoning_candidates_from_output(
                """
--effort <EFFORT>
  Possible values:
  - low
  - medium
  - high
""",
                hub_server.AGENT_TYPE_CLAUDE,
            ),
            ["default", "low", "medium", "high"],
        )

    def test_agent_capabilities_payload_normalizes_invalid_codex_models_and_reasoning(self) -> None:
        payload = hub_server._normalize_agent_capabilities_payload(
            {
                "version": 1,
                "updated_at": "2026-01-03T00:00:00Z",
                "discovery_in_progress": False,
                "discovery_started_at": "",
                "discovery_finished_at": "2026-01-03T00:00:00Z",
                "agents": [
                    {
                        "agent_type": "codex",
                        "label": "Codex",
                        "models": ["default", "codex", "codex-provided"],
                        "reasoning_modes": ["default", "low"],
                        "updated_at": "2026-01-03T00:00:00Z",
                        "last_error": "",
                    }
                ],
            }
        )
        codex = next(agent for agent in payload["agents"] if agent["agent_type"] == "codex")
        self.assertEqual(
            codex["models"],
            hub_server.AGENT_CAPABILITY_DEFAULT_MODELS_BY_TYPE[hub_server.AGENT_TYPE_CODEX],
        )
        self.assertEqual(codex["reasoning_modes"], ["default", "low"])

    def test_agent_capabilities_discovery_ignores_failed_codex_probe_output(self) -> None:
        def fake_probe(cmd: list[str], _timeout: float) -> tuple[int, str]:
            if cmd == ["codex", "--help"]:
                return (
                    2,
                    'Unknown argument "--help".\nmodel: codex-provided\nreasoning effort: low',
                )
            if cmd == ["claude", "--help"]:
                return 127, ""
            if cmd == ["gemini", "--help"]:
                return 127, ""
            return 127, ""

        with patch("agent_hub.server._run_agent_capability_probe", side_effect=fake_probe):
            started_payload = self.state.start_agent_capabilities_discovery()
            self.assertTrue(started_payload["discovery_in_progress"])
            worker = self.state._agent_capabilities_discovery_thread
            self.assertIsNotNone(worker)
            assert worker is not None
            worker.join(timeout=3.0)
            self.assertFalse(worker.is_alive())

        payload = self.state.agent_capabilities_payload()
        codex = next(agent for agent in payload["agents"] if agent["agent_type"] == "codex")
        self.assertEqual(
            codex["models"],
            hub_server.AGENT_CAPABILITY_DEFAULT_MODELS_BY_TYPE[hub_server.AGENT_TYPE_CODEX],
        )
        self.assertEqual(
            codex["reasoning_modes"],
            hub_server.AGENT_CAPABILITY_DEFAULT_REASONING_BY_TYPE[hub_server.AGENT_TYPE_CODEX],
        )

    def test_openai_credentials_round_trip_status(self) -> None:
        initial = self.state.openai_auth_status()
        self.assertFalse(initial["connected"])
        self.assertEqual(initial["key_hint"], "")

        with patch("agent_hub.server._verify_openai_api_key", return_value=None) as verify_call:
            saved = self.state.connect_openai("sk-test-abcdefghijklmnopqrstuvwxyz1234")

        verify_call.assert_called_once_with("sk-test-abcdefghijklmnopqrstuvwxyz1234")
        self.assertTrue(saved["connected"])
        self.assertTrue(saved["key_hint"].startswith("sk-tes"))
        self.assertTrue(saved["updated_at"])
        self.assertTrue(self.state.openai_credentials_file.exists())

        mode = self.state.openai_credentials_file.stat().st_mode & 0o777
        self.assertEqual(mode, 0o600)

        payload = self.state.auth_settings_payload()
        self.assertIn("providers", payload)
        self.assertIn("openai", payload["providers"])
        self.assertTrue(payload["providers"]["openai"]["connected"])

        disconnected = self.state.disconnect_openai()
        self.assertFalse(disconnected["connected"])
        self.assertFalse(self.state.openai_credentials_file.exists())

    def test_connect_openai_skips_verification_when_requested(self) -> None:
        with patch("agent_hub.server._verify_openai_api_key") as verify_call:
            saved = self.state.connect_openai("sk-test-abcdefghijklmnopqrstuvwxyz1234", verify=False)
        verify_call.assert_not_called()
        self.assertTrue(saved["connected"])

    def test_github_app_credentials_round_trip_status(self) -> None:
        initial = self.state.github_auth_status()
        self.assertFalse(initial["connected"])
        self.assertTrue(initial["app_configured"])
        self.assertEqual(initial["installation_id"], 0)

        saved = self._connect_github_app()
        self.assertTrue(saved["connected"])
        self.assertEqual(saved["installation_id"], TEST_GITHUB_INSTALLATION_ID)
        self.assertEqual(saved["installation_account_login"], "acme-org")
        self.assertEqual(saved["installation_account_type"], "Organization")
        self.assertEqual(saved["repository_selection"], "selected")
        self.assertTrue(saved["updated_at"])
        self.assertTrue(self.state.github_app_installation_file.exists())
        self.assertTrue(self.state.github_git_credentials_file.exists())

        installation_mode = self.state.github_app_installation_file.stat().st_mode & 0o777
        credentials_mode = self.state.github_git_credentials_file.stat().st_mode & 0o777
        self.assertEqual(installation_mode, 0o600)
        self.assertEqual(credentials_mode, 0o600)

        payload = self.state.auth_settings_payload()
        self.assertIn("providers", payload)
        self.assertIn("github", payload["providers"])
        self.assertTrue(payload["providers"]["github"]["connected"])

        disconnected = self.state.disconnect_github_app()
        self.assertFalse(disconnected["connected"])
        self.assertFalse(self.state.github_app_installation_file.exists())
        self.assertFalse(self.state.github_git_credentials_file.exists())

    def test_github_personal_access_token_credentials_round_trip_status(self) -> None:
        initial = self.state.github_auth_status()
        self.assertFalse(initial["connected"])

        saved = self._connect_github_pat()
        self.assertTrue(saved["connected"])
        self.assertEqual(saved["connection_mode"], "personal_access_token")
        self.assertEqual(saved["personal_access_token_user_login"], "agentuser")
        self.assertEqual(saved["personal_access_token_user_name"], "Agent User")
        self.assertEqual(saved["personal_access_token_user_email"], "agentuser@example.com")
        self.assertEqual(saved["personal_access_token_git_user_name"], "Agent User")
        self.assertEqual(saved["personal_access_token_git_user_email"], "agentuser@example.com")
        self.assertEqual(saved["personal_access_token_host"], "github.com")
        self.assertEqual(saved["personal_access_token_count"], 1)
        self.assertEqual(len(saved["personal_access_tokens"]), 1)
        self.assertEqual(saved["personal_access_tokens"][0]["host"], "github.com")
        self.assertEqual(saved["personal_access_tokens"][0]["owner_scopes"], [])
        self.assertTrue(saved["updated_at"])
        self.assertTrue(self.state.github_personal_access_token_file.exists())
        self.assertTrue(self.state.github_git_credentials_file.exists())
        credentials_line = self.state.github_git_credentials_file.read_text(encoding="utf-8").strip()
        self.assertEqual(
            credentials_line,
            f"https://agentuser:{TEST_GITHUB_PERSONAL_ACCESS_TOKEN}@github.com",
        )

        token_mode = self.state.github_personal_access_token_file.stat().st_mode & 0o777
        credentials_mode = self.state.github_git_credentials_file.stat().st_mode & 0o777
        self.assertEqual(token_mode, 0o600)
        self.assertEqual(credentials_mode, 0o600)

        disconnected = self.state.disconnect_github_app()
        self.assertFalse(disconnected["connected"])
        self.assertFalse(self.state.github_personal_access_token_file.exists())
        self.assertFalse(self.state.github_git_credentials_file.exists())

    def test_connect_github_personal_access_token_rejects_invalid_token(self) -> None:
        with self.assertRaises(HTTPException) as ctx:
            self.state.connect_github_personal_access_token("short-token")
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("personal_access_token", str(ctx.exception.detail))

    def test_connect_github_app_clears_personal_access_token_state(self) -> None:
        self._connect_github_pat()
        with patch.object(
            hub_server.HubState,
            "_github_api_request",
            return_value=(200, json.dumps(TEST_GITHUB_INSTALLATION_PAYLOAD)),
        ), patch.object(
            hub_server.HubState,
            "_github_installation_token",
            return_value=("ghs_test_installation_token", "2030-01-01T00:00:00Z"),
        ):
            status = self.state.connect_github_app(TEST_GITHUB_INSTALLATION_ID)
        self.assertEqual(status["connection_mode"], "github_app")
        self.assertTrue(self.state.github_app_installation_file.exists())
        self.assertFalse(self.state.github_personal_access_token_file.exists())

    def test_connect_github_personal_access_token_clears_app_connection_state(self) -> None:
        self._connect_github_app()
        status = self._connect_github_pat()
        self.assertEqual(status["connection_mode"], "personal_access_token")
        self.assertTrue(self.state.github_personal_access_token_file.exists())
        self.assertFalse(self.state.github_app_installation_file.exists())

    def test_github_auth_status_migrates_legacy_personal_access_token_record(self) -> None:
        legacy_payload = {
            "host": "github.com",
            "personal_access_token": TEST_GITHUB_PERSONAL_ACCESS_TOKEN,
            "account_login": "legacy-user",
            "account_name": "Legacy User",
            "account_email": "legacy@example.com",
            "account_id": "999",
            "git_user_name": "Legacy User",
            "git_user_email": "legacy@example.com",
            "token_scopes": "repo",
            "verified_at": "2030-01-01T00:00:00Z",
            "connected_at": "2030-01-01T00:00:00Z",
        }
        self.state.github_personal_access_token_file.parent.mkdir(parents=True, exist_ok=True)
        self.state.github_personal_access_token_file.write_text(
            json.dumps(legacy_payload, indent=2) + "\n",
            encoding="utf-8",
        )

        status = self.state.github_auth_status()
        self.assertTrue(status["connected"])
        self.assertEqual(status["connection_mode"], "personal_access_token")
        self.assertEqual(status["personal_access_token_count"], 1)
        self.assertEqual(len(status["personal_access_tokens"]), 1)
        self.assertEqual(status["personal_access_tokens"][0]["account_login"], "legacy-user")
        self.assertEqual(status["personal_access_tokens"][0]["owner_scopes"], [])

    def test_github_repo_auth_context_prefers_owner_scoped_personal_access_token(self) -> None:
        first_verification = dict(TEST_GITHUB_PERSONAL_ACCESS_VERIFICATION)
        first_verification["account_login"] = "fallback-user"
        first_verification["account_name"] = "Fallback User"
        first_verification["account_email"] = "fallback@example.com"
        first_verification["account_id"] = "111"
        second_verification = dict(TEST_GITHUB_PERSONAL_ACCESS_VERIFICATION)
        second_verification["account_login"] = "scoped-user"
        second_verification["account_name"] = "Scoped User"
        second_verification["account_email"] = "scoped@example.com"
        second_verification["account_id"] = "222"

        with patch.object(
            hub_server.HubState,
            "_verify_github_personal_access_token",
            side_effect=[first_verification, second_verification],
        ):
            self.state.connect_github_personal_access_token(
                TEST_GITHUB_PERSONAL_ACCESS_TOKEN,
                host="github.com",
                owner_scopes=[],
            )
            self.state.connect_github_personal_access_token(
                TEST_GITHUB_PERSONAL_ACCESS_TOKEN_SECOND,
                host="github.com",
                owner_scopes=["acme-org"],
            )

        context = self.state._github_repo_auth_context("https://github.com/acme-org/repo.git")
        self.assertIsNotNone(context)
        assert context is not None
        mode, host, payload = context
        self.assertEqual(mode, "personal_access_token")
        self.assertEqual(host, "github.com")
        self.assertEqual(payload["account_login"], "scoped-user")
        self.assertEqual(payload["personal_access_token"], TEST_GITHUB_PERSONAL_ACCESS_TOKEN_SECOND)

        fallback_context = self.state._github_repo_auth_context("https://github.com/other-org/repo.git")
        self.assertIsNotNone(fallback_context)
        assert fallback_context is not None
        fallback_mode, fallback_host, fallback_payload = fallback_context
        self.assertEqual(fallback_mode, "personal_access_token")
        self.assertEqual(fallback_host, "github.com")
        self.assertEqual(fallback_payload["account_login"], "fallback-user")
        self.assertEqual(fallback_payload["personal_access_token"], TEST_GITHUB_PERSONAL_ACCESS_TOKEN)

    def test_disconnect_github_personal_access_token_removes_only_selected_token(self) -> None:
        first_verification = dict(TEST_GITHUB_PERSONAL_ACCESS_VERIFICATION)
        first_verification["account_login"] = "fallback-user"
        second_verification = dict(TEST_GITHUB_PERSONAL_ACCESS_VERIFICATION)
        second_verification["account_login"] = "scoped-user"

        with patch.object(
            hub_server.HubState,
            "_verify_github_personal_access_token",
            side_effect=[first_verification, second_verification],
        ):
            self.state.connect_github_personal_access_token(
                TEST_GITHUB_PERSONAL_ACCESS_TOKEN,
                host="github.com",
                owner_scopes=[],
            )
            connected = self.state.connect_github_personal_access_token(
                TEST_GITHUB_PERSONAL_ACCESS_TOKEN_SECOND,
                host="github.com",
                owner_scopes=["acme-org"],
            )

        self.assertEqual(connected["personal_access_token_count"], 2)
        first_token_id = str(connected["personal_access_tokens"][0]["token_id"])
        second_token_id = str(connected["personal_access_tokens"][1]["token_id"])
        self.assertTrue(first_token_id)
        self.assertTrue(second_token_id)

        after_first_disconnect = self.state.disconnect_github_personal_access_token(first_token_id)
        self.assertEqual(after_first_disconnect["personal_access_token_count"], 1)
        self.assertEqual(after_first_disconnect["personal_access_tokens"][0]["token_id"], second_token_id)
        self.assertTrue(self.state.github_personal_access_token_file.exists())

        after_second_disconnect = self.state.disconnect_github_personal_access_token(second_token_id)
        self.assertEqual(after_second_disconnect["personal_access_token_count"], 0)
        self.assertFalse(after_second_disconnect["connected"])
        self.assertFalse(self.state.github_personal_access_token_file.exists())

    def test_connect_github_app_rejects_invalid_installation_id(self) -> None:
        with self.assertRaises(HTTPException) as ctx:
            self.state.connect_github_app("invalid-installation")
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("installation_id", str(ctx.exception.detail))

    def test_list_github_app_installations(self) -> None:
        with patch.object(
            hub_server.HubState,
            "_github_api_request",
            return_value=(200, json.dumps([TEST_GITHUB_INSTALLATION_PAYLOAD])),
        ):
            payload = self.state.list_github_app_installations()
        self.assertTrue(payload["app_configured"])
        self.assertEqual(payload["installations"][0]["id"], TEST_GITHUB_INSTALLATION_ID)
        self.assertEqual(payload["installations"][0]["account_login"], "acme-org")

    def test_reload_github_app_settings_reads_settings_file(self) -> None:
        self.state.github_app_settings = None
        self.state.github_app_settings_error = ""
        self.state.github_app_settings_file.write_text(
            json.dumps(
                {
                    "app_id": "999999",
                    "app_slug": "agent-hub-file-config",
                    "private_key": TEST_GITHUB_MANIFEST_CONVERSION_PAYLOAD["pem"],
                    "web_base_url": "https://github.com",
                    "api_base_url": "https://api.github.com",
                }
            ),
            encoding="utf-8",
        )
        self.state._reload_github_app_settings()
        self.assertIsNotNone(self.state.github_app_settings)
        assert self.state.github_app_settings is not None
        self.assertEqual(self.state.github_app_settings.app_id, "999999")
        self.assertEqual(self.state.github_app_settings.app_slug, "agent-hub-file-config")
        self.assertEqual(self.state.github_app_settings_error, "")

    def test_start_github_app_setup_returns_manifest_payload(self) -> None:
        with patch.dict(
            os.environ,
            {
                hub_server.GITHUB_APP_ID_ENV: "",
                hub_server.GITHUB_APP_SLUG_ENV: "",
                hub_server.GITHUB_APP_PRIVATE_KEY_ENV: "",
                hub_server.GITHUB_APP_PRIVATE_KEY_FILE_ENV: "",
            },
            clear=False,
        ):
            payload = self.state.start_github_app_setup(origin="http://localhost:8765")
        self.assertTrue(payload["active"])
        self.assertEqual(payload["status"], "awaiting_user")
        self.assertTrue(str(payload["form_action"]).startswith("https://github.com/settings/apps/new?state="))
        self.assertEqual(payload["manifest"]["redirect_url"], "http://localhost:8765/api/settings/auth/github/app/setup/callback")
        self.assertTrue(str(payload["manifest"]["name"]).startswith(hub_server.GITHUB_APP_DEFAULT_NAME))

    def test_complete_github_app_setup_persists_settings(self) -> None:
        self.state.start_github_app_setup(origin="http://localhost:8765")
        state_token = self._current_github_setup_state_token()
        with patch.object(
            hub_server.HubState,
            "_github_manifest_conversion_request",
            return_value=TEST_GITHUB_MANIFEST_CONVERSION_PAYLOAD,
        ):
            payload = self.state.complete_github_app_setup(code="manifest-code-1", state_value=state_token)

        self.assertEqual(payload["status"], "completed")
        self.assertEqual(payload["app_slug"], "agent-hub-configured-app")
        self.assertTrue(self.state.github_app_settings_file.exists())
        stored = json.loads(self.state.github_app_settings_file.read_text(encoding="utf-8"))
        self.assertEqual(stored["app_id"], "777777")
        self.assertEqual(stored["app_slug"], "agent-hub-configured-app")
        self.assertIn("configured_at", stored)
        self.assertIsNotNone(self.state.github_app_settings)
        assert self.state.github_app_settings is not None
        self.assertEqual(self.state.github_app_settings.app_slug, "agent-hub-configured-app")
        self.assertEqual(self.state.github_app_settings_error, "")

    def test_complete_github_app_setup_rejects_invalid_state(self) -> None:
        self.state.start_github_app_setup(origin="http://localhost:8765")
        with self.assertRaises(HTTPException) as ctx:
            self.state.complete_github_app_setup(code="manifest-code-1", state_value="wrong-state")
        self.assertEqual(ctx.exception.status_code, 400)
        session_payload = self.state.github_app_setup_session_payload()
        self.assertEqual(session_payload["status"], "failed")
        self.assertIn("state", session_payload["error"])

    def test_complete_github_app_setup_records_conversion_failure(self) -> None:
        self.state.github_app_settings = None
        self.state.github_app_settings_error = ""
        self.state.start_github_app_setup(origin="http://localhost:8765")
        state_token = self._current_github_setup_state_token()
        with patch.object(
            hub_server.HubState,
            "_github_manifest_conversion_request",
            side_effect=HTTPException(status_code=400, detail="Invalid manifest conversion code."),
        ):
            with self.assertRaises(HTTPException) as ctx:
                self.state.complete_github_app_setup(code="manifest-code-1", state_value=state_token)
        self.assertEqual(ctx.exception.status_code, 400)
        session_payload = self.state.github_app_setup_session_payload()
        self.assertEqual(session_payload["status"], "failed")
        self.assertIn("Invalid manifest conversion code.", session_payload["error"])
        self.assertFalse(self.state.github_app_settings_file.exists())
        self.assertIsNone(self.state.github_app_settings)

    def test_connect_openai_verify_failure_does_not_persist_key(self) -> None:
        with patch(
            "agent_hub.server._verify_openai_api_key",
            side_effect=HTTPException(status_code=400, detail="OpenAI rejected the API key."),
        ):
            with self.assertRaises(HTTPException):
                self.state.connect_openai("sk-test-abcdefghijklmnopqrstuvwxyz1234")
        self.assertFalse(self.state.openai_credentials_file.exists())

    def test_test_openai_chat_title_generation_requires_prompt(self) -> None:
        with self.assertRaises(HTTPException) as ctx:
            self.state.test_openai_chat_title_generation("   ")
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("prompt is required", str(ctx.exception.detail))

    def test_test_openai_chat_title_generation_reports_missing_credentials(self) -> None:
        with patch("agent_hub.server._read_codex_auth", return_value=(False, "")):
            result = self.state.test_openai_chat_title_generation("triage flaky websocket reconnect tests")
        self.assertFalse(result["ok"])
        self.assertEqual(result["title"], "")
        self.assertIn("No OpenAI credentials configured", result["error"])
        self.assertEqual(result["connectivity"]["title_generation_auth_mode"], "none")
        self.assertFalse(result["connectivity"]["api_key_connected"])
        self.assertTrue(result["issues"])

    def test_test_openai_chat_title_generation_returns_generated_title(self) -> None:
        self.state.connect_openai("sk-test-abcdefghijklmnopqrstuvwxyz1234", verify=False)
        with patch("agent_hub.server._read_codex_auth", return_value=(False, "")), patch(
            "agent_hub.server._openai_generate_chat_title",
            return_value="Triage flaky websocket reconnect tests",
        ) as generate_title:
            result = self.state.test_openai_chat_title_generation("triage flaky websocket reconnect tests")

        self.assertTrue(result["ok"])
        self.assertEqual(result["title"], "Triage flaky websocket reconnect tests")
        self.assertEqual(result["error"], "")
        self.assertEqual(result["issues"], [])
        self.assertEqual(result["connectivity"]["title_generation_auth_mode"], "api_key")
        self.assertTrue(result["connectivity"]["api_key_connected"])
        generate_title.assert_called_once_with(
            api_key="sk-test-abcdefghijklmnopqrstuvwxyz1234",
            user_prompts=["triage flaky websocket reconnect tests"],
            max_chars=hub_server.CHAT_TITLE_MAX_CHARS,
        )

    def test_test_openai_chat_title_generation_prefers_connected_account(self) -> None:
        with patch("agent_hub.server._read_codex_auth", return_value=(True, "chatgpt")), patch(
            "agent_hub.server._codex_generate_chat_title",
            return_value="Triage flaky websocket reconnect tests",
        ) as generate_title:
            result = self.state.test_openai_chat_title_generation("triage flaky websocket reconnect tests")

        self.assertTrue(result["ok"])
        self.assertEqual(result["title"], "Triage flaky websocket reconnect tests")
        self.assertEqual(result["connectivity"]["title_generation_auth_mode"], "chatgpt_account")
        self.assertEqual(result["model"], hub_server.CHAT_TITLE_ACCOUNT_MODEL)
        generate_title.assert_called_once_with(
            host_agent_home=self.state.host_agent_home,
            host_codex_dir=self.state.host_codex_dir,
            user_prompts=["triage flaky websocket reconnect tests"],
            max_chars=hub_server.CHAT_TITLE_MAX_CHARS,
        )

    def test_normalize_chat_prompt_history_keeps_all_prompts_without_limit(self) -> None:
        prompts = [f"prompt {index}" for index in range(1, 200)]
        normalized = hub_server._normalize_chat_prompt_history(prompts)
        self.assertEqual(normalized, prompts)

    def test_prompt_templates_are_loadable(self) -> None:
        system_prompt = hub_server._render_prompt_template(
            hub_server.PROMPT_CHAT_TITLE_OPENAI_SYSTEM_FILE,
            max_chars=hub_server.CHAT_TITLE_MAX_CHARS,
        )
        user_prompt = hub_server._render_prompt_template(
            hub_server.PROMPT_CHAT_TITLE_OPENAI_USER_FILE,
            prompt_lines="1. test prompt",
            max_chars=hub_server.CHAT_TITLE_MAX_CHARS,
        )
        codex_prompt = hub_server._render_prompt_template(
            hub_server.PROMPT_CHAT_TITLE_CODEX_REQUEST_FILE,
            prompt_lines="1. test prompt",
            max_chars=hub_server.CHAT_TITLE_MAX_CHARS,
        )
        auto_config_prompt = hub_server._render_prompt_template(
            hub_server.PROMPT_AUTO_CONFIGURE_PROJECT_FILE,
            repo_url="https://github.com/org/repo.git",
            branch="main",
        )
        self.assertIn(f"Maximum length: {hub_server.CHAT_TITLE_MAX_CHARS} characters.", system_prompt)
        self.assertIn("1. test prompt", user_prompt)
        self.assertIn("1. test prompt", codex_prompt)
        self.assertIn("Repository URL: https://github.com/org/repo.git", auto_config_prompt)
        self.assertIn("Checked out branch: main", auto_config_prompt)
        self.assertIn("Do not include compiler-cache mounts", auto_config_prompt)
        self.assertIn("Do not include Docker daemon socket mounts", auto_config_prompt)
        self.assertIn("Dockerfile file path: build context is repository root.", auto_config_prompt)
        self.assertIn("choose a Dockerfile file path when the Dockerfile needs repository-root context", auto_config_prompt)

    def test_first_url_in_text_trims_trailing_punctuation(self) -> None:
        value = hub_server._first_url_in_text(
            "Starting local login server on http://localhost:1455.",
            "http://localhost",
        )
        self.assertEqual(value, "http://localhost:1455")

    def test_parse_local_callback_allows_trailing_period(self) -> None:
        local_url, callback_port, callback_path = hub_server._parse_local_callback("http://localhost:1455.")
        self.assertEqual(callback_port, 1455)
        self.assertEqual(callback_path, "/auth/callback")
        self.assertTrue(local_url.startswith("http://localhost:1455"))

    def test_openai_auth_status_reports_account_credentials(self) -> None:
        self.state.openai_codex_auth_file.parent.mkdir(parents=True, exist_ok=True)
        self.state.openai_codex_auth_file.write_text(
            json.dumps(
                {
                    "auth_mode": "chatgpt",
                    "tokens": {
                        "refresh_token": "rt-test",
                    },
                }
            ),
            encoding="utf-8",
        )
        status = self.state.openai_auth_status()
        self.assertTrue(status["account_connected"])
        self.assertEqual(status["account_auth_mode"], "chatgpt")
        self.assertTrue(status["account_updated_at"])

    def test_start_openai_account_login_uses_host_network(self) -> None:
        captured: dict[str, list[str]] = {}
        self.state.local_supp_gids = f"{self.state.local_gid},3000,3001"

        def fake_popen(cmd: list[str], **kwargs):
            del kwargs
            captured["cmd"] = list(cmd)
            return SimpleNamespace(pid=4321, stdout=None, wait=lambda: 0, poll=lambda: None)

        with patch("agent_hub.server.shutil.which", return_value="/usr/bin/docker"), patch(
            "agent_hub.server._docker_image_exists",
            return_value=True,
        ), patch(
            "agent_hub.server.subprocess.Popen",
            side_effect=fake_popen,
        ), patch.object(
            hub_server.HubState,
            "_start_openai_login_reader",
            return_value=None,
        ):
            payload = self.state.start_openai_account_login(method="browser_callback")

        cmd = captured["cmd"]
        self.assertIn("--network", cmd)
        self.assertIn("host", cmd)
        self.assertIn("--tmpfs", cmd)
        self.assertIn(hub_server.TMP_DIR_TMPFS_SPEC, cmd)
        self.assertIn("--user", cmd)
        self.assertIn(f"{self.state.local_uid}:{self.state.local_gid}", cmd)
        self.assertIn("codex", cmd)
        self.assertIn("login", cmd)
        self.assertNotIn("--device-auth", cmd)
        self.assertNotIn(f"LOCAL_UID={self.state.local_uid}", cmd)
        image_idx = cmd.index(hub_server.DEFAULT_AGENT_IMAGE)
        codex_idx = cmd.index("codex")
        login_idx = cmd.index("login")
        self.assertLess(image_idx, codex_idx)
        self.assertLess(codex_idx, login_idx)
        for group in ("3000", "3001"):
            self.assertIn("--group-add", cmd)
            self.assertIn(group, cmd)
            group_idx = cmd.index(group)
            self.assertLess(group_idx, image_idx)
        self.assertNotIn("--group-add", cmd[login_idx + 1 :])
        container_home = hub_server.DEFAULT_CONTAINER_HOME
        self.assertNotIn(f"{self.state.host_agent_home}:{container_home}", cmd)
        self.assertIn(f"{self.state.host_codex_dir}:{container_home}/.codex", cmd)
        self.assertIn("session", payload)

    def test_start_openai_account_login_device_auth_includes_flag(self) -> None:
        captured: dict[str, list[str]] = {}

        def fake_popen(cmd: list[str], **kwargs):
            del kwargs
            captured["cmd"] = list(cmd)
            return SimpleNamespace(pid=4322, stdout=None, wait=lambda: 0, poll=lambda: None)

        with patch("agent_hub.server.shutil.which", return_value="/usr/bin/docker"), patch(
            "agent_hub.server._docker_image_exists",
            return_value=True,
        ), patch(
            "agent_hub.server.subprocess.Popen",
            side_effect=fake_popen,
        ), patch.object(
            hub_server.HubState,
            "_start_openai_login_reader",
            return_value=None,
        ):
            payload = self.state.start_openai_account_login(method="device_auth")

        cmd = captured["cmd"]
        self.assertIn("--device-auth", cmd)
        self.assertIn("session", payload)

    def test_forward_openai_account_callback_proxies_to_local_server(self) -> None:
        captured: dict[str, str] = {}

        class CallbackHandler(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                captured["path"] = self.path
                self.send_response(200)
                self.send_header("Content-Type", "text/plain")
                self.end_headers()
                self.wfile.write(b"ok")

            def log_message(self, format: str, *args) -> None:  # noqa: A003
                del format, args
                return

        server = HTTPServer(("127.0.0.1", 0), CallbackHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        try:
            callback_port = int(server.server_address[1])
            self.state._openai_login_session = hub_server.OpenAIAccountLoginSession(
                id="session-test",
                process=SimpleNamespace(pid=9991, poll=lambda: None),
                container_name="container-test",
                started_at="2026-02-21T00:00:00Z",
                status="waiting_for_browser",
                callback_port=callback_port,
                callback_path="/auth/callback",
            )
            with patch("agent_hub.server._is_process_running", return_value=True):
                result = self.state.forward_openai_account_callback("code=abc&state=xyz", path="/auth/callback")
            self.assertTrue(result["forwarded"])
            self.assertEqual(result["status_code"], 200)
            self.assertEqual(captured["path"], "/auth/callback?code=abc&state=xyz")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=1.0)

    def test_parse_env_vars_rejects_openai_api_key(self) -> None:
        with self.assertRaises(HTTPException):
            hub_server._parse_env_vars(["OPENAI_API_KEY=sk-test-abcdef"])

    def test_start_chat_filters_reserved_openai_env_vars(self) -> None:
        project = self.state.add_project(
            repo_url="https://example.com/org/repo.git",
            default_branch="main",
            setup_script="echo setup",
        )
        chat = self.state.create_chat(
            project["id"],
            profile="",
            ro_mounts=[],
            rw_mounts=[],
            env_vars=["OPENAI_API_KEY=should_not_pass", "FOO=bar"],
            agent_args=[],
        )

        captured: dict[str, list[str]] = {}

        def fake_clone(_: hub_server.HubState, chat_obj: dict[str, str], __: dict[str, str]) -> Path:
            workspace = self.state.chat_workdir(chat_obj["id"])
            workspace.mkdir(parents=True, exist_ok=True)
            return workspace

        class DummyProc:
            pid = 4242

        def fake_spawn(_: hub_server.HubState, _chat_id: str, cmd: list[str]) -> DummyProc:
            captured["cmd"] = list(cmd)
            return DummyProc()

        with patch.object(hub_server.HubState, "_ensure_chat_clone", fake_clone), patch.object(
            hub_server.HubState, "_sync_checkout_to_remote", lambda *args, **kwargs: None
        ), patch(
            "agent_hub.server._docker_image_exists",
            return_value=True,
        ), patch(
            "agent_hub.server._new_artifact_publish_token",
            return_value="artifact-token-test",
        ), patch.object(
            hub_server.HubState,
            "_spawn_chat_process",
            fake_spawn,
        ):
            self.state.start_chat(chat["id"])

        cmd = captured["cmd"]
        self.assertNotIn("OPENAI_API_KEY=should_not_pass", cmd)
        self.assertIn("FOO=bar", cmd)

    def test_start_chat_builds_cmd_with_mounts_env_and_repo_base_path(self) -> None:
        project = self.state.add_project(
            repo_url="https://example.com/org/repo.git",
            default_branch="main",
            base_image_mode="repo_path",
            base_image_value="docker/base",
            setup_script="echo setup",
        )
        chat = self.state.create_chat(
            project["id"],
            profile="fast",
            ro_mounts=[f"{self.host_ro}:/ro_data"],
            rw_mounts=[f"{self.host_rw}:/rw_data"],
            env_vars=["FOO=bar", "EMPTY="],
            agent_args=["--model", "gpt-5", "-c", 'model_reasoning_effort="high"'],
        )

        captured: dict[str, list[str]] = {}

        def fake_clone(_: hub_server.HubState, chat_obj: dict[str, str], __: dict[str, str]) -> Path:
            workspace = self.state.chat_workdir(chat_obj["id"])
            (workspace / "docker" / "base").mkdir(parents=True, exist_ok=True)
            return workspace

        class DummyProc:
            pid = 4242

        def fake_spawn(_: hub_server.HubState, _chat_id: str, cmd: list[str]) -> DummyProc:
            captured["cmd"] = list(cmd)
            return DummyProc()

        with patch.object(hub_server.HubState, "_ensure_chat_clone", fake_clone), patch.object(
            hub_server.HubState, "_sync_checkout_to_remote", lambda *args, **kwargs: None
        ), patch(
            "agent_hub.server._docker_image_exists",
            return_value=True,
        ), patch(
            "agent_hub.server._new_artifact_publish_token",
            return_value="artifact-token-test",
        ), patch.object(
            hub_server.HubState,
            "_spawn_chat_process",
            fake_spawn,
        ):
            self.state.start_chat(chat["id"])

        cmd = captured["cmd"]
        workspace = self.state.chat_workdir(chat["id"])
        self.assertIn("--base", cmd)
        base_index = cmd.index("--base")
        self.assertEqual(cmd[base_index + 1], str(workspace / "docker" / "base"))
        self.assertNotIn("--base-docker-context", cmd)
        self.assertNotIn("--base-dockerfile", cmd)
        self.assertIn("--credentials-file", cmd)
        self.assertIn(str(self.state.openai_credentials_file), cmd)
        self.assertIn("--agent-home-path", cmd)
        self.assertIn(str(self.state.host_agent_home), cmd)
        self.assertIn("--agent-command", cmd)
        self.assertIn("codex", cmd)
        self.assertIn("--container-project-name", cmd)
        container_name_index = cmd.index("--container-project-name")
        self.assertEqual(cmd[container_name_index + 1], "repo")
        self.assertIn("--no-alt-screen", cmd)
        self.assertIn("--ro-mount", cmd)
        self.assertIn(f"{self.host_ro}:/ro_data", cmd)
        self.assertIn("--rw-mount", cmd)
        self.assertIn(f"{self.host_rw}:/rw_data", cmd)
        self.assertIn("--env-var", cmd)
        self.assertIn("FOO=bar", cmd)
        self.assertIn("EMPTY=", cmd)
        self.assertIn(
            f"AGENT_HUB_ARTIFACTS_URL=http://host.docker.internal:{hub_server.DEFAULT_PORT}/api/chats/{chat['id']}/artifacts/publish",
            cmd,
        )
        self.assertIn("AGENT_HUB_ARTIFACT_TOKEN=artifact-token-test", cmd)
        self.assertIn("--snapshot-image-tag", cmd)
        self.assertIn(self.state._project_setup_snapshot_tag(project), cmd)
        self.assertIn("--", cmd)
        self.assertIn("--model", cmd)
        self.assertIn("gpt-5", cmd)
        self.assertIn('model_reasoning_effort="high"', cmd)
        started_chat = self.state.load()["chats"][chat["id"]]
        self.assertEqual(
            started_chat["artifact_publish_token_hash"],
            hub_server._hash_artifact_publish_token("artifact-token-test"),
        )
        self.assertEqual(
            started_chat["container_workspace"],
            f"{hub_server.DEFAULT_CONTAINER_HOME}/repo",
        )

    def test_start_chat_builds_cmd_with_repo_dockerfile_uses_workspace_context(self) -> None:
        project = self.state.add_project(
            repo_url="https://example.com/org/repo.git",
            default_branch="main",
            base_image_mode="repo_path",
            base_image_value="docker/development/Dockerfile",
            setup_script="echo setup",
        )
        chat = self.state.create_chat(
            project["id"],
            profile="fast",
            ro_mounts=[],
            rw_mounts=[],
            env_vars=[],
            agent_args=[],
        )

        captured: dict[str, list[str]] = {}

        def fake_clone(_: hub_server.HubState, chat_obj: dict[str, str], __: dict[str, str]) -> Path:
            workspace = self.state.chat_workdir(chat_obj["id"])
            dockerfile = workspace / "docker" / "development" / "Dockerfile"
            dockerfile.parent.mkdir(parents=True, exist_ok=True)
            dockerfile.write_text("FROM python:3.11-slim-bookworm\n", encoding="utf-8")
            return workspace

        class DummyProc:
            pid = 4242

        def fake_spawn(_: hub_server.HubState, _chat_id: str, cmd: list[str]) -> DummyProc:
            captured["cmd"] = list(cmd)
            return DummyProc()

        with patch.object(hub_server.HubState, "_ensure_chat_clone", fake_clone), patch.object(
            hub_server.HubState, "_sync_checkout_to_remote", lambda *args, **kwargs: None
        ), patch(
            "agent_hub.server._docker_image_exists",
            return_value=True,
        ), patch(
            "agent_hub.server._new_artifact_publish_token",
            return_value="artifact-token-test",
        ), patch.object(
            hub_server.HubState,
            "_spawn_chat_process",
            fake_spawn,
        ):
            self.state.start_chat(chat["id"])

        cmd = captured["cmd"]
        workspace = self.state.chat_workdir(chat["id"]).resolve()
        dockerfile = workspace / "docker" / "development" / "Dockerfile"
        self.assertNotIn("--base", cmd)
        self.assertIn("--base-docker-context", cmd)
        context_index = cmd.index("--base-docker-context")
        self.assertEqual(cmd[context_index + 1], str(workspace))
        self.assertIn("--base-dockerfile", cmd)
        dockerfile_index = cmd.index("--base-dockerfile")
        self.assertEqual(cmd[dockerfile_index + 1], str(dockerfile))

    def test_start_chat_uses_claude_agent_command_when_selected(self) -> None:
        project = self.state.add_project(
            repo_url="https://example.com/org/repo.git",
            default_branch="main",
            setup_script="echo setup",
        )
        chat = self.state.create_chat(
            project["id"],
            profile="",
            ro_mounts=[],
            rw_mounts=[],
            env_vars=[],
            agent_args=["--model", "sonnet"],
            agent_type="claude",
        )

        captured: dict[str, list[str]] = {}

        def fake_clone(_: hub_server.HubState, chat_obj: dict[str, str], __: dict[str, str]) -> Path:
            workspace = self.state.chat_workdir(chat_obj["id"])
            workspace.mkdir(parents=True, exist_ok=True)
            return workspace

        class DummyProc:
            pid = 4242

        def fake_spawn(_: hub_server.HubState, _chat_id: str, cmd: list[str]) -> DummyProc:
            captured["cmd"] = list(cmd)
            return DummyProc()

        with patch.object(hub_server.HubState, "_ensure_chat_clone", fake_clone), patch.object(
            hub_server.HubState, "_sync_checkout_to_remote", lambda *args, **kwargs: None
        ), patch(
            "agent_hub.server._docker_image_exists",
            return_value=True,
        ), patch(
            "agent_hub.server._new_artifact_publish_token",
            return_value="artifact-token-test",
        ), patch.object(
            hub_server.HubState,
            "_spawn_chat_process",
            fake_spawn,
        ):
            self.state.start_chat(chat["id"])

        cmd = captured["cmd"]
        self.assertIn("--agent-command", cmd)
        self.assertIn("claude", cmd)
        self.assertIn("--model", cmd)
        self.assertIn("sonnet", cmd)
        started_chat = self.state.load()["chats"][chat["id"]]
        self.assertEqual(started_chat["agent_type"], "claude")

    def test_start_chat_uses_gemini_agent_command_when_selected(self) -> None:
        project = self.state.add_project(
            repo_url="https://example.com/org/repo.git",
            default_branch="main",
            setup_script="echo setup",
        )
        chat = self.state.create_chat(
            project["id"],
            profile="",
            ro_mounts=[],
            rw_mounts=[],
            env_vars=[],
            agent_args=[],
            agent_type="gemini",
        )

        captured: dict[str, list[str]] = {}

        def fake_clone(_: hub_server.HubState, chat_obj: dict[str, str], __: dict[str, str]) -> Path:
            workspace = self.state.chat_workdir(chat_obj["id"])
            workspace.mkdir(parents=True, exist_ok=True)
            return workspace

        class DummyProc:
            pid = 4242

        def fake_spawn(_: hub_server.HubState, _chat_id: str, cmd: list[str]) -> DummyProc:
            captured["cmd"] = list(cmd)
            return DummyProc()

        with patch.object(hub_server.HubState, "_ensure_chat_clone", fake_clone), patch.object(
            hub_server.HubState, "_sync_checkout_to_remote", lambda *args, **kwargs: None
        ), patch(
            "agent_hub.server._docker_image_exists",
            return_value=True,
        ), patch(
            "agent_hub.server._new_artifact_publish_token",
            return_value="artifact-token-test",
        ), patch.object(
            hub_server.HubState,
            "_spawn_chat_process",
            fake_spawn,
        ):
            self.state.start_chat(chat["id"])

        cmd = captured["cmd"]
        self.assertIn("--agent-command", cmd)
        self.assertIn("gemini", cmd)
        started_chat = self.state.load()["chats"][chat["id"]]
        self.assertEqual(started_chat["agent_type"], "gemini")

    def test_start_chat_resume_for_codex_uses_agent_cli_resume_without_explicit_args(self) -> None:
        project = self.state.add_project(
            repo_url="https://example.com/org/repo.git",
            default_branch="main",
            setup_script="echo setup",
        )
        chat = self.state.create_chat(
            project["id"],
            profile="",
            ro_mounts=[],
            rw_mounts=[],
            env_vars=[],
            agent_args=["--model", "gpt-5.3-codex"],
            agent_type="codex",
        )

        captured: dict[str, list[str]] = {}

        def fake_clone(_: hub_server.HubState, chat_obj: dict[str, str], __: dict[str, str]) -> Path:
            workspace = self.state.chat_workdir(chat_obj["id"])
            workspace.mkdir(parents=True, exist_ok=True)
            return workspace

        class DummyProc:
            pid = 4242

        def fake_spawn(_: hub_server.HubState, _chat_id: str, cmd: list[str]) -> DummyProc:
            captured["cmd"] = list(cmd)
            return DummyProc()

        with patch.object(hub_server.HubState, "_ensure_chat_clone", fake_clone), patch.object(
            hub_server.HubState, "_sync_checkout_to_remote", lambda *args, **kwargs: None
        ), patch(
            "agent_hub.server._docker_image_exists",
            return_value=True,
        ), patch(
            "agent_hub.server._new_artifact_publish_token",
            return_value="artifact-token-test",
        ), patch.object(
            hub_server.HubState,
            "_spawn_chat_process",
            fake_spawn,
        ):
            self.state.start_chat(chat["id"], resume=True)

        cmd = captured["cmd"]
        self.assertIn("--resume", cmd)
        self.assertNotIn("--", cmd)
        self.assertNotIn("gpt-5.3-codex", cmd)

    def test_start_chat_resume_for_claude_adds_continue_flag(self) -> None:
        project = self.state.add_project(
            repo_url="https://example.com/org/repo.git",
            default_branch="main",
            setup_script="echo setup",
        )
        chat = self.state.create_chat(
            project["id"],
            profile="",
            ro_mounts=[],
            rw_mounts=[],
            env_vars=[],
            agent_args=["--model", "sonnet"],
            agent_type="claude",
        )

        captured: dict[str, list[str]] = {}

        def fake_clone(_: hub_server.HubState, chat_obj: dict[str, str], __: dict[str, str]) -> Path:
            workspace = self.state.chat_workdir(chat_obj["id"])
            workspace.mkdir(parents=True, exist_ok=True)
            return workspace

        class DummyProc:
            pid = 4242

        def fake_spawn(_: hub_server.HubState, _chat_id: str, cmd: list[str]) -> DummyProc:
            captured["cmd"] = list(cmd)
            return DummyProc()

        with patch.object(hub_server.HubState, "_ensure_chat_clone", fake_clone), patch.object(
            hub_server.HubState, "_sync_checkout_to_remote", lambda *args, **kwargs: None
        ), patch(
            "agent_hub.server._docker_image_exists",
            return_value=True,
        ), patch(
            "agent_hub.server._new_artifact_publish_token",
            return_value="artifact-token-test",
        ), patch.object(
            hub_server.HubState,
            "_spawn_chat_process",
            fake_spawn,
        ):
            self.state.start_chat(chat["id"], resume=True)

        cmd = captured["cmd"]
        self.assertIn("--", cmd)
        args_index = cmd.index("--")
        runtime_args = cmd[args_index + 1:]
        self.assertIn("--continue", runtime_args)
        self.assertIn("--model", runtime_args)
        self.assertIn("sonnet", runtime_args)

    def test_start_chat_resume_for_gemini_adds_resume_flag(self) -> None:
        project = self.state.add_project(
            repo_url="https://example.com/org/repo.git",
            default_branch="main",
            setup_script="echo setup",
        )
        chat = self.state.create_chat(
            project["id"],
            profile="",
            ro_mounts=[],
            rw_mounts=[],
            env_vars=[],
            agent_args=["--model", "gemini-2.5-pro"],
            agent_type="gemini",
        )

        captured: dict[str, list[str]] = {}

        def fake_clone(_: hub_server.HubState, chat_obj: dict[str, str], __: dict[str, str]) -> Path:
            workspace = self.state.chat_workdir(chat_obj["id"])
            workspace.mkdir(parents=True, exist_ok=True)
            return workspace

        class DummyProc:
            pid = 4242

        def fake_spawn(_: hub_server.HubState, _chat_id: str, cmd: list[str]) -> DummyProc:
            captured["cmd"] = list(cmd)
            return DummyProc()

        with patch.object(hub_server.HubState, "_ensure_chat_clone", fake_clone), patch.object(
            hub_server.HubState, "_sync_checkout_to_remote", lambda *args, **kwargs: None
        ), patch(
            "agent_hub.server._docker_image_exists",
            return_value=True,
        ), patch(
            "agent_hub.server._new_artifact_publish_token",
            return_value="artifact-token-test",
        ), patch.object(
            hub_server.HubState,
            "_spawn_chat_process",
            fake_spawn,
        ):
            self.state.start_chat(chat["id"], resume=True)

        cmd = captured["cmd"]
        self.assertIn("--", cmd)
        args_index = cmd.index("--")
        runtime_args = cmd[args_index + 1:]
        self.assertIn("--resume", runtime_args)
        self.assertIn("--model", runtime_args)
        self.assertIn("gemini-2.5-pro", runtime_args)

    def test_state_payload_marks_running_chat_container_outdated(self) -> None:
        project = self.state.add_project(
            repo_url="https://example.com/org/repo.git",
            default_branch="main",
            setup_script="echo setup",
        )
        chat = self.state.create_chat(
            project["id"],
            profile="",
            ro_mounts=[],
            rw_mounts=[],
            env_vars=[],
            agent_args=[],
        )
        latest_snapshot = self.state._project_setup_snapshot_tag(project)
        state_data = self.state.load()
        state_data["chats"][chat["id"]]["status"] = "running"
        state_data["chats"][chat["id"]]["pid"] = 7777
        state_data["chats"][chat["id"]]["setup_snapshot_image"] = "older-snapshot-tag"
        self.state.save(state_data)

        with patch("agent_hub.server._is_process_running", return_value=True):
            payload = self.state.state_payload()

        chat_payload = next(item for item in payload["chats"] if item["id"] == chat["id"])
        self.assertTrue(chat_payload["container_outdated"])
        self.assertIn("older-snapshot-tag", chat_payload["container_outdated_reason"])
        self.assertIn(latest_snapshot, chat_payload["container_outdated_reason"])

    def test_refresh_chat_container_closes_and_restarts_with_resume(self) -> None:
        project = self.state.add_project(
            repo_url="https://example.com/org/repo.git",
            default_branch="main",
            setup_script="echo setup",
        )
        chat = self.state.create_chat(
            project["id"],
            profile="",
            ro_mounts=[],
            rw_mounts=[],
            env_vars=[],
            agent_args=[],
            agent_type="claude",
        )
        state_data = self.state.load()
        state_data["chats"][chat["id"]]["status"] = "running"
        state_data["chats"][chat["id"]]["pid"] = 9988
        state_data["chats"][chat["id"]]["setup_snapshot_image"] = "older-snapshot-tag"
        self.state.save(state_data)

        captured: dict[str, object] = {}

        def fake_close(_: hub_server.HubState, chat_id: str) -> dict[str, str]:
            captured["closed"] = chat_id
            return {"id": chat_id, "status": "stopped"}

        def fake_start(_: hub_server.HubState, chat_id: str, resume: bool = False) -> dict[str, str]:
            captured["started"] = {"chat_id": chat_id, "resume": resume}
            return {"id": chat_id, "status": "running"}

        with patch("agent_hub.server._is_process_running", return_value=True), patch.object(
            hub_server.HubState, "close_chat", fake_close
        ), patch.object(
            hub_server.HubState, "start_chat", fake_start
        ):
            result = self.state.refresh_chat_container(chat["id"])

        self.assertEqual(captured["closed"], chat["id"])
        self.assertEqual(captured["started"], {"chat_id": chat["id"], "resume": True})
        self.assertEqual(result, {"id": chat["id"], "status": "running"})

    def test_start_chat_passes_github_app_credentials_when_configured(self) -> None:
        self._connect_github_app()
        project = self.state.add_project(
            repo_url="https://github.com/org/repo.git",
            default_branch="main",
            setup_script="echo setup",
        )
        chat = self.state.create_chat(
            project["id"],
            profile="",
            ro_mounts=[],
            rw_mounts=[],
            env_vars=[],
            agent_args=[],
        )

        captured: dict[str, list[str]] = {}

        def fake_clone(_: hub_server.HubState, chat_obj: dict[str, str], __: dict[str, str]) -> Path:
            workspace = self.state.chat_workdir(chat_obj["id"])
            workspace.mkdir(parents=True, exist_ok=True)
            return workspace

        class DummyProc:
            pid = 4242

        def fake_spawn(_: hub_server.HubState, _chat_id: str, cmd: list[str]) -> DummyProc:
            captured["cmd"] = list(cmd)
            return DummyProc()

        with patch.object(hub_server.HubState, "_ensure_chat_clone", fake_clone), patch.object(
            hub_server.HubState, "_sync_checkout_to_remote", lambda *args, **kwargs: None
        ), patch(
            "agent_hub.server._docker_image_exists",
            return_value=True,
        ), patch(
            "agent_hub.server._new_artifact_publish_token",
            return_value="artifact-token-test",
        ), patch.object(
            hub_server.HubState,
            "_spawn_chat_process",
            fake_spawn,
        ):
            self.state.start_chat(chat["id"])

        cmd = captured["cmd"]
        self.assertIn("--git-credential-file", cmd)
        self.assertIn(str(self.state.github_git_credentials_file), cmd)
        self.assertIn("--git-credential-host", cmd)
        self.assertIn("github.com", cmd)

    def test_start_chat_passes_github_pat_credentials_and_identity_when_configured(self) -> None:
        self._connect_github_pat()
        project = self.state.add_project(
            repo_url="https://github.com/org/repo.git",
            default_branch="main",
            setup_script="echo setup",
        )
        chat = self.state.create_chat(
            project["id"],
            profile="",
            ro_mounts=[],
            rw_mounts=[],
            env_vars=[],
            agent_args=[],
        )

        captured: dict[str, list[str]] = {}

        def fake_clone(_: hub_server.HubState, chat_obj: dict[str, str], __: dict[str, str]) -> Path:
            workspace = self.state.chat_workdir(chat_obj["id"])
            workspace.mkdir(parents=True, exist_ok=True)
            return workspace

        class DummyProc:
            pid = 4243

        def fake_spawn(_: hub_server.HubState, _chat_id: str, cmd: list[str]) -> DummyProc:
            captured["cmd"] = list(cmd)
            return DummyProc()

        with patch.object(hub_server.HubState, "_ensure_chat_clone", fake_clone), patch.object(
            hub_server.HubState, "_sync_checkout_to_remote", lambda *args, **kwargs: None
        ), patch(
            "agent_hub.server._docker_image_exists",
            return_value=True,
        ), patch(
            "agent_hub.server._new_artifact_publish_token",
            return_value="artifact-token-test",
        ), patch.object(
            hub_server.HubState,
            "_spawn_chat_process",
            fake_spawn,
        ):
            self.state.start_chat(chat["id"])

        cmd = captured["cmd"]
        self.assertIn("--git-credential-file", cmd)
        self.assertIn(str(self.state.github_git_credentials_file), cmd)
        self.assertIn("--git-credential-host", cmd)
        self.assertIn("github.com", cmd)
        self.assertIn("AGENT_HUB_GIT_USER_NAME=Agent User", cmd)
        self.assertIn("AGENT_HUB_GIT_USER_EMAIL=agentuser@example.com", cmd)

    def test_start_chat_uses_configured_artifact_publish_base_url(self) -> None:
        self.state.artifact_publish_base_url = "http://172.17.0.4:8765/hub"
        project = self.state.add_project(
            repo_url="https://example.com/org/repo.git",
            default_branch="main",
            setup_script="echo setup",
        )
        chat = self.state.create_chat(
            project["id"],
            profile="",
            ro_mounts=[],
            rw_mounts=[],
            env_vars=[],
            agent_args=[],
        )

        captured: dict[str, list[str]] = {}

        def fake_clone(_: hub_server.HubState, chat_obj: dict[str, str], __: dict[str, str]) -> Path:
            workspace = self.state.chat_workdir(chat_obj["id"])
            workspace.mkdir(parents=True, exist_ok=True)
            return workspace

        class DummyProc:
            pid = 4242

        def fake_spawn(_: hub_server.HubState, _chat_id: str, cmd: list[str]) -> DummyProc:
            captured["cmd"] = list(cmd)
            return DummyProc()

        with patch.object(hub_server.HubState, "_ensure_chat_clone", fake_clone), patch.object(
            hub_server.HubState, "_sync_checkout_to_remote", lambda *args, **kwargs: None
        ), patch(
            "agent_hub.server._docker_image_exists",
            return_value=True,
        ), patch(
            "agent_hub.server._new_artifact_publish_token",
            return_value="artifact-token-test",
        ), patch.object(
            hub_server.HubState,
            "_spawn_chat_process",
            fake_spawn,
        ):
            self.state.start_chat(chat["id"])

        self.assertIn(
            f"AGENT_HUB_ARTIFACTS_URL=http://172.17.0.4:8765/hub/api/chats/{chat['id']}/artifacts/publish",
            captured["cmd"],
        )
        self.assertIn("--system-prompt-file", captured["cmd"])
        system_prompt_index = captured["cmd"].index("--system-prompt-file")
        self.assertEqual(captured["cmd"][system_prompt_index + 1], str(self.state.system_prompt_file))

    def test_hub_state_rejects_invalid_artifact_publish_base_url(self) -> None:
        with self.assertRaises(ValueError):
            hub_server.HubState(
                data_dir=self.tmp_path / "hub-invalid-artifacts-base",
                config_file=self.config_file,
                artifact_publish_base_url="host.docker.internal:8765",
            )

    def test_start_chat_rejects_base_path_outside_workspace(self) -> None:
        project = self.state.add_project(
            repo_url="https://example.com/org/repo.git",
            default_branch="main",
            base_image_mode="repo_path",
            base_image_value="../outside",
        )
        chat = self.state.create_chat(
            project["id"],
            profile="",
            ro_mounts=[],
            rw_mounts=[],
            env_vars=[],
            agent_args=[],
        )

        def fake_clone(_: hub_server.HubState, chat_obj: dict[str, str], __: dict[str, str]) -> Path:
            workspace = self.state.chat_workdir(chat_obj["id"])
            workspace.mkdir(parents=True, exist_ok=True)
            return workspace

        with patch.object(hub_server.HubState, "_ensure_chat_clone", fake_clone), patch.object(
            hub_server.HubState, "_sync_checkout_to_remote", lambda *args, **kwargs: None
        ), patch(
            "agent_hub.server._docker_image_exists",
            return_value=True,
        ):
            with self.assertRaises(HTTPException):
                self.state.start_chat(chat["id"])

    def test_publish_chat_artifact_registers_download_metadata(self) -> None:
        project = self.state.add_project(
            repo_url="https://example.com/org/repo.git",
            default_branch="main",
        )
        chat = self.state.create_chat(
            project["id"],
            profile="",
            ro_mounts=[],
            rw_mounts=[],
            env_vars=[],
            agent_args=[],
        )
        workspace = self.state.chat_workdir(chat["id"])
        output_dir = workspace / "outputs"
        output_dir.mkdir(parents=True, exist_ok=True)
        artifact_file = output_dir / "summary.txt"
        artifact_file.write_text("artifact payload\n", encoding="utf-8")

        state_data = self.state.load()
        state_data["chats"][chat["id"]]["artifact_publish_token_hash"] = hub_server._hash_artifact_publish_token("token-abc")
        state_data["chats"][chat["id"]]["artifact_publish_token_issued_at"] = "2026-02-21T00:00:00Z"
        self.state.save(state_data)

        artifact = self.state.publish_chat_artifact(
            chat_id=chat["id"],
            token="token-abc",
            submitted_path="outputs/summary.txt",
            name="Run Summary",
        )
        self.assertEqual(artifact["name"], "Run Summary")
        self.assertEqual(artifact["relative_path"], "outputs/summary.txt")
        self.assertEqual(artifact["size_bytes"], len("artifact payload\n"))
        self.assertEqual(
            artifact["download_url"],
            f"/api/chats/{chat['id']}/artifacts/{artifact['id']}/download",
        )
        self.assertEqual(
            artifact["preview_url"],
            f"/api/chats/{chat['id']}/artifacts/{artifact['id']}/preview",
        )

        listed = self.state.list_chat_artifacts(chat["id"])
        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0]["id"], artifact["id"])
        self.assertEqual(
            listed[0]["preview_url"],
            f"/api/chats/{chat['id']}/artifacts/{artifact['id']}/preview",
        )

        payload = self.state.state_payload()
        chat_payload = next(item for item in payload["chats"] if item["id"] == chat["id"])
        self.assertNotIn("artifact_publish_token_hash", chat_payload)
        self.assertNotIn("artifact_publish_token_issued_at", chat_payload)
        self.assertEqual(len(chat_payload["artifacts"]), 1)
        self.assertEqual(chat_payload["artifact_current_ids"], [artifact["id"]])
        self.assertEqual(chat_payload["artifact_prompt_history"], [])

    def test_resolve_chat_artifact_preview_uses_media_type_without_download_name(self) -> None:
        project = self.state.add_project(
            repo_url="https://example.com/org/repo.git",
            default_branch="main",
        )
        chat = self.state.create_chat(
            project["id"],
            profile="",
            ro_mounts=[],
            rw_mounts=[],
            env_vars=[],
            agent_args=[],
        )
        workspace = self.state.chat_workdir(chat["id"])
        workspace.mkdir(parents=True, exist_ok=True)
        artifact_file = workspace / "plot.png"
        artifact_file.write_bytes(b"png-bytes")

        state_data = self.state.load()
        state_data["chats"][chat["id"]]["artifact_publish_token_hash"] = hub_server._hash_artifact_publish_token("token-preview")
        self.state.save(state_data)

        artifact = self.state.publish_chat_artifact(
            chat_id=chat["id"],
            token="token-preview",
            submitted_path="plot.png",
            name="plot output",
        )
        preview_path, media_type = self.state.resolve_chat_artifact_preview(chat["id"], artifact["id"])
        self.assertEqual(preview_path, artifact_file.resolve())
        self.assertEqual(media_type, "image/png")

    def test_record_chat_title_prompt_archives_current_artifacts_by_previous_prompt(self) -> None:
        project = self.state.add_project(
            repo_url="https://example.com/org/repo.git",
            default_branch="main",
        )
        chat = self.state.create_chat(
            project["id"],
            profile="",
            ro_mounts=[],
            rw_mounts=[],
            env_vars=[],
            agent_args=[],
        )
        workspace = self.state.chat_workdir(chat["id"])
        workspace.mkdir(parents=True, exist_ok=True)
        (workspace / "notes.txt").write_text("run output\n", encoding="utf-8")

        state_data = self.state.load()
        state_data["chats"][chat["id"]]["artifact_publish_token_hash"] = hub_server._hash_artifact_publish_token("token-archive")
        state_data["chats"][chat["id"]]["title_user_prompts"] = ["summarize yesterday's run logs"]
        self.state.save(state_data)

        artifact = self.state.publish_chat_artifact(
            chat_id=chat["id"],
            token="token-archive",
            submitted_path="notes.txt",
            name="Run Notes",
        )

        with patch.object(hub_server.HubState, "_schedule_chat_title_generation") as schedule_title:
            result = self.state.record_chat_title_prompt(chat["id"], "generate retry recommendations")
            self.assertTrue(result["recorded"])
            schedule_title.assert_called_once_with(chat["id"])

        updated = self.state.load()["chats"][chat["id"]]
        self.assertEqual(updated["artifact_current_ids"], [])
        self.assertEqual(len(updated["artifact_prompt_history"]), 1)
        archived_entry = updated["artifact_prompt_history"][0]
        self.assertEqual(archived_entry["prompt"], "summarize yesterday's run logs")
        self.assertTrue(archived_entry["archived_at"])
        self.assertEqual(len(archived_entry["artifacts"]), 1)
        self.assertEqual(archived_entry["artifacts"][0]["id"], artifact["id"])
        self.assertEqual(updated["title_user_prompts"][-1], "generate retry recommendations")

        payload = self.state.state_payload()
        chat_payload = next(item for item in payload["chats"] if item["id"] == chat["id"])
        self.assertEqual(chat_payload["artifact_current_ids"], [])
        self.assertEqual(len(chat_payload["artifact_prompt_history"]), 1)
        history_payload = chat_payload["artifact_prompt_history"][0]
        self.assertEqual(history_payload["prompt"], "summarize yesterday's run logs")
        self.assertEqual(len(history_payload["artifacts"]), 1)
        self.assertEqual(
            history_payload["artifacts"][0]["download_url"],
            f"/api/chats/{chat['id']}/artifacts/{artifact['id']}/download",
        )
        self.assertEqual(
            history_payload["artifacts"][0]["preview_url"],
            f"/api/chats/{chat['id']}/artifacts/{artifact['id']}/preview",
        )

    def test_load_backfills_current_artifact_ids_for_legacy_state(self) -> None:
        project = self.state.add_project(
            repo_url="https://example.com/org/repo.git",
            default_branch="main",
        )
        chat = self.state.create_chat(
            project["id"],
            profile="",
            ro_mounts=[],
            rw_mounts=[],
            env_vars=[],
            agent_args=[],
        )

        state_data = self.state.load()
        state_data["chats"][chat["id"]]["artifacts"] = [
            {
                "id": "artifact-legacy",
                "name": "Legacy File",
                "relative_path": "legacy.txt",
                "size_bytes": 12,
                "created_at": "2026-02-21T00:00:00Z",
            }
        ]
        state_data["chats"][chat["id"]].pop("artifact_current_ids", None)
        self.state.save(state_data)

        loaded = self.state.load()["chats"][chat["id"]]
        self.assertEqual(loaded["artifact_current_ids"], ["artifact-legacy"])

    def test_publish_chat_artifact_rejects_invalid_token(self) -> None:
        project = self.state.add_project(
            repo_url="https://example.com/org/repo.git",
            default_branch="main",
        )
        chat = self.state.create_chat(
            project["id"],
            profile="",
            ro_mounts=[],
            rw_mounts=[],
            env_vars=[],
            agent_args=[],
        )
        workspace = self.state.chat_workdir(chat["id"])
        workspace.mkdir(parents=True, exist_ok=True)
        (workspace / "output.txt").write_text("artifact", encoding="utf-8")

        state_data = self.state.load()
        state_data["chats"][chat["id"]]["artifact_publish_token_hash"] = hub_server._hash_artifact_publish_token("token-good")
        self.state.save(state_data)

        with self.assertRaises(HTTPException) as ctx:
            self.state.publish_chat_artifact(
                chat_id=chat["id"],
                token="token-bad",
                submitted_path="output.txt",
            )
        self.assertEqual(ctx.exception.status_code, 403)

    def test_publish_chat_artifact_rejects_paths_outside_workspace(self) -> None:
        project = self.state.add_project(
            repo_url="https://example.com/org/repo.git",
            default_branch="main",
        )
        chat = self.state.create_chat(
            project["id"],
            profile="",
            ro_mounts=[],
            rw_mounts=[],
            env_vars=[],
            agent_args=[],
        )

        state_data = self.state.load()
        state_data["chats"][chat["id"]]["artifact_publish_token_hash"] = hub_server._hash_artifact_publish_token("token-abc")
        self.state.save(state_data)

        with self.assertRaises(HTTPException) as ctx:
            self.state.publish_chat_artifact(
                chat_id=chat["id"],
                token="token-abc",
                submitted_path="../outside.txt",
            )
        self.assertEqual(ctx.exception.status_code, 400)

    def test_create_and_start_chat_rejects_when_project_build_is_not_ready(self) -> None:
        project = self.state.add_project(
            repo_url="https://example.com/org/repo.git",
            default_branch="main",
            setup_script="echo setup",
        )
        state_data = self.state.load()
        state_data["projects"][project["id"]]["build_status"] = "building"
        state_data["projects"][project["id"]]["setup_snapshot_image"] = ""
        self.state.save(state_data)

        with self.assertRaises(HTTPException):
            self.state.create_and_start_chat(project["id"])

    def test_create_and_start_chat_passes_agent_args(self) -> None:
        project = self.state.add_project(
            repo_url="https://example.com/org/repo.git",
            default_branch="main",
            setup_script="echo setup",
        )
        captured: dict[str, object] = {}

        def fake_create(
            _: hub_server.HubState,
            project_id: str,
            profile: str | None,
            ro_mounts: list[str],
            rw_mounts: list[str],
            env_vars: list[str],
            agent_args: list[str] | None = None,
            agent_type: str | None = None,
        ) -> dict[str, str]:
            captured["project_id"] = project_id
            captured["profile"] = profile
            captured["ro_mounts"] = list(ro_mounts)
            captured["rw_mounts"] = list(rw_mounts)
            captured["env_vars"] = list(env_vars)
            captured["agent_args"] = list(agent_args or [])
            captured["agent_type"] = str(agent_type or "")
            return {"id": "chat-created"}

        def fake_start(_: hub_server.HubState, chat_id: str) -> dict[str, str]:
            captured["started_chat_id"] = chat_id
            return {"id": chat_id, "status": "running"}

        with patch.object(hub_server.HubState, "create_chat", fake_create), patch.object(
            hub_server.HubState, "start_chat", fake_start
        ):
            result = self.state.create_and_start_chat(
                project["id"],
                agent_args=["--model", "gpt-5.3-codex", "-c", 'model_reasoning_effort="high"'],
            )

        self.assertEqual(captured["project_id"], project["id"])
        self.assertEqual(captured["profile"], "")
        self.assertEqual(captured["agent_args"], ["--model", "gpt-5.3-codex", "-c", 'model_reasoning_effort="high"'])
        self.assertEqual(captured["agent_type"], hub_server.DEFAULT_CHAT_AGENT_TYPE)
        self.assertEqual(captured["started_chat_id"], "chat-created")
        self.assertEqual(result["id"], "chat-created")

    def test_create_and_start_chat_passes_agent_type(self) -> None:
        project = self.state.add_project(
            repo_url="https://example.com/org/repo.git",
            default_branch="main",
            setup_script="echo setup",
        )
        captured: dict[str, object] = {}

        def fake_create(
            _: hub_server.HubState,
            project_id: str,
            profile: str | None,
            ro_mounts: list[str],
            rw_mounts: list[str],
            env_vars: list[str],
            agent_args: list[str] | None = None,
            agent_type: str | None = None,
        ) -> dict[str, str]:
            del project_id, profile, ro_mounts, rw_mounts, env_vars, agent_args
            captured["agent_type"] = agent_type
            return {"id": "chat-created"}

        with patch.object(hub_server.HubState, "create_chat", fake_create), patch.object(
            hub_server.HubState, "start_chat", return_value={"id": "chat-created", "status": "running"}
        ):
            result = self.state.create_and_start_chat(project["id"], agent_type="claude")

        self.assertEqual(captured["agent_type"], "claude")
        self.assertEqual(result["id"], "chat-created")

    def test_create_and_start_chat_uses_configured_default_agent_type(self) -> None:
        project = self.state.add_project(
            repo_url="https://example.com/org/repo.git",
            default_branch="main",
            setup_script="echo setup",
        )
        self.state.update_settings({"default_agent_type": "gemini"})
        captured: dict[str, object] = {}

        def fake_create(
            _: hub_server.HubState,
            project_id: str,
            profile: str | None,
            ro_mounts: list[str],
            rw_mounts: list[str],
            env_vars: list[str],
            agent_args: list[str] | None = None,
            agent_type: str | None = None,
        ) -> dict[str, str]:
            del project_id, profile, ro_mounts, rw_mounts, env_vars, agent_args
            captured["agent_type"] = agent_type
            return {"id": "chat-created"}

        with patch.object(hub_server.HubState, "create_chat", fake_create), patch.object(
            hub_server.HubState, "start_chat", return_value={"id": "chat-created", "status": "running"}
        ):
            self.state.create_and_start_chat(project["id"])

        self.assertEqual(captured["agent_type"], "gemini")

    def test_start_chat_rejects_when_stored_snapshot_tag_is_stale(self) -> None:
        project = self.state.add_project(
            repo_url="https://example.com/org/repo.git",
            default_branch="main",
            setup_script="echo setup",
        )

        state_data = self.state.load()
        state_data["projects"][project["id"]]["setup_snapshot_image"] = "stale-snapshot-tag"
        self.state.save(state_data)

        chat = self.state.create_chat(
            project["id"],
            profile="",
            ro_mounts=[],
            rw_mounts=[],
            env_vars=[],
            agent_args=[],
        )

        with patch(
            "agent_hub.server._docker_image_exists",
            return_value=True,
        ):
            with self.assertRaises(HTTPException):
                self.state.start_chat(chat["id"])

    def test_init_requeues_ready_project_when_snapshot_is_stale(self) -> None:
        data_dir = self.tmp_path / "hub-reconcile"
        data_dir.mkdir(parents=True, exist_ok=True)
        state_payload = hub_server._new_state()
        project_id = "project-stale"
        now = hub_server._iso_now()
        state_payload["projects"][project_id] = {
            "id": project_id,
            "name": "demo",
            "repo_url": "https://example.com/org/repo.git",
            "setup_script": "echo setup",
            "base_image_mode": "tag",
            "base_image_value": "nvidia/cuda:12.2.2-cudnn8-devel-ubuntu22.04",
            "default_ro_mounts": [],
            "default_rw_mounts": [],
            "default_env_vars": [],
            "default_branch": "main",
            "created_at": now,
            "updated_at": now,
            "setup_snapshot_image": "stale-snapshot-tag",
            "build_status": "ready",
            "build_error": "",
            "build_started_at": now,
            "build_finished_at": now,
        }
        (data_dir / hub_server.STATE_FILE_NAME).write_text(json.dumps(state_payload), encoding="utf-8")

        scheduled: list[str] = []

        def fake_schedule(_: hub_server.HubState, project_id: str) -> None:
            scheduled.append(project_id)

        with patch.object(hub_server.HubState, "_schedule_project_build", fake_schedule):
            reloaded = hub_server.HubState(data_dir, self.config_file)

        reloaded_state = reloaded.load()
        project = reloaded_state["projects"][project_id]
        self.assertEqual(project["build_status"], "pending")
        self.assertEqual(project["setup_snapshot_image"], "")
        self.assertEqual(project["build_error"], "")
        self.assertEqual(project["build_started_at"], "")
        self.assertEqual(project["build_finished_at"], "")
        self.assertEqual(scheduled, [project_id])

    def test_clean_start_clears_chat_artifacts_and_preserves_projects(self) -> None:
        project = self.state.add_project(
            repo_url="https://example.com/org/repo.git",
            default_branch="main",
            setup_script="echo setup",
        )
        chat = self.state.create_chat(
            project["id"],
            profile="",
            ro_mounts=[],
            rw_mounts=[],
            env_vars=[],
            agent_args=[],
        )

        chat_workspace = self.state.chat_workdir(chat["id"])
        project_workspace = self.state.project_workdir(project["id"])
        chat_workspace.mkdir(parents=True, exist_ok=True)
        project_workspace.mkdir(parents=True, exist_ok=True)
        self.state.chat_log(chat["id"]).write_text("log", encoding="utf-8")

        state_before = self.state.load()
        state_before["projects"][project["id"]]["setup_snapshot_image"] = "project-snapshot"
        state_before["chats"][chat["id"]]["setup_snapshot_image"] = "chat-snapshot"
        self.state.save(state_before)

        with patch("agent_hub.server._docker_remove_images") as docker_rm:
            summary = self.state.clean_start()

        self.assertEqual(summary["cleared_chats"], 1)
        self.assertGreaterEqual(summary["projects_reset"], 1)
        self.assertEqual(summary["docker_images_requested"], 2)

        state_after = self.state.load()
        self.assertIn(project["id"], state_after["projects"])
        self.assertEqual(state_after["chats"], {})
        self.assertEqual(state_after["projects"][project["id"]]["setup_snapshot_image"], "")
        self.assertEqual(state_after["projects"][project["id"]]["build_status"], "pending")
        self.assertTrue(self.state.chat_dir.exists())
        self.assertTrue(self.state.project_dir.exists())
        self.assertTrue(self.state.log_dir.exists())
        self.assertEqual(list(self.state.chat_dir.iterdir()), [])
        self.assertEqual(list(self.state.project_dir.iterdir()), [])
        self.assertEqual(list(self.state.log_dir.iterdir()), [])

        docker_rm.assert_called_once()
        prefixes, tags = docker_rm.call_args[0]
        self.assertEqual(prefixes, ("agent-hub-setup-", "agent-base-"))
        self.assertIn("project-snapshot", tags)
        self.assertIn("chat-snapshot", tags)

    def test_ensure_project_setup_snapshot_builds_once(self) -> None:
        self._connect_github_app()
        project = self.state.add_project(
            repo_url="https://github.com/org/repo.git",
            default_branch="main",
            setup_script="echo setup",
            base_image_mode="repo_path",
            base_image_value="docker/base",
            default_ro_mounts=[f"{self.host_ro}:/ro_data"],
            default_rw_mounts=[f"{self.host_rw}:/rw_data"],
            default_env_vars=["FOO=bar"],
        )
        workspace = self.tmp_path / "workspace"
        (workspace / "docker" / "base").mkdir(parents=True, exist_ok=True)

        executed: list[list[str]] = []

        def fake_run(cmd: list[str], cwd: Path | None = None, capture: bool = False, check: bool = True):
            del cwd, capture, check
            executed.append(list(cmd))
            class Dummy:
                returncode = 0
                stdout = ""
                stderr = ""
            return Dummy()

        with patch("agent_hub.server._docker_image_exists", side_effect=[False, True]), patch(
            "agent_hub.server._run", side_effect=fake_run
        ):
            first = self.state._ensure_project_setup_snapshot(workspace, project)
            second = self.state._ensure_project_setup_snapshot(workspace, project)

        self.assertEqual(first, second)
        self.assertEqual(len(executed), 1)
        cmd = executed[0]
        self.assertIn("agent_cli", cmd)
        self.assertIn("--prepare-snapshot-only", cmd)
        self.assertIn("--snapshot-image-tag", cmd)
        self.assertIn("--setup-script", cmd)
        self.assertIn("--container-project-name", cmd)
        container_name_index = cmd.index("--container-project-name")
        self.assertEqual(cmd[container_name_index + 1], "repo")
        self.assertIn("--agent-home-path", cmd)
        self.assertIn(str(self.state.host_agent_home), cmd)
        self.assertIn("--system-prompt-file", cmd)
        prompt_index = cmd.index("--system-prompt-file")
        self.assertEqual(cmd[prompt_index + 1], str(self.state.system_prompt_file))
        self.assertIn("--credentials-file", cmd)
        self.assertIn(str(self.state.openai_credentials_file), cmd)
        self.assertIn("--git-credential-file", cmd)
        self.assertIn(str(self.state.github_git_credentials_file), cmd)
        self.assertIn("--git-credential-host", cmd)
        self.assertIn("github.com", cmd)
        self.assertIn("--no-alt-screen", cmd)
        self.assertIn("--base", cmd)
        base_index = cmd.index("--base")
        self.assertEqual(cmd[base_index + 1], str(workspace / "docker" / "base"))
        self.assertNotIn("--base-docker-context", cmd)
        self.assertNotIn("--base-dockerfile", cmd)

    def test_delete_path_retries_after_permission_repair(self) -> None:
        path = self.tmp_path / "workspace-delete"
        path.mkdir(parents=True, exist_ok=True)

        attempts = {"count": 0}

        def fake_rmtree(target_path: Path) -> None:
            self.assertEqual(target_path, path)
            attempts["count"] += 1
            if attempts["count"] == 1:
                raise PermissionError("permission denied")

        with patch("agent_hub.server.shutil.rmtree", side_effect=fake_rmtree) as rmtree_call, patch(
            "agent_hub.server._docker_fix_path_ownership"
        ) as repair_call:
            self.state._delete_path(path)

        self.assertEqual(rmtree_call.call_count, 2)
        repair_call.assert_called_once_with(path, self.state.local_uid, self.state.local_gid)

    def test_ensure_project_setup_snapshot_uses_repo_root_context_for_repo_dockerfile(self) -> None:
        self._connect_github_app()
        project = self.state.add_project(
            repo_url="https://github.com/org/repo.git",
            default_branch="main",
            setup_script="echo setup",
            base_image_mode="repo_path",
            base_image_value="docker/development/Dockerfile",
        )
        workspace = self.tmp_path / "workspace-dockerfile"
        dockerfile = workspace / "docker" / "development" / "Dockerfile"
        dockerfile.parent.mkdir(parents=True, exist_ok=True)
        dockerfile.write_text("FROM python:3.11-slim-bookworm\n", encoding="utf-8")

        executed: list[list[str]] = []

        def fake_run(cmd: list[str], cwd: Path | None = None, capture: bool = False, check: bool = True):
            del cwd, capture, check
            executed.append(list(cmd))

            class Dummy:
                returncode = 0
                stdout = ""
                stderr = ""

            return Dummy()

        with patch("agent_hub.server._docker_image_exists", side_effect=[False]), patch(
            "agent_hub.server._run", side_effect=fake_run
        ):
            self.state._ensure_project_setup_snapshot(workspace, project)

        self.assertEqual(len(executed), 1)
        cmd = executed[0]
        self.assertNotIn("--base", cmd)
        self.assertIn("--base-docker-context", cmd)
        context_index = cmd.index("--base-docker-context")
        self.assertEqual(cmd[context_index + 1], str(workspace.resolve()))
        self.assertIn("--base-dockerfile", cmd)
        dockerfile_index = cmd.index("--base-dockerfile")
        self.assertEqual(cmd[dockerfile_index + 1], str(dockerfile.resolve()))

    def test_ensure_project_setup_snapshot_passes_git_identity_env_for_pat(self) -> None:
        self._connect_github_pat()
        project = self.state.add_project(
            repo_url="https://github.com/org/repo.git",
            default_branch="main",
            setup_script="echo setup",
        )
        workspace = self.tmp_path / "workspace-pat"
        workspace.mkdir(parents=True, exist_ok=True)

        executed: list[list[str]] = []

        def fake_run(cmd: list[str], cwd: Path | None = None, capture: bool = False, check: bool = True):
            del cwd, capture, check
            executed.append(list(cmd))
            class Dummy:
                returncode = 0
                stdout = ""
                stderr = ""
            return Dummy()

        with patch("agent_hub.server._docker_image_exists", side_effect=[False]), patch(
            "agent_hub.server._run", side_effect=fake_run
        ):
            self.state._ensure_project_setup_snapshot(workspace, project)

        self.assertEqual(len(executed), 1)
        cmd = executed[0]
        self.assertIn("agent_cli", cmd)
        self.assertIn("--git-credential-file", cmd)
        self.assertIn(str(self.state.github_git_credentials_file), cmd)
        self.assertIn("AGENT_HUB_GIT_USER_NAME=Agent User", cmd)
        self.assertIn("AGENT_HUB_GIT_USER_EMAIL=agentuser@example.com", cmd)

    def test_resize_terminal_sets_pty_size(self) -> None:
        runtime = hub_server.ChatRuntime(process=SimpleNamespace(pid=1), master_fd=42)
        with patch.object(hub_server.HubState, "_runtime_for_chat", return_value=runtime), patch(
            "agent_hub.server.fcntl.ioctl"
        ) as ioctl_mock, patch(
            "agent_hub.server.os.getpgid",
            return_value=1,
        ) as getpgid_mock, patch(
            "agent_hub.server.os.killpg"
        ) as killpg_mock:
            self.state.resize_terminal("chat-1", 120, 40)
        self.assertEqual(ioctl_mock.call_count, 1)
        getpgid_mock.assert_called_once_with(1)
        killpg_mock.assert_called_once_with(1, signal.SIGWINCH)

    def test_resize_terminal_falls_back_to_process_signal_when_group_signal_fails(self) -> None:
        runtime = hub_server.ChatRuntime(process=SimpleNamespace(pid=4321), master_fd=42)
        with patch.object(hub_server.HubState, "_runtime_for_chat", return_value=runtime), patch(
            "agent_hub.server.fcntl.ioctl"
        ), patch(
            "agent_hub.server.os.getpgid",
            return_value=4321,
        ), patch(
            "agent_hub.server.os.killpg",
            side_effect=OSError("group signal failed"),
        ), patch(
            "agent_hub.server.os.kill"
        ) as kill_mock:
            self.state.resize_terminal("chat-1", 100, 30)
        kill_mock.assert_called_once_with(4321, signal.SIGWINCH)

    def test_attach_terminal_returns_full_chat_log_history(self) -> None:
        project = self.state.add_project(
            repo_url="https://example.com/org/repo.git",
            default_branch="main",
        )
        chat = self.state.create_chat(
            project["id"],
            profile="",
            ro_mounts=[],
            rw_mounts=[],
            env_vars=[],
            agent_args=[],
        )
        log_text = "BEGIN_MARKER\n" + ("0123456789" * 25_000) + "\nEND_MARKER\n"
        self.state.chat_log(chat["id"]).write_text(log_text, encoding="utf-8")

        runtime = hub_server.ChatRuntime(process=SimpleNamespace(pid=1234), master_fd=42)
        with self.state._runtime_lock:
            self.state._chat_runtimes[chat["id"]] = runtime

        with patch("agent_hub.server._is_process_running", return_value=True):
            listener, backlog = self.state.attach_terminal(chat["id"])

        self.assertEqual(backlog, log_text)
        self.assertIn(listener, runtime.listeners)
        self.state.detach_terminal(chat["id"], listener)

    def test_record_chat_title_prompt_emits_state_changed_event(self) -> None:
        project = self.state.add_project(
            repo_url="https://example.com/org/repo.git",
            default_branch="main",
        )
        chat = self.state.create_chat(
            project["id"],
            profile="",
            ro_mounts=[],
            rw_mounts=[],
            env_vars=[],
            agent_args=[],
        )
        listener = self.state.attach_events()
        try:
            with patch.object(hub_server.HubState, "_schedule_chat_title_generation"):
                result = self.state.record_chat_title_prompt(chat["id"], "summarize websocket reconnect behavior")
            self.assertTrue(result["recorded"])
            event = listener.get_nowait()
            self.assertIsNotNone(event)
            assert event is not None
            self.assertEqual(event["type"], "state_changed")
        finally:
            self.state.detach_events(listener)

    def test_connect_openai_emits_auth_changed_event(self) -> None:
        listener = self.state.attach_events()
        try:
            self.state.connect_openai("sk-test-abcdefghijklmnopqrstuvwxyz1234", verify=False)
            event_types: list[str] = []
            while True:
                event = listener.get_nowait()
                if event is None:
                    continue
                event_types.append(str(event.get("type") or ""))
        except queue.Empty:
            pass
        finally:
            self.state.detach_events(listener)
        self.assertIn("auth_changed", event_types)

    def test_chat_workspace_uses_project_name_plus_chat_id(self) -> None:
        project = self.state.add_project(
            repo_url="https://example.com/org/repo.git",
            name="Demo Project",
            default_branch="main",
        )
        chat = self.state.create_chat(
            project["id"],
            profile="",
            ro_mounts=[],
            rw_mounts=[],
            env_vars=[],
            agent_args=[],
        )
        workspace = Path(chat["workspace"])
        self.assertEqual(workspace.name, f"Demo_Project_{chat['id']}")
        self.assertEqual(self.state.chat_workdir(chat["id"]), workspace)
        self.assertEqual(chat["container_workspace"], f"{hub_server.DEFAULT_CONTAINER_HOME}/Demo_Project")

    def test_close_chat_stops_runtime_and_keeps_workspace_and_chat_record(self) -> None:
        project = self.state.add_project(
            repo_url="https://example.com/org/repo.git",
            default_branch="main",
        )
        chat = self.state.create_chat(
            project["id"],
            profile="",
            ro_mounts=[],
            rw_mounts=[],
            env_vars=[],
            agent_args=[],
        )
        workspace = self.state.chat_workdir(chat["id"])
        workspace.mkdir(parents=True, exist_ok=True)
        (workspace / "sentinel.txt").write_text("data", encoding="utf-8")
        state_data = self.state.load()
        state_data["chats"][chat["id"]]["status"] = "running"
        state_data["chats"][chat["id"]]["pid"] = 9876
        state_data["chats"][chat["id"]]["artifact_publish_token_hash"] = hub_server._hash_artifact_publish_token("token-live")
        self.state.save(state_data)

        with patch("agent_hub.server._stop_process") as stop_process, patch.object(
            hub_server.HubState, "_close_runtime"
        ) as close_runtime:
            result = self.state.close_chat(chat["id"])

        stop_process.assert_called_once_with(9876)
        close_runtime.assert_called_once_with(chat["id"])
        self.assertEqual(result["status"], "stopped")
        self.assertIsNone(result["pid"])
        self.assertEqual(result["artifact_publish_token_hash"], "")
        self.assertTrue(workspace.exists())
        self.assertTrue((workspace / "sentinel.txt").exists())
        self.assertIn(chat["id"], self.state.load()["chats"])

    def test_state_payload_prunes_finished_chats(self) -> None:
        project = self.state.add_project(
            repo_url="https://example.com/org/repo.git",
            default_branch="main",
        )
        chat = self.state.create_chat(
            project["id"],
            profile="",
            ro_mounts=[],
            rw_mounts=[],
            env_vars=[],
            agent_args=[],
        )
        state_data = self.state.load()
        state_data["chats"][chat["id"]]["status"] = "running"
        state_data["chats"][chat["id"]]["pid"] = 424242
        self.state.save(state_data)

        with patch("agent_hub.server._is_process_running", return_value=False), patch(
            "agent_hub.server._stop_process"
        ):
            payload = self.state.state_payload()

        self.assertEqual(payload["chats"], [])
        self.assertNotIn(chat["id"], self.state.load()["chats"])

    def test_state_payload_keeps_new_stopped_chat(self) -> None:
        project = self.state.add_project(
            repo_url="https://example.com/org/repo.git",
            default_branch="main",
        )
        chat = self.state.create_chat(
            project["id"],
            profile="",
            ro_mounts=[],
            rw_mounts=[],
            env_vars=[],
            agent_args=[],
        )
        with patch("agent_hub.server._is_process_running", return_value=False):
            payload = self.state.state_payload()
        self.assertEqual(len(payload["chats"]), 1)
        self.assertEqual(payload["chats"][0]["id"], chat["id"])
        self.assertEqual(payload["chats"][0]["display_name"], "New Chat")
        self.assertEqual(payload["chats"][0]["status"], "stopped")
        self.assertIn(chat["id"], self.state.load()["chats"])

    def test_state_payload_sets_chat_display_name_and_subtitle(self) -> None:
        project = self.state.add_project(
            repo_url="https://example.com/org/repo.git",
            default_branch="main",
        )
        chat = self.state.create_chat(
            project["id"],
            profile="",
            ro_mounts=[],
            rw_mounts=[],
            env_vars=[],
            agent_args=[],
        )
        self.state.chat_log(chat["id"]).write_text(
            (
                "Tip: example\n"
                "\x1b[34m. Older status line\x1b[0m\n"
                "> how do i run tests?\n"
                "Intermediary output\n"
                "\x1b[32m. Use uv run python -m unittest discover -s tests -v\x1b[0m\n"
                "> fix login timeout handling\n"
            ),
            encoding="utf-8",
        )
        state_data = self.state.load()
        state_data["chats"][chat["id"]]["title_cached"] = "Run python unit tests"
        state_data["chats"][chat["id"]]["title_status"] = "ready"
        state_data["chats"][chat["id"]]["status"] = "running"
        state_data["chats"][chat["id"]]["pid"] = 1111
        self.state.save(state_data)

        with patch("agent_hub.server._is_process_running", return_value=True):
            payload = self.state.state_payload()

        chat_payload = next(item for item in payload["chats"] if item["id"] == chat["id"])
        self.assertEqual(chat_payload["display_name"], "Run python unit tests")
        self.assertEqual(chat_payload["display_subtitle"], "Use uv run python -m unittest discover -s tests -v")

    def test_state_payload_subtitle_strips_terminal_control_fragments(self) -> None:
        project = self.state.add_project(
            repo_url="https://example.com/org/repo.git",
            default_branch="main",
        )
        chat = self.state.create_chat(
            project["id"],
            profile="",
            ro_mounts=[],
            rw_mounts=[],
            env_vars=[],
            agent_args=[],
        )
        self.state.chat_log(chat["id"]).write_text(
            (
                "]10;rgb:e7e7/eded/f7f7 . Remove terminal color payload first\n"
                "> next prompt\n"
            ),
            encoding="utf-8",
        )
        state_data = self.state.load()
        state_data["chats"][chat["id"]]["status"] = "running"
        state_data["chats"][chat["id"]]["pid"] = 1111
        self.state.save(state_data)

        with patch("agent_hub.server._is_process_running", return_value=True):
            payload = self.state.state_payload()

        chat_payload = next(item for item in payload["chats"] if item["id"] == chat["id"])
        self.assertEqual(chat_payload["display_subtitle"], "Remove terminal color payload first")

    def test_state_payload_subtitle_uses_created_line_before_prompt(self) -> None:
        project = self.state.add_project(
            repo_url="https://example.com/org/repo.git",
            default_branch="main",
        )
        chat = self.state.create_chat(
            project["id"],
            profile="",
            ro_mounts=[],
            rw_mounts=[],
            env_vars=[],
            agent_args=[],
        )
        self.state.chat_log(chat["id"]).write_text(
            (
                "• Ran hub_artifact publish empty_test_files/*\n"
                "  └ Artifact published: test.bash (/api/chats/test/artifacts/a/download)\n"
                "    Artifact published: test.bat (/api/chats/test/artifacts/b/download)\n"
                "    … +113 lines\n"
                "    Artifact upload progress: 113/113 processed; 113 succeeded; 0 failed.\n"
                "    Published 113 artifacts.\n"
                "\n"
                "────────────────────────────────────────────────────────────────────────\n"
                "\n"
                "• Created 113 empty test files under empty_test_files/ (named like test.<ext>, spanning common code, config, doc, data, media, and archive extensions).\n"
                "\n"
                "  Published artifacts: 113/113 succeeded via hub_artifact publish empty_test_files/*.\n"
                "\n"
                "› Explain this codebase\n"
            ),
            encoding="utf-8",
        )
        state_data = self.state.load()
        state_data["chats"][chat["id"]]["status"] = "running"
        state_data["chats"][chat["id"]]["pid"] = 1111
        self.state.save(state_data)

        with patch("agent_hub.server._is_process_running", return_value=True):
            payload = self.state.state_payload()

        chat_payload = next(item for item in payload["chats"] if item["id"] == chat["id"])
        self.assertEqual(
            chat_payload["display_subtitle"],
            "Created 113 empty test files under empty_test_files/ (named like test.<ext>, spanning common code, config, doc, data, media, and archive extensions).",
        )

    def test_state_payload_subtitle_uses_hollow_bullet_working_line_before_prompt(self) -> None:
        project = self.state.add_project(
            repo_url="https://example.com/org/repo.git",
            default_branch="main",
        )
        chat = self.state.create_chat(
            project["id"],
            profile="",
            ro_mounts=[],
            rw_mounts=[],
            env_vars=[],
            agent_args=[],
        )
        self.state.chat_log(chat["id"]).write_text(
            (
                "› Make me empty example files of all common file extensions\n"
                "\n"
                "◦ Working (11s • esc to interrupt)\n"
                "\n"
                "› Implement {feature}\n"
            ),
            encoding="utf-8",
        )
        state_data = self.state.load()
        state_data["chats"][chat["id"]]["status"] = "running"
        state_data["chats"][chat["id"]]["pid"] = 1111
        self.state.save(state_data)

        with patch("agent_hub.server._is_process_running", return_value=True):
            payload = self.state.state_payload()

        chat_payload = next(item for item in payload["chats"] if item["id"] == chat["id"])
        self.assertEqual(chat_payload["display_subtitle"], "Working (11s • esc to interrupt)")

    def test_state_payload_subtitle_uses_alternate_circle_marker_before_prompt(self) -> None:
        project = self.state.add_project(
            repo_url="https://example.com/org/repo.git",
            default_branch="main",
        )
        chat = self.state.create_chat(
            project["id"],
            profile="",
            ro_mounts=[],
            rw_mounts=[],
            env_vars=[],
            agent_args=[],
        )
        self.state.chat_log(chat["id"]).write_text(
            (
                "› Make me empty example files of all common file extensions\n"
                "\n"
                "◉ Working (11s • esc to interrupt)\n"
                "\n"
                "› Implement {feature}\n"
            ),
            encoding="utf-8",
        )
        state_data = self.state.load()
        state_data["chats"][chat["id"]]["status"] = "running"
        state_data["chats"][chat["id"]]["pid"] = 1111
        self.state.save(state_data)

        with patch("agent_hub.server._is_process_running", return_value=True):
            payload = self.state.state_payload()

        chat_payload = next(item for item in payload["chats"] if item["id"] == chat["id"])
        self.assertEqual(chat_payload["display_subtitle"], "Working (11s • esc to interrupt)")

    def test_state_payload_subtitle_uses_last_animated_working_line_before_prompt(self) -> None:
        project = self.state.add_project(
            repo_url="https://example.com/org/repo.git",
            default_branch="main",
        )
        chat = self.state.create_chat(
            project["id"],
            profile="",
            ro_mounts=[],
            rw_mounts=[],
            env_vars=[],
            agent_args=[],
        )
        self.state.chat_log(chat["id"]).write_text(
            (
                "› Make me empty example files of all common file extensions\n"
                "◦ Working (9s • esc to interrupt)\r"
                "\x1b[2K◦ Working (10s • esc to interrupt)\r"
                "\x1b[2K◦ Working (11s • esc to interrupt)\r"
                "› Implement {feature}\n"
            ),
            encoding="utf-8",
        )
        state_data = self.state.load()
        state_data["chats"][chat["id"]]["status"] = "running"
        state_data["chats"][chat["id"]]["pid"] = 1111
        self.state.save(state_data)

        with patch("agent_hub.server._is_process_running", return_value=True):
            payload = self.state.state_payload()

        chat_payload = next(item for item in payload["chats"] if item["id"] == chat["id"])
        self.assertEqual(chat_payload["display_subtitle"], "Working (11s • esc to interrupt)")

    def test_state_payload_subtitle_uses_spinner_working_line_before_prompt(self) -> None:
        project = self.state.add_project(
            repo_url="https://example.com/org/repo.git",
            default_branch="main",
        )
        chat = self.state.create_chat(
            project["id"],
            profile="",
            ro_mounts=[],
            rw_mounts=[],
            env_vars=[],
            agent_args=[],
        )
        self.state.chat_log(chat["id"]).write_text(
            (
                "› Make me empty example files of all common file extensions\n"
                "⠋ Working (9s • esc to interrupt)\r"
                "\x1b[2K⠙ Working (10s • esc to interrupt)\r"
                "\x1b[2K⠹ Working (11s • esc to interrupt)\r"
                "› Implement {feature}\n"
            ),
            encoding="utf-8",
        )
        state_data = self.state.load()
        state_data["chats"][chat["id"]]["status"] = "running"
        state_data["chats"][chat["id"]]["pid"] = 1111
        self.state.save(state_data)

        with patch("agent_hub.server._is_process_running", return_value=True):
            payload = self.state.state_payload()

        chat_payload = next(item for item in payload["chats"] if item["id"] == chat["id"])
        self.assertEqual(chat_payload["display_subtitle"], "Working (11s • esc to interrupt)")

    def test_state_payload_subtitle_uses_cursor_animation_working_line_before_prompt(self) -> None:
        project = self.state.add_project(
            repo_url="https://example.com/org/repo.git",
            default_branch="main",
        )
        chat = self.state.create_chat(
            project["id"],
            profile="",
            ro_mounts=[],
            rw_mounts=[],
            env_vars=[],
            agent_args=[],
        )
        self.state.chat_log(chat["id"]).write_text(
            (
                "› Make me empty example files of all common file extensions\n"
                "\x1b[?2026h\x1b[15;2H\x1b[0m\x1b[49m\x1b[K⠋ Working (9s • esc to interrupt)\x1b[19;3H\x1b[?2026l"
                "\x1b[?2026h\x1b[15;2H\x1b[0m\x1b[49m\x1b[K⠙ Working (10s • esc to interrupt)\x1b[19;3H\x1b[?2026l"
                "\x1b[?2026h\x1b[15;2H\x1b[0m\x1b[49m\x1b[K⠹ Working (11s • esc to interrupt)\x1b[19;3H\x1b[?2026l"
                "\n› Implement {feature}\n"
            ),
            encoding="utf-8",
        )
        state_data = self.state.load()
        state_data["chats"][chat["id"]]["status"] = "running"
        state_data["chats"][chat["id"]]["pid"] = 1111
        self.state.save(state_data)

        with patch("agent_hub.server._is_process_running", return_value=True):
            payload = self.state.state_payload()

        chat_payload = next(item for item in payload["chats"] if item["id"] == chat["id"])
        self.assertEqual(chat_payload["display_subtitle"], "Working (11s • esc to interrupt)")

    def test_state_payload_subtitle_prefers_waiting_background_terminal_line(self) -> None:
        project = self.state.add_project(
            repo_url="https://example.com/org/repo.git",
            default_branch="main",
        )
        chat = self.state.create_chat(
            project["id"],
            profile="",
            ro_mounts=[],
            rw_mounts=[],
            env_vars=[],
            agent_args=[],
        )
        self.state.chat_log(chat["id"]).write_text(
            (
                "• Files are generated under example_files/common_extensions (155 files total).\n"
                "\n"
                "• Ran hub_artifact publish example_files/common_extensions\n"
                "  └ Artifact published: Dockerfile (/api/chats/test/artifacts/a/download)\n"
                "    Artifact upload progress: 155/155 processed; 155 succeeded; 0 failed.\n"
                "    Published 155 artifacts.\n"
                "\n"
                "\u200b• Waiting for background terminal (49s • esc to interrupt)\n"
                "\n"
                "› Use /skills to list available skills\n"
            ),
            encoding="utf-8",
        )
        state_data = self.state.load()
        state_data["chats"][chat["id"]]["status"] = "running"
        state_data["chats"][chat["id"]]["pid"] = 1111
        self.state.save(state_data)

        with patch("agent_hub.server._is_process_running", return_value=True):
            payload = self.state.state_payload()

        chat_payload = next(item for item in payload["chats"] if item["id"] == chat["id"])
        self.assertEqual(chat_payload["display_subtitle"], "Waiting for background terminal (49s • esc to interrupt)")

    def test_write_terminal_input_records_prompt_only_on_submit(self) -> None:
        project = self.state.add_project(
            repo_url="https://example.com/org/repo.git",
            default_branch="main",
        )
        chat = self.state.create_chat(
            project["id"],
            profile="",
            ro_mounts=[],
            rw_mounts=[],
            env_vars=[],
            agent_args=[],
        )
        runtime = hub_server.ChatRuntime(process=SimpleNamespace(pid=1234), master_fd=42)

        with patch.object(hub_server.HubState, "_runtime_for_chat", return_value=runtime), patch(
            "agent_hub.server.os.write", return_value=1
        ), patch.object(
            hub_server.HubState, "_schedule_chat_title_generation"
        ) as schedule_title:
            self.state.write_terminal_input(chat["id"], "fix flaky login tests")
            schedule_title.assert_not_called()
            self.state.write_terminal_input(chat["id"], "\r")
            schedule_title.assert_called_once_with(chat["id"])

        updated = self.state.load()["chats"][chat["id"]]
        self.assertEqual(updated["title_user_prompts"][-1], "fix flaky login tests")

    def test_write_terminal_input_does_not_set_title_cached_before_generation(self) -> None:
        project = self.state.add_project(
            repo_url="https://example.com/org/repo.git",
            default_branch="main",
        )
        chat = self.state.create_chat(
            project["id"],
            profile="",
            ro_mounts=[],
            rw_mounts=[],
            env_vars=[],
            agent_args=[],
        )
        runtime = hub_server.ChatRuntime(process=SimpleNamespace(pid=1234), master_fd=42)

        with patch.object(hub_server.HubState, "_runtime_for_chat", return_value=runtime), patch(
            "agent_hub.server.os.write", return_value=1
        ), patch.object(
            hub_server.HubState, "_schedule_chat_title_generation"
        ):
            self.state.write_terminal_input(chat["id"], "investigate websocket close loop")
            self.state.write_terminal_input(chat["id"], "\r")

        updated = self.state.load()["chats"][chat["id"]]
        self.assertEqual(updated["title_cached"], "")
        self.assertEqual(updated["title_source"], "openai")
        self.assertEqual(updated["title_status"], "pending")
        self.assertEqual(updated["title_error"], "")

    def test_write_terminal_input_keeps_openai_title_until_regenerated(self) -> None:
        project = self.state.add_project(
            repo_url="https://example.com/org/repo.git",
            default_branch="main",
        )
        chat = self.state.create_chat(
            project["id"],
            profile="",
            ro_mounts=[],
            rw_mounts=[],
            env_vars=[],
            agent_args=[],
        )
        state_data = self.state.load()
        state_data["chats"][chat["id"]]["title_cached"] = "Fix flaky CI auth smoke tests"
        state_data["chats"][chat["id"]]["title_source"] = "openai"
        self.state.save(state_data)
        runtime = hub_server.ChatRuntime(process=SimpleNamespace(pid=1234), master_fd=42)

        with patch.object(hub_server.HubState, "_runtime_for_chat", return_value=runtime), patch(
            "agent_hub.server.os.write", return_value=1
        ), patch.object(
            hub_server.HubState, "_schedule_chat_title_generation"
        ):
            self.state.write_terminal_input(chat["id"], "add an auth retry budget by environment")
            self.state.write_terminal_input(chat["id"], "\r")

        updated = self.state.load()["chats"][chat["id"]]
        self.assertEqual(updated["title_cached"], "Fix flaky CI auth smoke tests")
        self.assertEqual(updated["title_source"], "openai")
        self.assertEqual(updated["title_status"], "pending")
        self.assertEqual(updated["title_user_prompts"][-1], "add an auth retry budget by environment")

    def test_write_terminal_input_treats_application_keypad_enter_as_submit(self) -> None:
        project = self.state.add_project(
            repo_url="https://example.com/org/repo.git",
            default_branch="main",
        )
        chat = self.state.create_chat(
            project["id"],
            profile="",
            ro_mounts=[],
            rw_mounts=[],
            env_vars=[],
            agent_args=[],
        )
        runtime = hub_server.ChatRuntime(process=SimpleNamespace(pid=1234), master_fd=42)

        with patch.object(hub_server.HubState, "_runtime_for_chat", return_value=runtime), patch(
            "agent_hub.server.os.write", return_value=1
        ), patch.object(
            hub_server.HubState, "_schedule_chat_title_generation"
        ) as schedule_title:
            self.state.write_terminal_input(chat["id"], "summarize deploy failures")
            schedule_title.assert_not_called()
            self.state.write_terminal_input(chat["id"], "\x1bOM")
            schedule_title.assert_called_once_with(chat["id"])

        updated = self.state.load()["chats"][chat["id"]]
        self.assertEqual(updated["title_user_prompts"][-1], "summarize deploy failures")

    def test_write_terminal_input_strips_split_osc_color_fragments(self) -> None:
        project = self.state.add_project(
            repo_url="https://example.com/org/repo.git",
            default_branch="main",
        )
        chat = self.state.create_chat(
            project["id"],
            profile="",
            ro_mounts=[],
            rw_mounts=[],
            env_vars=[],
            agent_args=[],
        )
        runtime = hub_server.ChatRuntime(process=SimpleNamespace(pid=1234), master_fd=42)
        prompt = "Examine the repository and fix flaky tests"

        with patch.object(hub_server.HubState, "_runtime_for_chat", return_value=runtime), patch(
            "agent_hub.server.os.write", return_value=1
        ), patch.object(
            hub_server.HubState, "_schedule_chat_title_generation"
        ) as schedule_title:
            self.state.write_terminal_input(chat["id"], "\x1b]10;rgb:e7e7/eded/f7f7")
            self.state.write_terminal_input(chat["id"], "\x1b\\")
            self.state.write_terminal_input(chat["id"], "\x1b]11;rgb:0b0b/1010/1818")
            self.state.write_terminal_input(chat["id"], "\x1b\\")
            self.state.write_terminal_input(chat["id"], prompt)
            self.state.write_terminal_input(chat["id"], "\r")
            schedule_title.assert_called_once_with(chat["id"])

        updated = self.state.load()["chats"][chat["id"]]
        self.assertEqual(updated["title_user_prompts"][-1], prompt)

    def test_submit_chat_input_buffer_records_pending_prompt(self) -> None:
        project = self.state.add_project(
            repo_url="https://example.com/org/repo.git",
            default_branch="main",
        )
        chat = self.state.create_chat(
            project["id"],
            profile="",
            ro_mounts=[],
            rw_mounts=[],
            env_vars=[],
            agent_args=[],
        )
        with self.state._chat_input_lock:
            self.state._chat_input_buffers[chat["id"]] = "triage reconnect failures in websocket transport"

        with patch.object(hub_server.HubState, "_schedule_chat_title_generation") as schedule_title:
            self.state.submit_chat_input_buffer(chat["id"])
            schedule_title.assert_called_once_with(chat["id"])

        updated = self.state.load()["chats"][chat["id"]]
        self.assertEqual(updated["title_status"], "pending")
        self.assertEqual(updated["title_user_prompts"][-1], "triage reconnect failures in websocket transport")
        with self.state._chat_input_lock:
            self.assertEqual(self.state._chat_input_buffers.get(chat["id"]), "")

    def test_record_chat_title_prompt_records_pending_prompt(self) -> None:
        project = self.state.add_project(
            repo_url="https://example.com/org/repo.git",
            default_branch="main",
        )
        chat = self.state.create_chat(
            project["id"],
            profile="",
            ro_mounts=[],
            rw_mounts=[],
            env_vars=[],
            agent_args=[],
        )

        with patch.object(hub_server.HubState, "_schedule_chat_title_generation") as schedule_title:
            result = self.state.record_chat_title_prompt(chat["id"], "investigate reconnect jitter in socket loop")
            schedule_title.assert_called_once_with(chat["id"])

        self.assertTrue(result["recorded"])
        updated = self.state.load()["chats"][chat["id"]]
        self.assertEqual(updated["title_status"], "pending")
        self.assertEqual(updated["title_user_prompts"][-1], "investigate reconnect jitter in socket loop")

    def test_record_chat_title_prompt_deduplicates_repeat_submit(self) -> None:
        project = self.state.add_project(
            repo_url="https://example.com/org/repo.git",
            default_branch="main",
        )
        chat = self.state.create_chat(
            project["id"],
            profile="",
            ro_mounts=[],
            rw_mounts=[],
            env_vars=[],
            agent_args=[],
        )

        with patch.object(hub_server.HubState, "_schedule_chat_title_generation") as schedule_title:
            first = self.state.record_chat_title_prompt(chat["id"], "check reconnect timeout handling")
            second = self.state.record_chat_title_prompt(chat["id"], "check reconnect timeout handling")
            self.assertTrue(first["recorded"])
            self.assertFalse(second["recorded"])
            schedule_title.assert_called_once_with(chat["id"])

        updated = self.state.load()["chats"][chat["id"]]
        self.assertEqual(updated["title_user_prompts"], ["check reconnect timeout handling"])

    def test_record_chat_title_prompt_keeps_unbounded_history(self) -> None:
        project = self.state.add_project(
            repo_url="https://example.com/org/repo.git",
            default_branch="main",
        )
        chat = self.state.create_chat(
            project["id"],
            profile="",
            ro_mounts=[],
            rw_mounts=[],
            env_vars=[],
            agent_args=[],
        )

        prompts = [f"prompt {index}" for index in range(1, 90)]
        with patch.object(hub_server.HubState, "_schedule_chat_title_generation"):
            for prompt in prompts:
                result = self.state.record_chat_title_prompt(chat["id"], prompt)
                self.assertTrue(result["recorded"])

        updated = self.state.load()["chats"][chat["id"]]
        self.assertEqual(updated["title_user_prompts"], prompts)

    def test_write_terminal_input_ignores_terminal_control_payload(self) -> None:
        project = self.state.add_project(
            repo_url="https://example.com/org/repo.git",
            default_branch="main",
        )
        chat = self.state.create_chat(
            project["id"],
            profile="",
            ro_mounts=[],
            rw_mounts=[],
            env_vars=[],
            agent_args=[],
        )
        runtime = hub_server.ChatRuntime(process=SimpleNamespace(pid=1234), master_fd=42)
        control_payload = "\x1b]10;rgb:e7e7/eded/f7f7\x1b\\\x1b]11;rgb:0b0b/1010/1818\x1b\\\r"

        with patch.object(hub_server.HubState, "_runtime_for_chat", return_value=runtime), patch(
            "agent_hub.server.os.write", return_value=1
        ), patch.object(
            hub_server.HubState, "_schedule_chat_title_generation"
        ) as schedule_title:
            self.state.write_terminal_input(chat["id"], control_payload)
            schedule_title.assert_not_called()

        updated = self.state.load()["chats"][chat["id"]]
        self.assertEqual(updated.get("title_user_prompts"), [])

    def test_generate_and_store_chat_title_uses_openai_once_per_prompt_fingerprint(self) -> None:
        project = self.state.add_project(
            repo_url="https://example.com/org/repo.git",
            default_branch="main",
        )
        chat = self.state.create_chat(
            project["id"],
            profile="",
            ro_mounts=[],
            rw_mounts=[],
            env_vars=[],
            agent_args=[],
        )
        state_data = self.state.load()
        state_data["chats"][chat["id"]]["title_user_prompts"] = ["first prompt", "second prompt"]
        self.state.save(state_data)

        with patch("agent_hub.server._read_codex_auth", return_value=(False, "")), patch(
            "agent_hub.server._read_openai_api_key", return_value="sk-test"
        ), patch(
            "agent_hub.server._openai_generate_chat_title",
            return_value="Fix flaky login tests in auth flow",
        ) as generate_title:
            self.state._generate_and_store_chat_title(chat["id"])
            self.state._generate_and_store_chat_title(chat["id"])

        self.assertEqual(generate_title.call_count, 1)
        generate_title.assert_called_once_with(
            api_key="sk-test",
            user_prompts=["first prompt", "second prompt"],
            max_chars=hub_server.CHAT_TITLE_MAX_CHARS,
        )
        updated = self.state.load()["chats"][chat["id"]]
        self.assertEqual(updated["title_cached"], "Fix flaky login tests in auth flow")
        self.assertEqual(updated["title_source"], "openai")
        self.assertEqual(updated["title_status"], "ready")
        self.assertEqual(updated["title_error"], "")
        self.assertTrue(updated["title_prompt_fingerprint"])

    def test_generate_and_store_chat_title_passes_full_prompt_history_to_generator(self) -> None:
        project = self.state.add_project(
            repo_url="https://example.com/org/repo.git",
            default_branch="main",
        )
        chat = self.state.create_chat(
            project["id"],
            profile="",
            ro_mounts=[],
            rw_mounts=[],
            env_vars=[],
            agent_args=[],
        )
        prompts = [f"prompt {index}" for index in range(1, 90)]
        state_data = self.state.load()
        state_data["chats"][chat["id"]]["title_user_prompts"] = prompts
        self.state.save(state_data)

        with patch("agent_hub.server._read_codex_auth", return_value=(False, "")), patch(
            "agent_hub.server._read_openai_api_key", return_value="sk-test"
        ), patch(
            "agent_hub.server._openai_generate_chat_title",
            return_value="Investigate websocket reconnect stability and retry behavior",
        ) as generate_title:
            self.state._generate_and_store_chat_title(chat["id"])

        generate_title.assert_called_once_with(
            api_key="sk-test",
            user_prompts=prompts,
            max_chars=hub_server.CHAT_TITLE_MAX_CHARS,
        )

    def test_generate_and_store_chat_title_records_openai_error(self) -> None:
        project = self.state.add_project(
            repo_url="https://example.com/org/repo.git",
            default_branch="main",
        )
        chat = self.state.create_chat(
            project["id"],
            profile="",
            ro_mounts=[],
            rw_mounts=[],
            env_vars=[],
            agent_args=[],
        )
        state_data = self.state.load()
        state_data["chats"][chat["id"]]["title_user_prompts"] = ["debug websocket reconnect issue"]
        self.state.save(state_data)

        with patch("agent_hub.server._read_codex_auth", return_value=(False, "")), patch(
            "agent_hub.server._read_openai_api_key", return_value="sk-test"
        ), patch(
            "agent_hub.server._openai_generate_chat_title",
            side_effect=RuntimeError("OpenAI title generation failed"),
        ):
            self.state._generate_and_store_chat_title(chat["id"])

        updated = self.state.load()["chats"][chat["id"]]
        self.assertEqual(updated["title_status"], "error")
        self.assertEqual(updated["title_source"], "openai")
        self.assertIn("OpenAI title generation failed", updated["title_error"])

    def test_generate_and_store_chat_title_records_missing_credentials_error(self) -> None:
        project = self.state.add_project(
            repo_url="https://example.com/org/repo.git",
            default_branch="main",
        )
        chat = self.state.create_chat(
            project["id"],
            profile="",
            ro_mounts=[],
            rw_mounts=[],
            env_vars=[],
            agent_args=[],
        )
        state_data = self.state.load()
        state_data["chats"][chat["id"]]["title_user_prompts"] = ["build a release checklist"]
        self.state.save(state_data)

        with patch("agent_hub.server._read_codex_auth", return_value=(False, "")), patch(
            "agent_hub.server._read_openai_api_key", return_value=""
        ):
            self.state._generate_and_store_chat_title(chat["id"])

        updated = self.state.load()["chats"][chat["id"]]
        self.assertEqual(updated["title_status"], "error")
        self.assertEqual(updated["title_source"], "openai")
        self.assertIn("No OpenAI credentials configured", updated["title_error"])

    def test_generate_and_store_chat_title_uses_connected_account(self) -> None:
        project = self.state.add_project(
            repo_url="https://example.com/org/repo.git",
            default_branch="main",
        )
        chat = self.state.create_chat(
            project["id"],
            profile="",
            ro_mounts=[],
            rw_mounts=[],
            env_vars=[],
            agent_args=[],
        )
        state_data = self.state.load()
        state_data["chats"][chat["id"]]["title_user_prompts"] = ["triage flaky websocket reconnect issue"]
        self.state.save(state_data)

        with patch("agent_hub.server._read_codex_auth", return_value=(True, "chatgpt")), patch(
            "agent_hub.server._codex_generate_chat_title",
            return_value="Triage flaky websocket reconnect issue",
        ) as generate_title:
            self.state._generate_and_store_chat_title(chat["id"])

        updated = self.state.load()["chats"][chat["id"]]
        self.assertEqual(updated["title_status"], "ready")
        self.assertEqual(updated["title_cached"], "Triage flaky websocket reconnect issue")
        generate_title.assert_called_once_with(
            host_agent_home=self.state.host_agent_home,
            host_codex_dir=self.state.host_codex_dir,
            user_prompts=["triage flaky websocket reconnect issue"],
            max_chars=hub_server.CHAT_TITLE_MAX_CHARS,
        )

    def test_parse_json_object_from_text_accepts_markdown_fences(self) -> None:
        payload = hub_server._parse_json_object_from_text(
            "```json\n{\"base_image_mode\":\"tag\",\"base_image_value\":\"ubuntu:22.04\"}\n```"
        )
        self.assertEqual(payload["base_image_mode"], "tag")
        self.assertEqual(payload["base_image_value"], "ubuntu:22.04")

    def test_normalize_auto_config_recommendation_adds_apt_update_and_cache_mount(self) -> None:
        workspace = self.tmp_path / "workspace-cache"
        workspace.mkdir(parents=True, exist_ok=True)
        (workspace / "CMakeLists.txt").write_text(
            "set(CMAKE_CXX_COMPILER_LAUNCHER ccache)\n",
            encoding="utf-8",
        )
        fake_home = self.tmp_path / "fake-home"
        fake_home.mkdir(parents=True, exist_ok=True)
        with patch("agent_hub.server.Path.home", return_value=fake_home):
            recommendation = self.state._normalize_auto_config_recommendation(
                {
                    "base_image_mode": "tag",
                    "base_image_value": "ubuntu:22.04",
                    "setup_script": "apt-get install -y ninja-build",
                    "default_ro_mounts": [],
                    "default_rw_mounts": [],
                    "default_env_vars": [],
                    "notes": "minimal setup",
                },
                workspace,
            )

        self.assertEqual(recommendation["setup_script"].splitlines()[0], "apt-get update")
        expected_mount = f"{fake_home / '.ccache'}:{hub_server.DEFAULT_CONTAINER_HOME}/.ccache"
        unexpected_mount = f"{fake_home / '.cache' / 'sccache'}:{hub_server.DEFAULT_CONTAINER_HOME}/.cache/sccache"
        self.assertIn(expected_mount, recommendation["default_rw_mounts"])
        self.assertNotIn(unexpected_mount, recommendation["default_rw_mounts"])
        self.assertTrue((fake_home / ".ccache").exists())

    def test_normalize_auto_config_recommendation_drops_undetected_cache_mounts(self) -> None:
        workspace = self.tmp_path / "workspace-no-cache"
        workspace.mkdir(parents=True, exist_ok=True)
        (workspace / "README.md").write_text(
            "This text mentions ccache and sccache but does not configure either tool.\n",
            encoding="utf-8",
        )
        fake_home = self.tmp_path / "fake-home-no-cache"
        ccache_host = fake_home / ".ccache"
        sccache_host = fake_home / ".cache" / "sccache"
        ccache_host.mkdir(parents=True, exist_ok=True)
        sccache_host.mkdir(parents=True, exist_ok=True)

        with patch("agent_hub.server.Path.home", return_value=fake_home):
            recommendation = self.state._normalize_auto_config_recommendation(
                {
                    "base_image_mode": "tag",
                    "base_image_value": "ubuntu:22.04",
                    "setup_script": "",
                    "default_ro_mounts": [],
                    "default_rw_mounts": [
                        f"{ccache_host}:{hub_server.DEFAULT_CONTAINER_HOME}/.ccache",
                        f"{sccache_host}:{hub_server.DEFAULT_CONTAINER_HOME}/.cache/sccache",
                    ],
                    "default_env_vars": [],
                    "notes": "",
                },
                workspace,
            )

        self.assertEqual(recommendation["default_rw_mounts"], [])

    def test_normalize_auto_config_recommendation_drops_docker_socket_mounts(self) -> None:
        workspace = self.tmp_path / "workspace-drop-docker-socket"
        workspace.mkdir(parents=True, exist_ok=True)
        keep_host = self.tmp_path / "safe-cache"
        keep_host.mkdir(parents=True, exist_ok=True)
        fake_home = self.tmp_path / "fake-home-drop-docker-socket"
        fake_home.mkdir(parents=True, exist_ok=True)

        with patch("agent_hub.server.Path.home", return_value=fake_home):
            recommendation = self.state._normalize_auto_config_recommendation(
                {
                    "base_image_mode": "tag",
                    "base_image_value": "ubuntu:22.04",
                    "setup_script": "",
                    "default_ro_mounts": ["/tmp/nonexistent/docker.sock:/var/run/docker.sock"],
                    "default_rw_mounts": [
                        f"{keep_host}:{hub_server.DEFAULT_CONTAINER_HOME}/.cache/build",
                        "/run/user/1000/docker.sock:/tmp/agent-docker.sock",
                    ],
                    "default_env_vars": [],
                    "notes": "",
                },
                workspace,
            )

        self.assertEqual(recommendation["default_ro_mounts"], [])
        self.assertEqual(
            recommendation["default_rw_mounts"],
            [f"{keep_host}:{hub_server.DEFAULT_CONTAINER_HOME}/.cache/build"],
        )

    def test_normalize_auto_config_recommendation_ignores_cache_signals_in_test_paths(self) -> None:
        workspace = self.tmp_path / "workspace-test-cache-signals"
        cache_fixture = workspace / "tests" / "fixtures"
        cache_fixture.mkdir(parents=True, exist_ok=True)
        (cache_fixture / "CMakeLists.txt").write_text(
            "set(CMAKE_C_COMPILER_LAUNCHER ccache)\n",
            encoding="utf-8",
        )
        fake_home = self.tmp_path / "fake-home-test-cache-signals"
        fake_home.mkdir(parents=True, exist_ok=True)
        with patch("agent_hub.server.Path.home", return_value=fake_home):
            recommendation = self.state._normalize_auto_config_recommendation(
                {
                    "base_image_mode": "tag",
                    "base_image_value": "ubuntu:22.04",
                    "setup_script": "",
                    "default_ro_mounts": [],
                    "default_rw_mounts": [],
                    "default_env_vars": [],
                    "notes": "",
                },
                workspace,
            )

        self.assertEqual(recommendation["default_rw_mounts"], [])

    def test_normalize_auto_config_recommendation_replaces_cache_like_mounts_with_inferred_canonical_mounts(self) -> None:
        workspace = self.tmp_path / "workspace-cache-canonicalize"
        workspace.mkdir(parents=True, exist_ok=True)
        (workspace / "CMakeLists.txt").write_text(
            "set(CMAKE_CXX_COMPILER_LAUNCHER ccache)\n",
            encoding="utf-8",
        )
        legacy_cache_host = self.tmp_path / "legacy-cache-host"
        legacy_cache_host.mkdir(parents=True, exist_ok=True)
        fake_home = self.tmp_path / "fake-home-cache-canonicalize"
        fake_home.mkdir(parents=True, exist_ok=True)
        with patch("agent_hub.server.Path.home", return_value=fake_home):
            recommendation = self.state._normalize_auto_config_recommendation(
                {
                    "base_image_mode": "tag",
                    "base_image_value": "ubuntu:22.04",
                    "setup_script": "",
                    "default_ro_mounts": [],
                    "default_rw_mounts": [f"{legacy_cache_host}:/workspace/.scache"],
                    "default_env_vars": [],
                    "notes": "",
                },
                workspace,
            )

        expected_mount = f"{fake_home / '.ccache'}:{hub_server.DEFAULT_CONTAINER_HOME}/.ccache"
        self.assertEqual(recommendation["default_rw_mounts"], [expected_mount])

    def test_normalize_auto_config_recommendation_keeps_detected_ccache_mount_only(self) -> None:
        workspace = self.tmp_path / "workspace-ccache-only"
        workspace.mkdir(parents=True, exist_ok=True)
        (workspace / "CMakeLists.txt").write_text(
            "set(CMAKE_C_COMPILER_LAUNCHER ccache)\n",
            encoding="utf-8",
        )
        fake_home = self.tmp_path / "fake-home-ccache-only"
        ccache_host = fake_home / ".ccache"
        sccache_host = fake_home / ".cache" / "sccache"
        ccache_host.mkdir(parents=True, exist_ok=True)
        sccache_host.mkdir(parents=True, exist_ok=True)

        with patch("agent_hub.server.Path.home", return_value=fake_home):
            recommendation = self.state._normalize_auto_config_recommendation(
                {
                    "base_image_mode": "tag",
                    "base_image_value": "ubuntu:22.04",
                    "setup_script": "",
                    "default_ro_mounts": [],
                    "default_rw_mounts": [
                        f"{ccache_host}:{hub_server.DEFAULT_CONTAINER_HOME}/.ccache",
                        f"{sccache_host}:{hub_server.DEFAULT_CONTAINER_HOME}/.cache/sccache",
                    ],
                    "default_env_vars": [],
                    "notes": "",
                },
                workspace,
            )

        self.assertIn(
            f"{ccache_host}:{hub_server.DEFAULT_CONTAINER_HOME}/.ccache",
            recommendation["default_rw_mounts"],
        )
        self.assertNotIn(
            f"{sccache_host}:{hub_server.DEFAULT_CONTAINER_HOME}/.cache/sccache",
            recommendation["default_rw_mounts"],
        )

    def test_normalize_auto_config_recommendation_normalizes_repo_path_base(self) -> None:
        workspace = self.tmp_path / "workspace-base"
        docker_base = workspace / "docker" / "dev"
        docker_base.mkdir(parents=True, exist_ok=True)
        (docker_base / "Dockerfile").write_text("FROM ubuntu:22.04\n", encoding="utf-8")
        fake_home = self.tmp_path / "fake-home-base"
        fake_home.mkdir(parents=True, exist_ok=True)
        with patch("agent_hub.server.Path.home", return_value=fake_home):
            recommendation = self.state._normalize_auto_config_recommendation(
                {
                    "base_image_mode": "repo_path",
                    "base_image_value": str(docker_base),
                    "setup_script": "",
                    "default_ro_mounts": [],
                    "default_rw_mounts": [],
                    "default_env_vars": [],
                    "notes": "",
                },
                workspace,
            )

        self.assertEqual(recommendation["base_image_mode"], "repo_path")
        self.assertEqual(recommendation["base_image_value"], "docker/dev")

    def test_normalize_auto_config_recommendation_dedupes_setup_commands_from_repo_dockerfile(self) -> None:
        workspace = self.tmp_path / "workspace-setup-dedupe"
        docker_dir = workspace / "docker" / "development"
        docker_dir.mkdir(parents=True, exist_ok=True)
        (docker_dir / "Dockerfile").write_text(
            (
                "FROM ubuntu:22.04\n"
                "RUN uv sync --frozen --no-dev \\\n"
                " && cd /opt/workspace/web \\\n"
                " && corepack yarn install --frozen-lockfile \\\n"
                " && cd /opt/workspace/tools/demo \\\n"
                " && npm ci\n"
            ),
            encoding="utf-8",
        )

        recommendation = self.state._normalize_auto_config_recommendation(
            {
                "base_image_mode": "repo_path",
                "base_image_value": "docker/development/Dockerfile",
                "setup_script": (
                    "uv sync --frozen --no-dev\n"
                    "corepack yarn install --frozen-lockfile --cwd web\n"
                    "npm ci --prefix tools/demo\n"
                    "echo keep-me\n"
                ),
                "default_ro_mounts": [],
                "default_rw_mounts": [],
                "default_env_vars": [],
                "notes": "",
            },
            workspace,
        )

        self.assertEqual(recommendation["setup_script"], "echo keep-me")

    def test_run_temporary_auto_config_chat_requires_connected_account(self) -> None:
        workspace = self.tmp_path / "workspace-chat-auth"
        workspace.mkdir(parents=True, exist_ok=True)
        with patch("agent_hub.server._read_codex_auth", return_value=(False, "")):
            with self.assertRaises(HTTPException) as ctx:
                self.state._run_temporary_auto_config_chat(
                    workspace,
                    repo_url="https://example.com/org/repo.git",
                    branch="main",
                )
        self.assertEqual(ctx.exception.status_code, 409)

    def test_auto_configure_project_retries_clone_without_auth_env(self) -> None:
        attempted_clone_envs: list[dict[str, str] | None] = []

        def fake_run(
            cmd: list[str],
            cwd: Path | None = None,
            capture: bool = False,
            check: bool = True,
            env: dict[str, str] | None = None,
        ) -> subprocess.CompletedProcess:
            del cwd, capture, check
            if cmd[:2] == ["git", "clone"]:
                attempted_clone_envs.append(env)
                if env and env.get("BAD_AUTH"):
                    return subprocess.CompletedProcess(cmd, 1, "", "fatal: HTTP 403")
                return subprocess.CompletedProcess(cmd, 0, "ok", "")
            return subprocess.CompletedProcess(cmd, 0, "", "")

        with patch.object(
            hub_server.HubState,
            "_github_git_env_for_repo",
            return_value={"BAD_AUTH": "1"},
        ), patch(
            "agent_hub.server._detect_default_branch",
            return_value="main",
        ), patch(
            "agent_hub.server._run",
            side_effect=fake_run,
        ), patch.object(
            hub_server.HubState,
            "_run_temporary_auto_config_chat",
            return_value={
                "payload": {
                    "base_image_mode": "tag",
                    "base_image_value": "ubuntu:22.04",
                    "setup_script": "",
                    "default_ro_mounts": [],
                    "default_rw_mounts": [],
                    "default_env_vars": [],
                    "notes": "",
                },
                "model": "chatgpt-account-codex",
            },
        ), patch.object(
            self.state,
            "_attempt_auto_config_recommendation_build",
            return_value={"ok": True, "snapshot_tag": "agent-hub-setup-autocfg"},
        ):
            recommendation = self.state.auto_configure_project(
                repo_url="https://example.com/org/repo.git",
                default_branch="",
            )

        self.assertGreaterEqual(len(attempted_clone_envs), 2)
        self.assertIsNotNone(attempted_clone_envs[0])
        assert attempted_clone_envs[0] is not None
        assert attempted_clone_envs[1] is not None
        self.assertEqual(attempted_clone_envs[0].get("BAD_AUTH"), "1")
        self.assertEqual(attempted_clone_envs[1].get("GIT_CONFIG_COUNT"), "0")
        self.assertNotIn("BAD_AUTH", attempted_clone_envs[1])
        self.assertEqual(recommendation["base_image_mode"], "tag")
        self.assertEqual(recommendation["analysis_model"], "chatgpt-account-codex")

    def test_auto_configure_project_emits_live_logs_for_request_id(self) -> None:
        emitted_logs: list[tuple[str, str, bool]] = []

        def fake_run(
            cmd: list[str],
            cwd: Path | None = None,
            capture: bool = False,
            check: bool = True,
            env: dict[str, str] | None = None,
        ) -> subprocess.CompletedProcess:
            del cwd, capture, check, env
            if cmd[:2] == ["git", "clone"]:
                Path(cmd[-1]).mkdir(parents=True, exist_ok=True)
                return subprocess.CompletedProcess(cmd, 0, "Cloning into 'repo'...\n", "")
            return subprocess.CompletedProcess(cmd, 0, "", "")

        def fake_temporary_chat(
            workspace: Path,
            repo_url: str,
            branch: str,
            on_output: Callable[[str], None] | None = None,
            retry_feedback: str = "",
        ) -> dict[str, Any]:
            self.assertTrue(workspace.exists())
            self.assertEqual(repo_url, "https://example.com/org/repo.git")
            self.assertEqual(branch, "main")
            self.assertEqual(retry_feedback, "")
            if on_output is not None:
                on_output("assistant> analyzing repository layout...\n")
            return {
                "payload": {
                    "base_image_mode": "tag",
                    "base_image_value": "ubuntu:22.04",
                    "setup_script": "",
                    "default_ro_mounts": [],
                    "default_rw_mounts": [],
                    "default_env_vars": [],
                    "notes": "",
                },
                "model": "chatgpt-account-codex",
            }

        def capture_live_log(request_id: str, text: str, replace: bool = False) -> None:
            emitted_logs.append((request_id, text, replace))

        with patch("agent_hub.server._detect_default_branch", return_value="main"), patch(
            "agent_hub.server._run",
            side_effect=fake_run,
        ), patch.object(
            self.state,
            "_run_temporary_auto_config_chat",
            side_effect=fake_temporary_chat,
        ), patch.object(
            self.state,
            "_attempt_auto_config_recommendation_build",
            return_value={"ok": True, "snapshot_tag": "agent-hub-setup-autocfg"},
        ), patch.object(
            self.state,
            "_emit_auto_config_log",
            side_effect=capture_live_log,
        ):
            recommendation = self.state.auto_configure_project(
                repo_url="https://example.com/org/repo.git",
                default_branch="",
                request_id="pending-auto-123",
            )

        self.assertEqual(recommendation["default_branch"], "main")
        self.assertEqual(recommendation["analysis_model"], "chatgpt-account-codex")
        self.assertTrue(emitted_logs)
        self.assertTrue(all(request_id == "pending-auto-123" for request_id, _text, _replace in emitted_logs))
        self.assertTrue(any(replace for _request_id, _text, replace in emitted_logs))
        self.assertTrue(
            any(
                "assistant> analyzing repository layout..." in text
                for _request_id, text, _replace in emitted_logs
            )
        )

    def test_auto_configure_project_retries_build_failures_and_applies_feedback(self) -> None:
        retry_feedbacks: list[str] = []

        def fake_run(
            cmd: list[str],
            cwd: Path | None = None,
            capture: bool = False,
            check: bool = True,
            env: dict[str, str] | None = None,
        ) -> subprocess.CompletedProcess:
            del cwd, capture, check, env
            if cmd[:2] == ["git", "clone"]:
                Path(cmd[-1]).mkdir(parents=True, exist_ok=True)
                return subprocess.CompletedProcess(cmd, 0, "Cloning into 'repo'...\n", "")
            return subprocess.CompletedProcess(cmd, 0, "", "")

        def fake_temporary_chat(
            workspace: Path,
            repo_url: str,
            branch: str,
            on_output: Callable[[str], None] | None = None,
            retry_feedback: str = "",
        ) -> dict[str, Any]:
            self.assertTrue(workspace.exists())
            self.assertEqual(repo_url, "https://example.com/org/repo.git")
            self.assertEqual(branch, "main")
            retry_feedbacks.append(retry_feedback)
            if on_output is not None:
                on_output("assistant> generated recommendation\n")
            if len(retry_feedbacks) == 1:
                return {
                    "payload": {
                        "base_image_mode": "tag",
                        "base_image_value": "ubuntu:22.04",
                        "setup_script": "apt-get install -y build-essential",
                        "default_ro_mounts": [],
                        "default_rw_mounts": [],
                        "default_env_vars": [],
                        "notes": "",
                    },
                    "model": "chatgpt-account-codex",
                }
            return {
                "payload": {
                    "base_image_mode": "tag",
                    "base_image_value": "ubuntu:24.04",
                    "setup_script": "apt-get update\napt-get install -y build-essential",
                    "default_ro_mounts": [],
                    "default_rw_mounts": [],
                    "default_env_vars": [],
                    "notes": "",
                },
                "model": "chatgpt-account-codex",
            }

        with patch("agent_hub.server._detect_default_branch", return_value="main"), patch(
            "agent_hub.server._run",
            side_effect=fake_run,
        ), patch.object(
            self.state,
            "_run_temporary_auto_config_chat",
            side_effect=fake_temporary_chat,
        ) as temporary_chat, patch.object(
            self.state,
            "_attempt_auto_config_recommendation_build",
            side_effect=[
                {
                    "ok": False,
                    "summary": "apt-get update was missing before apt-get install.",
                    "failing_command": "uv run --project /workspace/agent_hub agent_cli ...",
                    "build_log_excerpt": "$ uv run --project /workspace/agent_hub agent_cli ...\nE: Unable to locate package build-essential",
                },
                {
                    "ok": True,
                    "snapshot_tag": "agent-hub-setup-autocfg",
                },
            ],
        ) as build_attempt:
            recommendation = self.state.auto_configure_project(
                repo_url="https://example.com/org/repo.git",
                default_branch="",
            )

        self.assertEqual(temporary_chat.call_count, 2)
        self.assertEqual(build_attempt.call_count, 2)
        self.assertEqual(retry_feedbacks[0], "")
        self.assertIn("Attempt 1 of 5 failed.", retry_feedbacks[1])
        self.assertIn("Build log excerpt:", retry_feedbacks[1])
        self.assertEqual(recommendation["base_image_value"], "ubuntu:24.04")
        self.assertEqual(
            recommendation["setup_script"],
            "apt-get update\napt-get install -y build-essential",
        )
        self.assertEqual(recommendation["analysis_model"], "chatgpt-account-codex")

    def test_auto_configure_project_reports_summary_and_build_log_after_max_retries(self) -> None:
        def fake_run(
            cmd: list[str],
            cwd: Path | None = None,
            capture: bool = False,
            check: bool = True,
            env: dict[str, str] | None = None,
        ) -> subprocess.CompletedProcess:
            del cwd, capture, check, env
            if cmd[:2] == ["git", "clone"]:
                Path(cmd[-1]).mkdir(parents=True, exist_ok=True)
                return subprocess.CompletedProcess(cmd, 0, "Cloning into 'repo'...\n", "")
            return subprocess.CompletedProcess(cmd, 0, "", "")

        with patch("agent_hub.server._detect_default_branch", return_value="main"), patch(
            "agent_hub.server._run",
            side_effect=fake_run,
        ), patch.object(
            self.state,
            "_run_temporary_auto_config_chat",
            return_value={
                "payload": {
                    "base_image_mode": "tag",
                    "base_image_value": "ubuntu:22.04",
                    "setup_script": "",
                    "default_ro_mounts": [],
                    "default_rw_mounts": [],
                    "default_env_vars": [],
                    "notes": "",
                },
                "model": "chatgpt-account-codex",
            },
        ) as temporary_chat, patch.object(
            self.state,
            "_attempt_auto_config_recommendation_build",
            return_value={
                "ok": False,
                "summary": "docker build failed: missing compiler toolchain.",
                "failing_command": "uv run --project /workspace/agent_hub agent_cli ...",
                "build_log_excerpt": "$ uv run --project /workspace/agent_hub agent_cli ...\nERROR: gcc: command not found",
            },
        ) as build_attempt:
            with self.assertRaises(HTTPException) as ctx:
                self.state.auto_configure_project(
                    repo_url="https://example.com/org/repo.git",
                    default_branch="",
                )

        self.assertEqual(ctx.exception.status_code, 422)
        detail = str(ctx.exception.detail)
        self.assertIn("could not produce a buildable setup after 5 attempts", detail)
        self.assertIn("Issue summary: docker build failed: missing compiler toolchain.", detail)
        self.assertIn("Failing command: uv run --project /workspace/agent_hub agent_cli ...", detail)
        self.assertIn("Build log:", detail)
        self.assertIn("ERROR: gcc: command not found", detail)
        self.assertEqual(temporary_chat.call_count, hub_server.AUTO_CONFIG_BUILD_MAX_ATTEMPTS)
        self.assertEqual(build_attempt.call_count, hub_server.AUTO_CONFIG_BUILD_MAX_ATTEMPTS)

    def test_apply_auto_config_repository_hints_prefers_ci_dockerfile_and_make_target(self) -> None:
        workspace = self.tmp_path / "workspace-hints"
        (workspace / "ci" / "x86_docker").mkdir(parents=True, exist_ok=True)
        (workspace / "docker").mkdir(parents=True, exist_ok=True)
        (workspace / ".github" / "workflows").mkdir(parents=True, exist_ok=True)
        (workspace / "ci" / "x86_docker" / "Dockerfile").write_text(
            "FROM ubuntu:22.04\n",
            encoding="utf-8",
        )
        (workspace / "docker" / "Dockerfile").write_text(
            "FROM ubuntu:20.04\n",
            encoding="utf-8",
        )
        (workspace / "make.sh").write_text(
            "#!/usr/bin/env bash\nset -e\n",
            encoding="utf-8",
        )
        (workspace / ".github" / "workflows" / "build.yml").write_text(
            "steps:\\n  - run: bash make.sh rbufc\\n",
            encoding="utf-8",
        )

        recommendation = self.state._apply_auto_config_repository_hints(
            {
                "base_image_mode": "tag",
                "base_image_value": "ubuntu:22.04",
                "setup_script": "echo bootstrap",
                "default_ro_mounts": [],
                "default_rw_mounts": [],
                "default_env_vars": [],
                "notes": "",
            },
            workspace,
        )

        self.assertEqual(recommendation["base_image_mode"], "repo_path")
        self.assertEqual(recommendation["base_image_value"], "ci/x86_docker/Dockerfile")
        self.assertEqual(recommendation["setup_script"], "bash make.sh rbufc")
        self.assertIn("selected repository Dockerfile: ci/x86_docker/Dockerfile", recommendation["notes"])

    def test_apply_auto_config_repository_hints_prefers_repo_dockerfile_for_high_confidence_path(self) -> None:
        workspace = self.tmp_path / "workspace-hints-docker"
        (workspace / "docker" / "development").mkdir(parents=True, exist_ok=True)
        (workspace / "docker" / "development" / "Dockerfile").write_text(
            "FROM ubuntu:22.04\n",
            encoding="utf-8",
        )

        recommendation = self.state._apply_auto_config_repository_hints(
            {
                "base_image_mode": "tag",
                "base_image_value": "ubuntu:22.04",
                "setup_script": "",
                "default_ro_mounts": [],
                "default_rw_mounts": [],
                "default_env_vars": [],
                "notes": "",
            },
            workspace,
        )

        self.assertEqual(recommendation["base_image_mode"], "repo_path")
        self.assertEqual(recommendation["base_image_value"], "docker/development/Dockerfile")

    def test_state_payload_does_not_call_openai_title_generation_from_log_changes(self) -> None:
        project = self.state.add_project(
            repo_url="https://example.com/org/repo.git",
            default_branch="main",
        )
        chat = self.state.create_chat(
            project["id"],
            profile="",
            ro_mounts=[],
            rw_mounts=[],
            env_vars=[],
            agent_args=[],
        )
        self.state.chat_log(chat["id"]).write_text(
            "> refine the Dockerfile caching strategy\nassistant output keeps changing...\n",
            encoding="utf-8",
        )

        with patch("agent_hub.server._openai_generate_chat_title") as generate_title:
            payload = self.state.state_payload()

        self.assertEqual(generate_title.call_count, 0)
        chat_payload = next(item for item in payload["chats"] if item["id"] == chat["id"])
        self.assertEqual(chat_payload["display_name"], "New Chat")

    def test_state_payload_reschedules_pending_chat_title_generation(self) -> None:
        project = self.state.add_project(
            repo_url="https://example.com/org/repo.git",
            default_branch="main",
        )
        chat = self.state.create_chat(
            project["id"],
            profile="",
            ro_mounts=[],
            rw_mounts=[],
            env_vars=[],
            agent_args=[],
        )
        state_data = self.state.load()
        state_data["chats"][chat["id"]]["title_user_prompts"] = ["triage flaky websocket reconnect test"]
        state_data["chats"][chat["id"]]["title_status"] = "pending"
        self.state.save(state_data)

        with patch("agent_hub.server._is_process_running", return_value=False), patch.object(
            hub_server.HubState, "_schedule_chat_title_generation"
        ) as schedule_title:
            self.state.state_payload()

        schedule_title.assert_called_once_with(chat["id"])

    def test_state_payload_discards_cached_terminal_control_title(self) -> None:
        project = self.state.add_project(
            repo_url="https://example.com/org/repo.git",
            default_branch="main",
        )
        chat = self.state.create_chat(
            project["id"],
            profile="",
            ro_mounts=[],
            rw_mounts=[],
            env_vars=[],
            agent_args=[],
        )
        state_data = self.state.load()
        state_data["chats"][chat["id"]]["title_cached"] = "]10;rgb:e7e7/eded/f7f7\\"
        state_data["chats"][chat["id"]]["title_user_prompts"] = ["implement auth retry logic"]
        self.state.save(state_data)

        payload = self.state.state_payload()
        chat_payload = next(item for item in payload["chats"] if item["id"] == chat["id"])
        self.assertEqual(chat_payload["display_name"], "New Chat")

    def test_state_payload_rewrites_legacy_generated_chat_name(self) -> None:
        project = self.state.add_project(
            repo_url="https://example.com/org/repo.git",
            default_branch="main",
        )
        chat = self.state.create_chat(
            project["id"],
            profile="",
            ro_mounts=[],
            rw_mounts=[],
            env_vars=[],
            agent_args=[],
        )
        state_data = self.state.load()
        state_data["chats"][chat["id"]]["name"] = "chat-deadbeef"
        self.state.save(state_data)

        payload = self.state.state_payload()
        chat_payload = next(item for item in payload["chats"] if item["id"] == chat["id"])
        self.assertEqual(chat_payload["display_name"], "New Chat")

    def test_shutdown_stops_running_chats_and_persists_state(self) -> None:
        project = self.state.add_project(
            repo_url="https://example.com/org/repo.git",
            default_branch="main",
        )
        running_chat = self.state.create_chat(
            project["id"],
            profile="",
            ro_mounts=[],
            rw_mounts=[],
            env_vars=[],
            agent_args=[],
        )
        stopped_chat = self.state.create_chat(
            project["id"],
            profile="",
            ro_mounts=[],
            rw_mounts=[],
            env_vars=[],
            agent_args=[],
        )

        state_data = self.state.load()
        state_data["chats"][running_chat["id"]]["status"] = "running"
        state_data["chats"][running_chat["id"]]["pid"] = 5001
        state_data["chats"][stopped_chat["id"]]["status"] = "stopped"
        state_data["chats"][stopped_chat["id"]]["pid"] = None
        self.state.save(state_data)

        with patch.object(hub_server.HubState, "_close_runtime"), patch(
            "agent_hub.server._is_process_running",
            side_effect=lambda pid: pid == 5001,
        ), patch(
            "agent_hub.server._stop_processes",
            return_value=1,
        ) as stop_many:
            summary = self.state.shutdown()

        self.assertEqual(summary["stopped_chats"], 1)
        self.assertEqual(summary["closed_chats"], 1)
        stop_many.assert_called_once_with([5001], timeout_seconds=4.0)

        post = self.state.load()
        self.assertNotIn(running_chat["id"], post["chats"])
        self.assertIn(stopped_chat["id"], post["chats"])


class HubArtifactCommandTests(unittest.TestCase):
    def _make_fake_curl(self, fake_bin: Path) -> Path:
        fake_bin.mkdir(parents=True, exist_ok=True)
        curl_script = fake_bin / "curl"
        curl_script.write_text(
            """#!/usr/bin/env bash
set -euo pipefail

payload=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --data)
      payload="$2"
      shift 2
      ;;
    *)
      shift
      ;;
  esac
done

if [[ -z "${payload}" ]]; then
  echo "missing --data payload" >&2
  exit 2
fi

printf '%s\\n' "${payload}" >> "${HUB_ARTIFACT_CURL_LOG:?}"

python3 - "${payload}" <<'PY'
import json
import os
import sys
from pathlib import Path

body = json.loads(sys.argv[1])
path = str(body.get("path") or "")
fail_once_path = str(os.environ.get("HUB_ARTIFACT_FAIL_ONCE_PATH") or "")
fail_once_marker = str(os.environ.get("HUB_ARTIFACT_FAIL_ONCE_MARKER") or "")
always_fail_path = str(os.environ.get("HUB_ARTIFACT_ALWAYS_FAIL_PATH") or "")

if always_fail_path and path == always_fail_path:
    print(f"simulated upload failure for {path}", file=sys.stderr)
    sys.exit(75)

if fail_once_path and path == fail_once_path and fail_once_marker:
    marker = Path(fail_once_marker)
    if not marker.exists():
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text("failed-once", encoding="utf-8")
        print(f"simulated transient upload failure for {path}", file=sys.stderr)
        sys.exit(75)

name = str(body.get("name") or Path(path).name or "artifact")
print(json.dumps({
    "artifact": {
        "name": name,
        "relative_path": path,
        "download_url": f"/download/{name}",
    }
}))
PY
""",
            encoding="utf-8",
        )
        curl_script.chmod(0o755)
        return curl_script

    def _run_publish(self, *args: str, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", str(ROOT / "docker" / "agent_cli" / "hub_artifact"), "publish", *args],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_hub_artifact_publish_accepts_file_list(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            file_one = tmp_path / "a.txt"
            file_two = tmp_path / "b.txt"
            file_one.write_text("a", encoding="utf-8")
            file_two.write_text("b", encoding="utf-8")

            fake_bin = tmp_path / "fake-bin"
            self._make_fake_curl(fake_bin)
            curl_log = tmp_path / "curl.log"

            env = os.environ.copy()
            env["PATH"] = f"{fake_bin}:{env.get('PATH', '')}"
            env["HUB_ARTIFACT_CURL_LOG"] = str(curl_log)
            env["AGENT_HUB_ARTIFACTS_URL"] = "http://example.invalid/publish"
            env["AGENT_HUB_ARTIFACT_TOKEN"] = "token-test"

            result = self._run_publish(str(file_one), str(file_two), env=env)
            self.assertEqual(result.returncode, 0, msg=result.stderr or result.stdout)
            self.assertIn("Artifact published: a.txt", result.stdout)
            self.assertIn("Artifact published: b.txt", result.stdout)
            self.assertIn("Published 2 artifacts.", result.stdout)

            payloads = [json.loads(line) for line in curl_log.read_text(encoding="utf-8").splitlines() if line.strip()]
            self.assertEqual(len(payloads), 2)
            self.assertEqual(payloads[0]["path"], str(file_one))
            self.assertEqual(payloads[1]["path"], str(file_two))
            self.assertNotIn("name", payloads[0])
            self.assertNotIn("name", payloads[1])

    def test_hub_artifact_publish_accepts_directory_and_rejects_subdirectories(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            fake_bin = tmp_path / "fake-bin"
            self._make_fake_curl(fake_bin)

            env = os.environ.copy()
            env["PATH"] = f"{fake_bin}:{env.get('PATH', '')}"
            env["AGENT_HUB_ARTIFACTS_URL"] = "http://example.invalid/publish"
            env["AGENT_HUB_ARTIFACT_TOKEN"] = "token-test"

            flat_dir = tmp_path / "flat"
            flat_dir.mkdir(parents=True, exist_ok=True)
            (flat_dir / "z.txt").write_text("z", encoding="utf-8")
            (flat_dir / "a.txt").write_text("a", encoding="utf-8")
            flat_log = tmp_path / "flat.log"
            env["HUB_ARTIFACT_CURL_LOG"] = str(flat_log)

            ok_result = self._run_publish(str(flat_dir), env=env)
            self.assertEqual(ok_result.returncode, 0, msg=ok_result.stderr or ok_result.stdout)
            payloads = [json.loads(line) for line in flat_log.read_text(encoding="utf-8").splitlines() if line.strip()]
            self.assertEqual(len(payloads), 2)
            self.assertEqual(payloads[0]["path"], str(flat_dir / "a.txt"))
            self.assertEqual(payloads[1]["path"], str(flat_dir / "z.txt"))

            nested_dir = tmp_path / "nested"
            nested_dir.mkdir(parents=True, exist_ok=True)
            (nested_dir / "keep.txt").write_text("k", encoding="utf-8")
            (nested_dir / "child").mkdir(parents=True, exist_ok=True)
            nested_log = tmp_path / "nested.log"
            env["HUB_ARTIFACT_CURL_LOG"] = str(nested_log)

            fail_result = self._run_publish(str(nested_dir), env=env)
            self.assertNotEqual(fail_result.returncode, 0)
            self.assertIn("Subdirectories are not supported for artifact publish", fail_result.stderr)
            self.assertFalse(nested_log.exists())

    def test_hub_artifact_publish_rejects_name_for_multiple_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            file_one = tmp_path / "a.txt"
            file_two = tmp_path / "b.txt"
            file_one.write_text("a", encoding="utf-8")
            file_two.write_text("b", encoding="utf-8")
            fake_bin = tmp_path / "fake-bin"
            self._make_fake_curl(fake_bin)

            env = os.environ.copy()
            env["PATH"] = f"{fake_bin}:{env.get('PATH', '')}"
            env["HUB_ARTIFACT_CURL_LOG"] = str(tmp_path / "curl.log")
            env["AGENT_HUB_ARTIFACTS_URL"] = "http://example.invalid/publish"
            env["AGENT_HUB_ARTIFACT_TOKEN"] = "token-test"

            result = self._run_publish(str(file_one), str(file_two), "--name", "Combined", env=env)
            self.assertEqual(result.returncode, 2)
            self.assertIn("--name can only be used when publishing exactly one file.", result.stderr)

    def test_hub_artifact_publish_accepts_archive_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            archive_file = tmp_path / "bundle.zip"
            archive_file.write_text("zip payload", encoding="utf-8")
            fake_bin = tmp_path / "fake-bin"
            self._make_fake_curl(fake_bin)

            env = os.environ.copy()
            env["PATH"] = f"{fake_bin}:{env.get('PATH', '')}"
            env["HUB_ARTIFACT_CURL_LOG"] = str(tmp_path / "curl.log")
            env["AGENT_HUB_ARTIFACTS_URL"] = "http://example.invalid/publish"
            env["AGENT_HUB_ARTIFACT_TOKEN"] = "token-test"

            result = self._run_publish(str(archive_file), env=env)
            self.assertEqual(result.returncode, 0, msg=result.stderr or result.stdout)
            self.assertIn("Artifact published: bundle.zip", result.stdout)
            payloads = [json.loads(line) for line in Path(env["HUB_ARTIFACT_CURL_LOG"]).read_text(encoding="utf-8").splitlines() if line.strip()]
            self.assertEqual(len(payloads), 1)
            self.assertEqual(payloads[0]["path"], str(archive_file))

    def test_hub_artifact_publish_retries_only_failed_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            file_one = tmp_path / "a.txt"
            file_two = tmp_path / "b.txt"
            file_one.write_text("a", encoding="utf-8")
            file_two.write_text("b", encoding="utf-8")
            fake_bin = tmp_path / "fake-bin"
            self._make_fake_curl(fake_bin)
            curl_log = tmp_path / "curl.log"

            env = os.environ.copy()
            env["PATH"] = f"{fake_bin}:{env.get('PATH', '')}"
            env["HUB_ARTIFACT_CURL_LOG"] = str(curl_log)
            env["AGENT_HUB_ARTIFACTS_URL"] = "http://example.invalid/publish"
            env["AGENT_HUB_ARTIFACT_TOKEN"] = "token-test"
            env["HUB_ARTIFACT_MAX_ATTEMPTS"] = "2"
            env["HUB_ARTIFACT_RETRY_DELAY_BASE_SEC"] = "0"
            env["HUB_ARTIFACT_FAIL_ONCE_PATH"] = str(file_two)
            env["HUB_ARTIFACT_FAIL_ONCE_MARKER"] = str(tmp_path / "failed-once.marker")

            result = self._run_publish(str(file_one), str(file_two), env=env)
            self.assertEqual(result.returncode, 0, msg=result.stderr or result.stdout)
            self.assertIn(f"Retrying artifact publish (2/2): {file_two}", result.stderr)

            payloads = [json.loads(line) for line in curl_log.read_text(encoding="utf-8").splitlines() if line.strip()]
            uploaded_paths = [payload["path"] for payload in payloads]
            self.assertEqual(uploaded_paths.count(str(file_one)), 1)
            self.assertEqual(uploaded_paths.count(str(file_two)), 2)
            self.assertEqual(uploaded_paths[0], str(file_one))

    def test_hub_artifact_publish_reports_failed_paths_after_retries(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            file_one = tmp_path / "a.txt"
            file_two = tmp_path / "b.txt"
            file_one.write_text("a", encoding="utf-8")
            file_two.write_text("b", encoding="utf-8")
            fake_bin = tmp_path / "fake-bin"
            self._make_fake_curl(fake_bin)
            curl_log = tmp_path / "curl.log"

            env = os.environ.copy()
            env["PATH"] = f"{fake_bin}:{env.get('PATH', '')}"
            env["HUB_ARTIFACT_CURL_LOG"] = str(curl_log)
            env["AGENT_HUB_ARTIFACTS_URL"] = "http://example.invalid/publish"
            env["AGENT_HUB_ARTIFACT_TOKEN"] = "token-test"
            env["HUB_ARTIFACT_MAX_ATTEMPTS"] = "2"
            env["HUB_ARTIFACT_RETRY_DELAY_BASE_SEC"] = "0"
            env["HUB_ARTIFACT_ALWAYS_FAIL_PATH"] = str(file_two)

            result = self._run_publish(str(file_one), str(file_two), env=env)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn(f"Artifact publish failed after 2 attempt(s): {file_two}", result.stderr)
            self.assertIn("Failed to publish 1 artifact(s):", result.stderr)
            self.assertIn(f"  - {file_two}", result.stderr)
            self.assertIn("Artifact published: a.txt", result.stdout)

            payloads = [json.loads(line) for line in curl_log.read_text(encoding="utf-8").splitlines() if line.strip()]
            uploaded_paths = [payload["path"] for payload in payloads]
            self.assertEqual(uploaded_paths.count(str(file_one)), 1)
            self.assertEqual(uploaded_paths.count(str(file_two)), 2)

    def test_hub_artifact_publish_handles_large_file_batch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            files = []
            for index in range(200):
                path = tmp_path / f"file-{index:03d}.txt"
                path.write_text(f"payload-{index}\n", encoding="utf-8")
                files.append(path)

            fake_bin = tmp_path / "fake-bin"
            self._make_fake_curl(fake_bin)
            curl_log = tmp_path / "curl.log"

            env = os.environ.copy()
            env["PATH"] = f"{fake_bin}:{env.get('PATH', '')}"
            env["HUB_ARTIFACT_CURL_LOG"] = str(curl_log)
            env["AGENT_HUB_ARTIFACTS_URL"] = "http://example.invalid/publish"
            env["AGENT_HUB_ARTIFACT_TOKEN"] = "token-test"
            env["HUB_ARTIFACT_PROGRESS_EVERY"] = "50"
            env["HUB_ARTIFACT_RETRY_DELAY_BASE_SEC"] = "0"
            env["HUB_ARTIFACT_MAX_ATTEMPTS"] = "3"
            env["HUB_ARTIFACT_FAIL_ONCE_PATH"] = str(files[137])
            env["HUB_ARTIFACT_FAIL_ONCE_MARKER"] = str(tmp_path / "failed-once.marker")

            result = self._run_publish(*[str(path) for path in files], env=env)
            self.assertEqual(result.returncode, 0, msg=result.stderr or result.stdout)
            self.assertIn("Published 200 artifacts.", result.stdout)
            self.assertIn("Artifact upload progress: 200/200 processed;", result.stderr)

            payloads = [json.loads(line) for line in curl_log.read_text(encoding="utf-8").splitlines() if line.strip()]
            uploaded_paths = [payload["path"] for payload in payloads]
            self.assertEqual(uploaded_paths.count(str(files[137])), 2)
            self.assertEqual(len(payloads), 201)
            self.assertEqual(uploaded_paths.count(str(files[0])), 1)
            self.assertEqual(uploaded_paths.count(str(files[-1])), 1)


class CliEnvVarTests(unittest.TestCase):
    def test_agent_cli_default_base_image_uses_ubuntu_24_04(self) -> None:
        content = AGENT_CLI_DOCKERFILE.read_text(encoding="utf-8")

        self.assertEqual(image_cli.DEFAULT_BASE_IMAGE, "ubuntu:24.04")
        self.assertIn("ARG BASE_IMAGE=ubuntu:24.04", content)

    def test_agent_cli_dockerfile_sets_root_user_before_apt_layers(self) -> None:
        content = AGENT_CLI_DOCKERFILE.read_text(encoding="utf-8")

        self.assertIn("USER root", content)
        self.assertLess(content.index("USER root"), content.index("RUN apt-get update"))

    def test_agent_cli_dockerfile_sets_root_home_before_provider_install_layers(self) -> None:
        content = AGENT_CLI_DOCKERFILE.read_text(encoding="utf-8")

        self.assertIn("ENV HOME=/root", content)
        self.assertLess(
            content.index("ENV HOME=/root"),
            content.index("RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash -"),
        )

    def test_agent_cli_dockerfile_precreates_workspace_config_dir_for_runtime_mounts(self) -> None:
        content = AGENT_CLI_DOCKERFILE.read_text(encoding="utf-8")

        self.assertIn("/workspace/.config", content)
        self.assertLess(
            content.index("/workspace/.config"),
            content.index("chmod -R 0777 /workspace"),
        )

    def test_agent_hub_dockerfile_uses_build_only_uv_project_environment(self) -> None:
        content = AGENT_HUB_DOCKERFILE.read_text(encoding="utf-8")
        exported_lines = [line.strip() for line in content.splitlines() if line.strip().startswith("UV_PROJECT_ENVIRONMENT=")]

        self.assertEqual(exported_lines, [])
        self.assertIn("UV_PROJECT_ENVIRONMENT=/opt/agent_hub/.venv uv sync --frozen --no-dev", content)

    def test_development_dockerfile_uses_build_only_uv_project_environment(self) -> None:
        content = DEVELOPMENT_DOCKERFILE.read_text(encoding="utf-8")
        exported_lines = [line.strip() for line in content.splitlines() if line.strip().startswith("UV_PROJECT_ENVIRONMENT=")]

        self.assertEqual(exported_lines, [])
        self.assertIn("UV_PROJECT_ENVIRONMENT=/opt/agent_hub/.venv uv sync --frozen --no-dev", content)

    def test_development_dockerfile_runs_demo_tooling_verification_script(self) -> None:
        content = DEVELOPMENT_DOCKERFILE.read_text(encoding="utf-8")
        verify_call = "&& /opt/agent_hub/docker/development/verify-demo-tooling.sh"

        self.assertIn("/opt/agent_hub/docker/development/verify-demo-tooling.sh", content)
        self.assertIn(verify_call, content)
        self.assertLess(
            content.index("npx playwright install --with-deps firefox"),
            content.index(verify_call),
        )

    def test_development_verify_script_checks_recording_and_playwright_stack(self) -> None:
        self.assertTrue(DEVELOPMENT_VERIFY_SCRIPT.is_file())
        content = DEVELOPMENT_VERIFY_SCRIPT.read_text(encoding="utf-8")

        self.assertIn("required_commands=(", content)
        self.assertIn("ffmpeg", content)
        self.assertIn("xdotool", content)
        self.assertIn("xvfb-run", content)
        self.assertIn("import { firefox } from \"playwright\";", content)
        self.assertIn("headless: true", content)
        self.assertIn("headless: false", content)
        self.assertIn("ffmpeg -hide_banner -loglevel error -y", content)

    def test_ensure_claude_json_file_initializes_missing_file_with_valid_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            claude_json_file = Path(tmp) / ".claude.json"

            image_cli._ensure_claude_json_file(claude_json_file)

            self.assertTrue(claude_json_file.is_file())
            self.assertEqual(json.loads(claude_json_file.read_text(encoding="utf-8")), {})

    def test_ensure_claude_json_file_rewrites_empty_file_with_valid_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            claude_json_file = Path(tmp) / ".claude.json"
            claude_json_file.write_text("", encoding="utf-8")

            image_cli._ensure_claude_json_file(claude_json_file)

            self.assertEqual(json.loads(claude_json_file.read_text(encoding="utf-8")), {})

    def test_ensure_claude_json_file_preserves_existing_non_empty_content(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            claude_json_file = Path(tmp) / ".claude.json"
            claude_json_file.write_text('{"existing":"value"}\n', encoding="utf-8")

            image_cli._ensure_claude_json_file(claude_json_file)

            self.assertEqual(claude_json_file.read_text(encoding="utf-8"), '{"existing":"value"}\n')

    def test_ensure_claude_json_file_fails_when_path_is_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            claude_json_dir = Path(tmp) / ".claude.json"
            claude_json_dir.mkdir(parents=True, exist_ok=True)

            with self.assertRaises(ClickException) as ctx:
                image_cli._ensure_claude_json_file(claude_json_dir)

            self.assertIn("not a file", str(ctx.exception))

    def test_default_system_prompt_file_contains_core_file_artifact_instructions(self) -> None:
        config_path = ROOT / "config" / "agent.config.toml"
        system_prompt_path = ROOT / "SYSTEM_PROMPT.md"
        content = config_path.read_text(encoding="utf-8")
        prompt_content = system_prompt_path.read_text(encoding="utf-8")

        self.assertNotIn("\ndeveloper_instructions = \"\"\"\n", content)
        self.assertNotIn("\ninstructions = \"\"\"\n", content)
        self.assertIn("SYSTEM_PROMPT.md", content)
        self.assertIn("hub_artifact publish <path> [<path> ...]", prompt_content)
        self.assertIn("If the user asks for a file", prompt_content)
        self.assertIn(
            "Do not introduce fallback implementation paths unless the user explicitly requests fallback behavior",
            prompt_content,
        )
        self.assertIn("When a requested implementation fails, fail fast with a hard error.", prompt_content)
        self.assertIn("Do not swallow, mask, or ignore errors with permissive operators", prompt_content)

    def test_parse_env_var_valid(self) -> None:
        self.assertEqual(image_cli._parse_env_var("FOO=bar", "--env-var"), "FOO=bar")
        self.assertEqual(image_cli._parse_env_var("EMPTY=", "--env-var"), "EMPTY=")

    def test_parse_env_var_invalid(self) -> None:
        with self.assertRaises(Exception):
            image_cli._parse_env_var("NO_EQUALS", "--env-var")

    def test_snapshot_commit_resets_entrypoint_and_cmd(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            project = tmp_path / "project"
            project.mkdir(parents=True, exist_ok=True)
            config = tmp_path / "agent.config.toml"
            config.write_text("model = 'test'\n", encoding="utf-8")

            commands: list[list[str]] = []

            def fake_run(cmd: list[str], cwd: Path | None = None) -> None:
                del cwd
                commands.append(list(cmd))

            runner = CliRunner()
            with patch("agent_cli.cli.shutil.which", return_value="/usr/bin/docker"), patch(
                "agent_cli.cli._read_openai_api_key", return_value=None
            ), patch(
                "agent_cli.cli._docker_image_exists", return_value=False
            ), patch(
                "agent_cli.cli._docker_rm_force", return_value=None
            ), patch(
                "agent_cli.cli._run", side_effect=fake_run
            ):
                result = runner.invoke(
                    image_cli.main,
                    [
                        "--project",
                        str(project),
                        "--config-file",
                        str(config),
                        "--snapshot-image-tag",
                        "snapshot:test",
                        "--setup-script",
                        "echo hello",
                        "--prepare-snapshot-only",
                    ],
                )

            self.assertEqual(result.exit_code, 0, msg=result.output)
            setup_cmd = next((cmd for cmd in commands if len(cmd) >= 2 and cmd[:2] == ["docker", "run"]), None)
            self.assertIsNotNone(setup_cmd)
            assert setup_cmd is not None
            self.assertIn("--entrypoint", setup_cmd)
            self.assertIn("bash", setup_cmd)
            setup_script = setup_cmd[-1]
            self.assertIn("set -o pipefail", setup_script)
            self.assertIn("git config --global --add safe.directory '*'", setup_script)
            self.assertIn('AGENT_HUB_GIT_CREDENTIALS_SOURCE', setup_script)
            self.assertIn('AGENT_HUB_GIT_CREDENTIALS_FILE', setup_script)
            self.assertNotIn("git config --system", setup_script)
            self.assertNotIn("|| true", setup_script)
            self.assertNotIn("chown -R", setup_script)
            commit_cmd = next((cmd for cmd in commands if len(cmd) >= 3 and cmd[0:2] == ["docker", "commit"]), None)
            self.assertIsNotNone(commit_cmd)
            assert commit_cmd is not None
            self.assertIn("--change", commit_cmd)
            self.assertIn("USER root", commit_cmd)
            self.assertIn("WORKDIR /workspace", commit_cmd)
            self.assertIn('ENTRYPOINT ["/usr/local/bin/docker-entrypoint.py"]', commit_cmd)
            self.assertIn('CMD ["bash"]', commit_cmd)

    def test_cached_snapshot_skips_runtime_build(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            project = tmp_path / "project"
            project.mkdir(parents=True, exist_ok=True)
            config = tmp_path / "agent.config.toml"
            config.write_text("model = 'test'\n", encoding="utf-8")

            commands: list[list[str]] = []

            def fake_run(cmd: list[str], cwd: Path | None = None) -> None:
                del cwd
                commands.append(list(cmd))

            runner = CliRunner()
            with patch("agent_cli.cli.shutil.which", return_value="/usr/bin/docker"), patch(
                "agent_cli.cli._read_openai_api_key", return_value=None
            ), patch(
                "agent_cli.cli._docker_image_exists", return_value=True
            ), patch(
                "agent_cli.cli._run", side_effect=fake_run
            ):
                result = runner.invoke(
                    image_cli.main,
                    [
                        "--project",
                        str(project),
                        "--config-file",
                        str(config),
                        "--snapshot-image-tag",
                        "snapshot:test",
                        "--prepare-snapshot-only",
                    ],
                )

            self.assertEqual(result.exit_code, 0, msg=result.output)
            self.assertEqual(commands, [])

    def test_snapshot_preflight_allows_unwritable_descendants_when_mount_root_is_writable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            project = tmp_path / "project"
            project.mkdir(parents=True, exist_ok=True)
            config = tmp_path / "agent.config.toml"
            config.write_text("model = 'test'\n", encoding="utf-8")
            rw_mount = tmp_path / "rw-mount"
            locked_dir = rw_mount / "x86_64-linux" / "packages"
            locked_dir.mkdir(parents=True, exist_ok=True)
            locked_dir.chmod(0o500)

            commands: list[list[str]] = []

            def fake_run(cmd: list[str], cwd: Path | None = None) -> None:
                del cwd
                commands.append(list(cmd))

            runner = CliRunner()
            try:
                with patch("agent_cli.cli.shutil.which", return_value="/usr/bin/docker"), patch(
                    "agent_cli.cli._read_openai_api_key", return_value=None
                ), patch(
                    "agent_cli.cli._docker_image_exists", return_value=False
                ), patch(
                    "agent_cli.cli._docker_rm_force", return_value=None
                ), patch(
                    "agent_cli.cli._run", side_effect=fake_run
                ):
                    result = runner.invoke(
                        image_cli.main,
                        [
                            "--project",
                            str(project),
                            "--config-file",
                            str(config),
                            "--rw-mount",
                            f"{rw_mount}:/workspace/.ark_toolchain_cache",
                            "--snapshot-image-tag",
                            "snapshot:test",
                            "--setup-script",
                            "echo hello",
                            "--prepare-snapshot-only",
                        ],
                    )
            finally:
                locked_dir.chmod(0o700)

            self.assertEqual(result.exit_code, 0, msg=result.output)
            self.assertTrue(commands)
            docker_run_cmd = next((cmd for cmd in commands if len(cmd) >= 2 and cmd[:2] == ["docker", "run"]), None)
            self.assertIsNotNone(docker_run_cmd)

    def test_snapshot_preflight_fails_when_rw_mount_root_owner_uid_mismatches_runtime_uid(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            project = tmp_path / "project"
            project.mkdir(parents=True, exist_ok=True)
            config = tmp_path / "agent.config.toml"
            config.write_text("model = 'test'\n", encoding="utf-8")
            rw_mount = tmp_path / "rw-mount"
            rw_mount.mkdir(parents=True, exist_ok=True)

            commands: list[list[str]] = []

            def fake_run(cmd: list[str], cwd: Path | None = None) -> None:
                del cwd
                commands.append(list(cmd))

            runner = CliRunner()
            with patch("agent_cli.cli.shutil.which", return_value="/usr/bin/docker"), patch(
                "agent_cli.cli._read_openai_api_key", return_value=None
            ), patch(
                "agent_cli.cli._docker_image_exists", return_value=False
            ), patch(
                "agent_cli.cli._docker_rm_force", return_value=None
            ), patch(
                "agent_cli.cli._run", side_effect=fake_run
            ):
                result = runner.invoke(
                    image_cli.main,
                    [
                        "--project",
                        str(project),
                        "--config-file",
                        str(config),
                        "--local-uid",
                        "999999",
                        "--rw-mount",
                        f"{rw_mount}:/workspace/.ark_toolchain_cache",
                        "--snapshot-image-tag",
                        "snapshot:test",
                        "--setup-script",
                        "echo hello",
                        "--prepare-snapshot-only",
                    ],
                )

            self.assertNotEqual(result.exit_code, 0)
            self.assertIn("RW mount preflight failed", result.output)
            self.assertIn("owner uid does not match runtime uid", result.output)
            self.assertEqual(commands, [])

    def test_no_alt_screen_flag_passes_through_to_codex_command(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            project = tmp_path / "project"
            project.mkdir(parents=True, exist_ok=True)
            config = tmp_path / "agent.config.toml"
            config.write_text("model = 'test'\n", encoding="utf-8")

            commands: list[list[str]] = []

            def fake_run(cmd: list[str], cwd: Path | None = None) -> None:
                del cwd
                commands.append(list(cmd))

            runner = CliRunner()
            with patch("agent_cli.cli.shutil.which", return_value="/usr/bin/docker"), patch(
                "agent_cli.cli._read_openai_api_key", return_value=None
            ), patch(
                "agent_cli.cli._docker_image_exists", return_value=True
            ), patch(
                "agent_cli.cli._run", side_effect=fake_run
            ):
                result = runner.invoke(
                    image_cli.main,
                    [
                        "--project",
                        str(project),
                        "--config-file",
                        str(config),
                        "--no-alt-screen",
                    ],
                )

            self.assertEqual(result.exit_code, 0, msg=result.output)
            run_cmd = next((cmd for cmd in commands if len(cmd) >= 2 and cmd[:2] == ["docker", "run"]), None)
            self.assertIsNotNone(run_cmd)
            assert run_cmd is not None
            self.assertIn("codex", run_cmd)
            codex_index = run_cmd.index("codex")
            codex_args = run_cmd[codex_index + 1 :]
            self.assertIn("--ask-for-approval", codex_args)
            self.assertIn("never", codex_args)
            self.assertIn("--sandbox", codex_args)
            self.assertIn("danger-full-access", codex_args)
            self.assertIn("--config", codex_args)
            config_index = codex_args.index("--config")
            self.assertGreater(len(codex_args), config_index + 1)
            self.assertTrue(codex_args[config_index + 1].startswith("developer_instructions="))
            self.assertIn("--no-alt-screen", codex_args)

    def test_codex_runtime_does_not_duplicate_explicit_developer_instructions_override(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            project = tmp_path / "project"
            project.mkdir(parents=True, exist_ok=True)
            config = tmp_path / "agent.config.toml"
            config.write_text("model = 'test'\n", encoding="utf-8")

            commands: list[list[str]] = []

            def fake_run(cmd: list[str], cwd: Path | None = None) -> None:
                del cwd
                commands.append(list(cmd))

            runner = CliRunner()
            with patch("agent_cli.cli.shutil.which", return_value="/usr/bin/docker"), patch(
                "agent_cli.cli._read_openai_api_key", return_value=None
            ), patch(
                "agent_cli.cli._docker_image_exists", return_value=True
            ), patch(
                "agent_cli.cli._run", side_effect=fake_run
            ):
                result = runner.invoke(
                    image_cli.main,
                    [
                        "--project",
                        str(project),
                        "--config-file",
                        str(config),
                        "--",
                        "--config",
                        "developer_instructions='manual prompt override'",
                    ],
                )

            self.assertEqual(result.exit_code, 0, msg=result.output)
            run_cmd = next((cmd for cmd in commands if len(cmd) >= 2 and cmd[:2] == ["docker", "run"]), None)
            self.assertIsNotNone(run_cmd)
            assert run_cmd is not None
            codex_index = run_cmd.index("codex")
            codex_args = run_cmd[codex_index + 1 :]
            assignments = [
                codex_args[index + 1]
                for index, arg in enumerate(codex_args[:-1])
                if arg in {"--config", "-c"}
            ]
            developer_assignments = [item for item in assignments if item.startswith("developer_instructions=")]
            self.assertEqual(len(developer_assignments), 1)
            self.assertEqual(developer_assignments[0], "developer_instructions='manual prompt override'")

    def test_claude_agent_command_uses_claude_runtime_image(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            project = tmp_path / "project"
            project.mkdir(parents=True, exist_ok=True)
            config = tmp_path / "agent.config.toml"
            config.write_text("model = 'test'\n", encoding="utf-8")

            commands: list[list[str]] = []

            def fake_run(cmd: list[str], cwd: Path | None = None) -> None:
                del cwd
                commands.append(list(cmd))

            runner = CliRunner()
            with patch("agent_cli.cli.shutil.which", return_value="/usr/bin/docker"), patch(
                "agent_cli.cli._read_openai_api_key", return_value=None
            ), patch(
                "agent_cli.cli._docker_image_exists", return_value=True
            ), patch(
                "agent_cli.cli._run", side_effect=fake_run
            ):
                result = runner.invoke(
                    image_cli.main,
                    [
                        "--project",
                        str(project),
                        "--config-file",
                        str(config),
                        "--agent-command",
                        "claude",
                    ],
                )

            self.assertEqual(result.exit_code, 0, msg=result.output)
            run_cmd = next((cmd for cmd in commands if len(cmd) >= 2 and cmd[:2] == ["docker", "run"]), None)
            self.assertIsNotNone(run_cmd)
            assert run_cmd is not None
            self.assertIn(image_cli.CLAUDE_RUNTIME_IMAGE, run_cmd)
            image_index = run_cmd.index(image_cli.CLAUDE_RUNTIME_IMAGE)
            self.assertEqual(run_cmd[image_index + 1], "claude")
            self.assertIn("--permission-mode", run_cmd[image_index + 2 :])
            self.assertIn("bypassPermissions", run_cmd[image_index + 2 :])

    def test_claude_runtime_appends_shared_prompt_context_from_system_prompt_file_and_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            project = tmp_path / "project"
            project.mkdir(parents=True, exist_ok=True)
            config = tmp_path / "agent.config.toml"
            system_prompt = tmp_path / "SYSTEM_PROMPT.md"
            config.write_text(
                "model = 'test'\n"
                "project_doc_auto_load = true\n"
                "project_doc_fallback_filenames = ['AGENTS.md', 'README.md']\n"
                "project_doc_auto_load_extra_filenames = ['docs/agent-setup.md']\n"
                "project_doc_max_bytes = 4096\n",
                encoding="utf-8",
            )
            system_prompt.write_text(
                "Always run deterministic integration tests before final output.\n",
                encoding="utf-8",
            )

            commands: list[list[str]] = []

            def fake_run(cmd: list[str], cwd: Path | None = None) -> None:
                del cwd
                commands.append(list(cmd))

            runner = CliRunner()
            with patch("agent_cli.cli.shutil.which", return_value="/usr/bin/docker"), patch(
                "agent_cli.cli._read_openai_api_key", return_value=None
            ), patch(
                "agent_cli.cli._docker_image_exists", return_value=True
            ), patch(
                "agent_cli.cli._run", side_effect=fake_run
            ):
                result = runner.invoke(
                    image_cli.main,
                    [
                        "--project",
                        str(project),
                        "--config-file",
                        str(config),
                        "--system-prompt-file",
                        str(system_prompt),
                        "--agent-command",
                        "claude",
                    ],
                )

            self.assertEqual(result.exit_code, 0, msg=result.output)
            run_cmd = next((cmd for cmd in commands if len(cmd) >= 2 and cmd[:2] == ["docker", "run"]), None)
            self.assertIsNotNone(run_cmd)
            assert run_cmd is not None
            image_index = run_cmd.index(image_cli.CLAUDE_RUNTIME_IMAGE)
            claude_args = run_cmd[image_index + 2 :]
            self.assertIn("--append-system-prompt", claude_args)
            prompt_index = claude_args.index("--append-system-prompt")
            self.assertGreater(len(claude_args), prompt_index + 1)
            shared_prompt = claude_args[prompt_index + 1]
            self.assertIn("Always run deterministic integration tests before final output.", shared_prompt)
            self.assertIn("AGENTS.md", shared_prompt)
            self.assertIn("README.md", shared_prompt)
            self.assertIn("docs/agent-setup.md", shared_prompt)
            self.assertIn("4096 bytes", shared_prompt)

    def test_claude_runtime_does_not_duplicate_explicit_system_prompt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            project = tmp_path / "project"
            project.mkdir(parents=True, exist_ok=True)
            config = tmp_path / "agent.config.toml"
            system_prompt = tmp_path / "SYSTEM_PROMPT.md"
            config.write_text(
                "model = 'test'\n"
                "project_doc_auto_load = true\n"
                "project_doc_fallback_filenames = ['AGENTS.md']\n",
                encoding="utf-8",
            )
            system_prompt.write_text("Shared instructions from system prompt file.\n", encoding="utf-8")

            commands: list[list[str]] = []

            def fake_run(cmd: list[str], cwd: Path | None = None) -> None:
                del cwd
                commands.append(list(cmd))

            runner = CliRunner()
            with patch("agent_cli.cli.shutil.which", return_value="/usr/bin/docker"), patch(
                "agent_cli.cli._read_openai_api_key", return_value=None
            ), patch(
                "agent_cli.cli._docker_image_exists", return_value=True
            ), patch(
                "agent_cli.cli._run", side_effect=fake_run
            ):
                result = runner.invoke(
                    image_cli.main,
                    [
                        "--project",
                        str(project),
                        "--config-file",
                        str(config),
                        "--system-prompt-file",
                        str(system_prompt),
                        "--agent-command",
                        "claude",
                        "--",
                        "--append-system-prompt",
                        "manual system prompt",
                    ],
                )

            self.assertEqual(result.exit_code, 0, msg=result.output)
            run_cmd = next((cmd for cmd in commands if len(cmd) >= 2 and cmd[:2] == ["docker", "run"]), None)
            self.assertIsNotNone(run_cmd)
            assert run_cmd is not None
            image_index = run_cmd.index(image_cli.CLAUDE_RUNTIME_IMAGE)
            claude_args = run_cmd[image_index + 2 :]
            self.assertEqual(claude_args.count("--append-system-prompt"), 1)
            prompt_index = claude_args.index("--append-system-prompt")
            self.assertEqual(claude_args[prompt_index + 1], "manual system prompt")

    def test_gemini_agent_command_uses_gemini_runtime_image(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            project = tmp_path / "project"
            project.mkdir(parents=True, exist_ok=True)
            config = tmp_path / "agent.config.toml"
            config.write_text("model = 'test'\n", encoding="utf-8")

            commands: list[list[str]] = []

            def fake_run(cmd: list[str], cwd: Path | None = None) -> None:
                del cwd
                commands.append(list(cmd))

            runner = CliRunner()
            with patch("agent_cli.cli.shutil.which", return_value="/usr/bin/docker"), patch(
                "agent_cli.cli._read_openai_api_key", return_value=None
            ), patch(
                "agent_cli.cli._docker_image_exists", return_value=True
            ), patch(
                "agent_cli.cli._run", side_effect=fake_run
            ):
                result = runner.invoke(
                    image_cli.main,
                    [
                        "--project",
                        str(project),
                        "--config-file",
                        str(config),
                        "--agent-command",
                        "gemini",
                    ],
                )

            self.assertEqual(result.exit_code, 0, msg=result.output)
            run_cmd = next((cmd for cmd in commands if len(cmd) >= 2 and cmd[:2] == ["docker", "run"]), None)
            self.assertIsNotNone(run_cmd)
            assert run_cmd is not None
            self.assertIn(image_cli.GEMINI_RUNTIME_IMAGE, run_cmd)
            image_index = run_cmd.index(image_cli.GEMINI_RUNTIME_IMAGE)
            self.assertEqual(run_cmd[image_index + 1], "gemini")
            gemini_args = run_cmd[image_index + 2 :]
            self.assertIn("--approval-mode", gemini_args)
            self.assertIn("yolo", gemini_args)

    def test_gemini_runtime_flags_respect_explicit_approval_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            project = tmp_path / "project"
            project.mkdir(parents=True, exist_ok=True)
            config = tmp_path / "agent.config.toml"
            config.write_text("model = 'test'\n", encoding="utf-8")

            commands: list[list[str]] = []

            def fake_run(cmd: list[str], cwd: Path | None = None) -> None:
                del cwd
                commands.append(list(cmd))

            runner = CliRunner()
            with patch("agent_cli.cli.shutil.which", return_value="/usr/bin/docker"), patch(
                "agent_cli.cli._read_openai_api_key", return_value=None
            ), patch(
                "agent_cli.cli._docker_image_exists", return_value=True
            ), patch(
                "agent_cli.cli._run", side_effect=fake_run
            ):
                result = runner.invoke(
                    image_cli.main,
                    [
                        "--project",
                        str(project),
                        "--config-file",
                        str(config),
                        "--agent-command",
                        "gemini",
                        "--",
                        "--approval-mode",
                        "default",
                    ],
                )

            self.assertEqual(result.exit_code, 0, msg=result.output)
            run_cmd = next((cmd for cmd in commands if len(cmd) >= 2 and cmd[:2] == ["docker", "run"]), None)
            self.assertIsNotNone(run_cmd)
            assert run_cmd is not None
            image_index = run_cmd.index(image_cli.GEMINI_RUNTIME_IMAGE)
            gemini_args = run_cmd[image_index + 2 :]
            self.assertIn("--approval-mode", gemini_args)
            self.assertIn("default", gemini_args)
            self.assertNotIn("yolo", gemini_args)

    def test_gemini_runtime_syncs_shared_prompt_context_from_system_prompt_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            project = tmp_path / "project"
            project.mkdir(parents=True, exist_ok=True)
            agent_home = tmp_path / "agent-home"
            gemini_context_file = agent_home / ".gemini" / "GEMINI.md"
            system_prompt = tmp_path / "SYSTEM_PROMPT.md"
            gemini_context_file.parent.mkdir(parents=True, exist_ok=True)
            gemini_context_file.write_text(
                "Pre-existing Gemini-only context that should be replaced.\n",
                encoding="utf-8",
            )
            config = tmp_path / "agent.config.toml"
            config.write_text(
                "model = 'test'\n"
                "project_doc_auto_load = true\n"
                "project_doc_fallback_filenames = ['AGENTS.md', 'README.md']\n"
                "project_doc_auto_load_extra_filenames = ['docs/agent-setup.md']\n"
                "project_doc_max_bytes = 4096\n",
                encoding="utf-8",
            )
            system_prompt.write_text(
                "Always run deterministic integration tests before final output.\n",
                encoding="utf-8",
            )

            commands: list[list[str]] = []

            def fake_run(cmd: list[str], cwd: Path | None = None) -> None:
                del cwd
                commands.append(list(cmd))

            runner = CliRunner()
            with patch("agent_cli.cli.shutil.which", return_value="/usr/bin/docker"), patch(
                "agent_cli.cli._read_openai_api_key", return_value=None
            ), patch(
                "agent_cli.cli._docker_image_exists", return_value=True
            ), patch(
                "agent_cli.cli._run", side_effect=fake_run
            ):
                result = runner.invoke(
                    image_cli.main,
                    [
                        "--project",
                        str(project),
                        "--config-file",
                        str(config),
                        "--system-prompt-file",
                        str(system_prompt),
                        "--agent-home-path",
                        str(agent_home),
                        "--agent-command",
                        "gemini",
                    ],
                )

            self.assertEqual(result.exit_code, 0, msg=result.output)
            updated_context = gemini_context_file.read_text(encoding="utf-8")
            expected_context = image_cli._shared_prompt_context_from_config(
                config,
                core_system_prompt=system_prompt.read_text(encoding="utf-8").strip(),
            )
            self.assertEqual(updated_context, f"{expected_context}\n")
            self.assertNotIn("Pre-existing Gemini-only context that should be replaced.", updated_context)
            self.assertNotIn("agent_cli managed shared context", updated_context)

    def test_gemini_runtime_removes_context_file_when_shared_prompt_context_is_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            project = tmp_path / "project"
            project.mkdir(parents=True, exist_ok=True)
            agent_home = tmp_path / "agent-home"
            gemini_context_file = agent_home / ".gemini" / "GEMINI.md"
            system_prompt = tmp_path / "SYSTEM_PROMPT.md"
            gemini_context_file.parent.mkdir(parents=True, exist_ok=True)
            gemini_context_file.write_text("Pre-existing Gemini-only context.\n", encoding="utf-8")
            config = tmp_path / "agent.config.toml"
            config.write_text("model = 'test'\n", encoding="utf-8")
            system_prompt.write_text("\n", encoding="utf-8")

            commands: list[list[str]] = []

            def fake_run(cmd: list[str], cwd: Path | None = None) -> None:
                del cwd
                commands.append(list(cmd))

            runner = CliRunner()
            with patch("agent_cli.cli.shutil.which", return_value="/usr/bin/docker"), patch(
                "agent_cli.cli._read_openai_api_key", return_value=None
            ), patch(
                "agent_cli.cli._docker_image_exists", return_value=True
            ), patch(
                "agent_cli.cli._run", side_effect=fake_run
            ):
                result = runner.invoke(
                    image_cli.main,
                    [
                        "--project",
                        str(project),
                        "--config-file",
                        str(config),
                        "--system-prompt-file",
                        str(system_prompt),
                        "--agent-home-path",
                        str(agent_home),
                        "--agent-command",
                        "gemini",
                    ],
                )

            self.assertEqual(result.exit_code, 0, msg=result.output)
            self.assertFalse(gemini_context_file.exists())

    def test_codex_runtime_flags_respect_explicit_cli_values(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            project = tmp_path / "project"
            project.mkdir(parents=True, exist_ok=True)
            config = tmp_path / "agent.config.toml"
            config.write_text("model = 'test'\n", encoding="utf-8")

            commands: list[list[str]] = []

            def fake_run(cmd: list[str], cwd: Path | None = None) -> None:
                del cwd
                commands.append(list(cmd))

            runner = CliRunner()
            with patch("agent_cli.cli.shutil.which", return_value="/usr/bin/docker"), patch(
                "agent_cli.cli._read_openai_api_key", return_value=None
            ), patch(
                "agent_cli.cli._docker_image_exists", return_value=True
            ), patch(
                "agent_cli.cli._run", side_effect=fake_run
            ):
                result = runner.invoke(
                    image_cli.main,
                    [
                        "--project",
                        str(project),
                        "--config-file",
                        str(config),
                        "--",
                        "--ask-for-approval",
                        "on-request",
                        "--sandbox",
                        "workspace-write",
                    ],
                )

            self.assertEqual(result.exit_code, 0, msg=result.output)
            run_cmd = next((cmd for cmd in commands if len(cmd) >= 2 and cmd[:2] == ["docker", "run"]), None)
            self.assertIsNotNone(run_cmd)
            assert run_cmd is not None
            codex_index = run_cmd.index("codex")
            codex_args = run_cmd[codex_index + 1 :]
            self.assertIn("--ask-for-approval", codex_args)
            self.assertIn("on-request", codex_args)
            self.assertIn("--sandbox", codex_args)
            self.assertIn("workspace-write", codex_args)
            self.assertNotIn("danger-full-access", codex_args)

    def test_claude_runtime_flags_respect_explicit_permission_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            project = tmp_path / "project"
            project.mkdir(parents=True, exist_ok=True)
            config = tmp_path / "agent.config.toml"
            config.write_text("model = 'test'\n", encoding="utf-8")

            commands: list[list[str]] = []

            def fake_run(cmd: list[str], cwd: Path | None = None) -> None:
                del cwd
                commands.append(list(cmd))

            runner = CliRunner()
            with patch("agent_cli.cli.shutil.which", return_value="/usr/bin/docker"), patch(
                "agent_cli.cli._read_openai_api_key", return_value=None
            ), patch(
                "agent_cli.cli._docker_image_exists", return_value=True
            ), patch(
                "agent_cli.cli._run", side_effect=fake_run
            ):
                result = runner.invoke(
                    image_cli.main,
                    [
                        "--project",
                        str(project),
                        "--config-file",
                        str(config),
                        "--agent-command",
                        "claude",
                        "--",
                        "--permission-mode",
                        "acceptEdits",
                    ],
                )

            self.assertEqual(result.exit_code, 0, msg=result.output)
            run_cmd = next((cmd for cmd in commands if len(cmd) >= 2 and cmd[:2] == ["docker", "run"]), None)
            self.assertIsNotNone(run_cmd)
            assert run_cmd is not None
            image_index = run_cmd.index(image_cli.CLAUDE_RUNTIME_IMAGE)
            claude_args = run_cmd[image_index + 2 :]
            self.assertIn("--permission-mode", claude_args)
            self.assertIn("acceptEdits", claude_args)
            self.assertNotIn("bypassPermissions", claude_args)

    def test_snapshot_reuses_shared_setup_image_and_builds_provider_overlay(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            project = tmp_path / "project"
            project.mkdir(parents=True, exist_ok=True)
            config = tmp_path / "agent.config.toml"
            config.write_text("model = 'test'\n", encoding="utf-8")

            commands: list[list[str]] = []

            def fake_run(cmd: list[str], cwd: Path | None = None) -> None:
                del cwd
                commands.append(list(cmd))

            snapshot_tag = "snapshot:test"
            overlay_tag = image_cli._snapshot_runtime_image_for_provider(snapshot_tag, image_cli.AGENT_PROVIDER_CLAUDE)

            def fake_image_exists(tag: str) -> bool:
                if tag == snapshot_tag:
                    return True
                if tag == overlay_tag:
                    return False
                return True

            runner = CliRunner()
            with patch("agent_cli.cli.shutil.which", return_value="/usr/bin/docker"), patch(
                "agent_cli.cli._read_openai_api_key", return_value=None
            ), patch(
                "agent_cli.cli._docker_image_exists", side_effect=fake_image_exists
            ), patch(
                "agent_cli.cli._run", side_effect=fake_run
            ):
                result = runner.invoke(
                    image_cli.main,
                    [
                        "--project",
                        str(project),
                        "--config-file",
                        str(config),
                        "--agent-command",
                        "claude",
                        "--snapshot-image-tag",
                        snapshot_tag,
                    ],
                )

            self.assertEqual(result.exit_code, 0, msg=result.output)
            build_cmd = next((cmd for cmd in commands if len(cmd) >= 2 and cmd[:2] == ["docker", "build"]), None)
            self.assertIsNotNone(build_cmd)
            assert build_cmd is not None
            self.assertIn(f"BASE_IMAGE={snapshot_tag}", build_cmd)
            self.assertIn("AGENT_PROVIDER=claude", build_cmd)
            run_cmd = next((cmd for cmd in commands if len(cmd) >= 2 and cmd[:2] == ["docker", "run"]), None)
            self.assertIsNotNone(run_cmd)
            assert run_cmd is not None
            self.assertIn(overlay_tag, run_cmd)

    def test_snapshot_reuses_shared_setup_image_and_builds_gemini_overlay(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            project = tmp_path / "project"
            project.mkdir(parents=True, exist_ok=True)
            config = tmp_path / "agent.config.toml"
            config.write_text("model = 'test'\n", encoding="utf-8")

            commands: list[list[str]] = []

            def fake_run(cmd: list[str], cwd: Path | None = None) -> None:
                del cwd
                commands.append(list(cmd))

            snapshot_tag = "snapshot:test"
            overlay_tag = image_cli._snapshot_runtime_image_for_provider(snapshot_tag, image_cli.AGENT_PROVIDER_GEMINI)

            def fake_image_exists(tag: str) -> bool:
                if tag == snapshot_tag:
                    return True
                if tag == overlay_tag:
                    return False
                return True

            runner = CliRunner()
            with patch("agent_cli.cli.shutil.which", return_value="/usr/bin/docker"), patch(
                "agent_cli.cli._read_openai_api_key", return_value=None
            ), patch(
                "agent_cli.cli._docker_image_exists", side_effect=fake_image_exists
            ), patch(
                "agent_cli.cli._run", side_effect=fake_run
            ):
                result = runner.invoke(
                    image_cli.main,
                    [
                        "--project",
                        str(project),
                        "--config-file",
                        str(config),
                        "--agent-command",
                        "gemini",
                        "--snapshot-image-tag",
                        snapshot_tag,
                    ],
                )

            self.assertEqual(result.exit_code, 0, msg=result.output)
            build_cmd = next((cmd for cmd in commands if len(cmd) >= 2 and cmd[:2] == ["docker", "build"]), None)
            self.assertIsNotNone(build_cmd)
            assert build_cmd is not None
            self.assertIn(f"BASE_IMAGE={snapshot_tag}", build_cmd)
            self.assertIn("AGENT_PROVIDER=gemini", build_cmd)
            run_cmd = next((cmd for cmd in commands if len(cmd) >= 2 and cmd[:2] == ["docker", "run"]), None)
            self.assertIsNotNone(run_cmd)
            assert run_cmd is not None
            self.assertIn(overlay_tag, run_cmd)

    def test_resume_uses_shell_command_as_container_entry_command(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            project = tmp_path / "project"
            project.mkdir(parents=True, exist_ok=True)
            config = tmp_path / "agent.config.toml"
            config.write_text("model = 'test'\n", encoding="utf-8")

            commands: list[list[str]] = []

            def fake_run(cmd: list[str], cwd: Path | None = None) -> None:
                del cwd
                commands.append(list(cmd))

            runner = CliRunner()
            with patch("agent_cli.cli.shutil.which", return_value="/usr/bin/docker"), patch(
                "agent_cli.cli._read_openai_api_key", return_value=None
            ), patch(
                "agent_cli.cli._docker_image_exists", return_value=True
            ), patch(
                "agent_cli.cli._run", side_effect=fake_run
            ):
                result = runner.invoke(
                    image_cli.main,
                    [
                        "--project",
                        str(project),
                        "--config-file",
                        str(config),
                        "--resume",
                    ],
                )

            self.assertEqual(result.exit_code, 0, msg=result.output)
            run_cmd = next((cmd for cmd in commands if len(cmd) >= 2 and cmd[:2] == ["docker", "run"]), None)
            self.assertIsNotNone(run_cmd)
            assert run_cmd is not None
            image_index = run_cmd.index(image_cli.DEFAULT_RUNTIME_IMAGE)
            self.assertEqual(run_cmd[image_index + 1], "bash")
            self.assertEqual(run_cmd[image_index + 2], "-lc")
            resume_script = run_cmd[image_index + 3]
            self.assertIn("codex --ask-for-approval never --sandbox danger-full-access --config", resume_script)
            self.assertIn("developer_instructions=", resume_script)
            self.assertIn("resume --last", resume_script)
            self.assertIn("exec codex --ask-for-approval never --sandbox danger-full-access --config", resume_script)

    def test_resume_with_no_alt_screen_passes_flag_to_resume_script(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            project = tmp_path / "project"
            project.mkdir(parents=True, exist_ok=True)
            config = tmp_path / "agent.config.toml"
            config.write_text("model = 'test'\n", encoding="utf-8")

            commands: list[list[str]] = []

            def fake_run(cmd: list[str], cwd: Path | None = None) -> None:
                del cwd
                commands.append(list(cmd))

            runner = CliRunner()
            with patch("agent_cli.cli.shutil.which", return_value="/usr/bin/docker"), patch(
                "agent_cli.cli._read_openai_api_key", return_value=None
            ), patch(
                "agent_cli.cli._docker_image_exists", return_value=True
            ), patch(
                "agent_cli.cli._run", side_effect=fake_run
            ):
                result = runner.invoke(
                    image_cli.main,
                    [
                        "--project",
                        str(project),
                        "--config-file",
                        str(config),
                        "--resume",
                        "--no-alt-screen",
                    ],
                )

            self.assertEqual(result.exit_code, 0, msg=result.output)
            run_cmd = next((cmd for cmd in commands if len(cmd) >= 2 and cmd[:2] == ["docker", "run"]), None)
            self.assertIsNotNone(run_cmd)
            assert run_cmd is not None
            image_index = run_cmd.index(image_cli.DEFAULT_RUNTIME_IMAGE)
            resume_script = run_cmd[image_index + 3]
            self.assertIn("codex --ask-for-approval never --sandbox danger-full-access --config", resume_script)
            self.assertIn("--no-alt-screen resume --last", resume_script)
            self.assertIn("developer_instructions=", resume_script)
            self.assertIn("exec codex --ask-for-approval never --sandbox danger-full-access --config", resume_script)

    def test_resume_rejects_non_codex_agent_command(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            project = tmp_path / "project"
            project.mkdir(parents=True, exist_ok=True)
            config = tmp_path / "agent.config.toml"
            config.write_text("model = 'test'\n", encoding="utf-8")

            runner = CliRunner()
            with patch("agent_cli.cli.shutil.which", return_value="/usr/bin/docker"), patch(
                "agent_cli.cli._read_openai_api_key", return_value=None
            ), patch(
                "agent_cli.cli._docker_image_exists", return_value=True
            ), patch(
                "agent_cli.cli._run", return_value=None
            ):
                result = runner.invoke(
                    image_cli.main,
                    [
                        "--project",
                        str(project),
                        "--config-file",
                        str(config),
                        "--agent-command",
                        "claude",
                        "--resume",
                    ],
                )

            self.assertNotEqual(result.exit_code, 0)
            self.assertIn("--resume is currently only supported when --agent-command is codex.", result.output)

    def test_cli_mounts_codex_claude_and_gemini_dirs_for_container_home_persistence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            project = tmp_path / "project"
            project.mkdir(parents=True, exist_ok=True)
            config = tmp_path / "agent.config.toml"
            config.write_text("model = 'test'\n", encoding="utf-8")
            agent_home = tmp_path / "agent-home"

            commands: list[list[str]] = []

            def fake_run(cmd: list[str], cwd: Path | None = None) -> None:
                del cwd
                commands.append(list(cmd))

            runner = CliRunner()
            with patch("agent_cli.cli.shutil.which", return_value="/usr/bin/docker"), patch(
                "agent_cli.cli._read_openai_api_key", return_value=None
            ), patch(
                "agent_cli.cli._docker_image_exists", return_value=True
            ), patch(
                "agent_cli.cli._run", side_effect=fake_run
            ):
                result = runner.invoke(
                    image_cli.main,
                    [
                        "--project",
                        str(project),
                        "--config-file",
                        str(config),
                        "--agent-home-path",
                        str(agent_home),
                    ],
                )

            self.assertEqual(result.exit_code, 0, msg=result.output)
            run_cmd = next((cmd for cmd in commands if len(cmd) >= 2 and cmd[:2] == ["docker", "run"]), None)
            self.assertIsNotNone(run_cmd)
            assert run_cmd is not None

            container_home = image_cli.DEFAULT_CONTAINER_HOME
            full_home_mount = f"{agent_home.resolve()}:{container_home}"
            codex_mount = f"{(agent_home / '.codex').resolve()}:{container_home}/.codex"
            claude_mount = f"{(agent_home / '.claude').resolve()}:{container_home}/.claude"
            claude_json_mount = f"{(agent_home / '.claude.json').resolve()}:{container_home}/.claude.json"
            claude_config_mount = f"{(agent_home / '.config' / 'claude').resolve()}:{container_home}/.config/claude"
            gemini_mount = f"{(agent_home / '.gemini').resolve()}:{container_home}/.gemini"
            self.assertNotIn(full_home_mount, run_cmd)
            self.assertIn(codex_mount, run_cmd)
            self.assertIn(claude_mount, run_cmd)
            self.assertIn(claude_json_mount, run_cmd)
            self.assertIn(claude_config_mount, run_cmd)
            self.assertIn(gemini_mount, run_cmd)

    def test_cli_mounts_git_credentials_and_sets_git_config_env(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            project = tmp_path / "project"
            project.mkdir(parents=True, exist_ok=True)
            config = tmp_path / "agent.config.toml"
            config.write_text("model = 'test'\n", encoding="utf-8")
            credential_file = tmp_path / "github_credentials"
            credential_file.write_text(
                "https://x-access-token:ghs_test_installation_token@github.com\n",
                encoding="utf-8",
            )

            commands: list[list[str]] = []

            def fake_run(cmd: list[str], cwd: Path | None = None) -> None:
                del cwd
                commands.append(list(cmd))

            runner = CliRunner()
            with patch("agent_cli.cli.shutil.which", return_value="/usr/bin/docker"), patch(
                "agent_cli.cli._read_openai_api_key", return_value=None
            ), patch(
                "agent_cli.cli._docker_image_exists", return_value=True
            ), patch(
                "agent_cli.cli._run", side_effect=fake_run
            ):
                result = runner.invoke(
                    image_cli.main,
                    [
                        "--project",
                        str(project),
                        "--config-file",
                        str(config),
                        "--git-credential-file",
                        str(credential_file),
                        "--git-credential-host",
                        "github.com",
                    ],
                )

            self.assertEqual(result.exit_code, 0, msg=result.output)
            run_cmd = next((cmd for cmd in commands if len(cmd) >= 2 and cmd[:2] == ["docker", "run"]), None)
            self.assertIsNotNone(run_cmd)
            assert run_cmd is not None

            self.assertIn(
                f"{credential_file.resolve()}:{image_cli.GIT_CREDENTIALS_SOURCE_PATH}:ro",
                run_cmd,
            )

            env_values = [
                run_cmd[index + 1]
                for index, part in enumerate(run_cmd[:-1])
                if part == "--env"
            ]
            self.assertIn("GIT_TERMINAL_PROMPT=0", env_values)
            self.assertIn(
                f"AGENT_HUB_GIT_CREDENTIALS_SOURCE={image_cli.GIT_CREDENTIALS_SOURCE_PATH}",
                env_values,
            )
            self.assertIn(
                f"AGENT_HUB_GIT_CREDENTIALS_FILE={image_cli.GIT_CREDENTIALS_FILE_PATH}",
                env_values,
            )
            self.assertIn("AGENT_HUB_GIT_CREDENTIAL_HOST=github.com", env_values)
            self.assertIn("GIT_CONFIG_COUNT=3", env_values)
            self.assertIn("GIT_CONFIG_KEY_0=credential.helper", env_values)
            self.assertIn(
                f"GIT_CONFIG_VALUE_0=store --file={image_cli.GIT_CREDENTIALS_FILE_PATH}",
                env_values,
            )
            self.assertIn("GIT_CONFIG_KEY_1=url.https://github.com/.insteadOf", env_values)
            self.assertIn("GIT_CONFIG_VALUE_1=git@github.com:", env_values)
            self.assertIn("GIT_CONFIG_KEY_2=url.https://github.com/.insteadOf", env_values)
            self.assertIn("GIT_CONFIG_VALUE_2=ssh://git@github.com/", env_values)

    def test_cli_auto_discovers_agent_hub_github_credentials_when_flags_not_provided(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            project = tmp_path / "project"
            project.mkdir(parents=True, exist_ok=True)
            config = tmp_path / "agent.config.toml"
            config.write_text("model = 'test'\n", encoding="utf-8")

            stored_credentials = (
                tmp_path
                / ".local"
                / "share"
                / "agent-hub"
                / image_cli.AGENT_HUB_SECRETS_DIR_NAME
                / image_cli.AGENT_HUB_GITHUB_CREDENTIALS_FILE_NAME
            )
            stored_credentials.parent.mkdir(parents=True, exist_ok=True)
            stored_credentials.write_text(
                "https://x-access-token:ghs_test_installation_token@github.com\n",
                encoding="utf-8",
            )

            commands: list[list[str]] = []

            def fake_run(cmd: list[str], cwd: Path | None = None) -> None:
                del cwd
                commands.append(list(cmd))

            runner = CliRunner()
            with patch("agent_cli.cli.Path.home", return_value=tmp_path), patch(
                "agent_cli.cli.shutil.which", return_value="/usr/bin/docker"
            ), patch(
                "agent_cli.cli._read_openai_api_key", return_value=None
            ), patch(
                "agent_cli.cli._docker_image_exists", return_value=True
            ), patch(
                "agent_cli.cli._run", side_effect=fake_run
            ):
                result = runner.invoke(
                    image_cli.main,
                    [
                        "--project",
                        str(project),
                        "--config-file",
                        str(config),
                    ],
                )

            self.assertEqual(result.exit_code, 0, msg=result.output)
            run_cmd = next((cmd for cmd in commands if len(cmd) >= 2 and cmd[:2] == ["docker", "run"]), None)
            self.assertIsNotNone(run_cmd)
            assert run_cmd is not None

            self.assertIn(
                f"{stored_credentials.resolve()}:{image_cli.GIT_CREDENTIALS_SOURCE_PATH}:ro",
                run_cmd,
            )
            env_values = [
                run_cmd[index + 1]
                for index, part in enumerate(run_cmd[:-1])
                if part == "--env"
            ]
            self.assertIn("AGENT_HUB_GIT_CREDENTIAL_HOST=github.com", env_values)
            self.assertIn("GIT_CONFIG_KEY_1=url.https://github.com/.insteadOf", env_values)
            self.assertIn("GIT_CONFIG_VALUE_1=git@github.com:", env_values)

    def test_cli_auto_discovery_parses_github_enterprise_host(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            project = tmp_path / "project"
            project.mkdir(parents=True, exist_ok=True)
            config = tmp_path / "agent.config.toml"
            config.write_text("model = 'test'\n", encoding="utf-8")

            stored_credentials = (
                tmp_path
                / ".local"
                / "share"
                / "agent-hub"
                / image_cli.AGENT_HUB_SECRETS_DIR_NAME
                / image_cli.AGENT_HUB_GITHUB_CREDENTIALS_FILE_NAME
            )
            stored_credentials.parent.mkdir(parents=True, exist_ok=True)
            stored_credentials.write_text(
                "https://agentuser:github_pat_enterprise_test_token@github.enterprise.local\n",
                encoding="utf-8",
            )

            commands: list[list[str]] = []

            def fake_run(cmd: list[str], cwd: Path | None = None) -> None:
                del cwd
                commands.append(list(cmd))

            runner = CliRunner()
            with patch("agent_cli.cli.Path.home", return_value=tmp_path), patch(
                "agent_cli.cli.shutil.which", return_value="/usr/bin/docker"
            ), patch(
                "agent_cli.cli._read_openai_api_key", return_value=None
            ), patch(
                "agent_cli.cli._docker_image_exists", return_value=True
            ), patch(
                "agent_cli.cli._run", side_effect=fake_run
            ):
                result = runner.invoke(
                    image_cli.main,
                    [
                        "--project",
                        str(project),
                        "--config-file",
                        str(config),
                    ],
                )

            self.assertEqual(result.exit_code, 0, msg=result.output)
            run_cmd = next((cmd for cmd in commands if len(cmd) >= 2 and cmd[:2] == ["docker", "run"]), None)
            self.assertIsNotNone(run_cmd)
            assert run_cmd is not None
            env_values = [
                run_cmd[index + 1]
                for index, part in enumerate(run_cmd[:-1])
                if part == "--env"
            ]
            self.assertIn("AGENT_HUB_GIT_CREDENTIAL_HOST=github.enterprise.local", env_values)
            self.assertIn("GIT_CONFIG_KEY_1=url.https://github.enterprise.local/.insteadOf", env_values)
            self.assertIn("GIT_CONFIG_VALUE_1=git@github.enterprise.local:", env_values)
            self.assertIn("GIT_CONFIG_KEY_2=url.https://github.enterprise.local/.insteadOf", env_values)
            self.assertIn("GIT_CONFIG_VALUE_2=ssh://git@github.enterprise.local/", env_values)

    def test_cli_mounts_docker_socket_into_container(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            project = tmp_path / "project"
            project.mkdir(parents=True, exist_ok=True)
            config = tmp_path / "agent.config.toml"
            config.write_text("model = 'test'\n", encoding="utf-8")

            commands: list[list[str]] = []

            def fake_run(cmd: list[str], cwd: Path | None = None) -> None:
                del cwd
                commands.append(list(cmd))

            runner = CliRunner()
            with patch("agent_cli.cli.shutil.which", return_value="/usr/bin/docker"), patch(
                "agent_cli.cli._read_openai_api_key", return_value=None
            ), patch(
                "agent_cli.cli._docker_image_exists", return_value=True
            ), patch(
                "agent_cli.cli._run", side_effect=fake_run
            ):
                result = runner.invoke(
                    image_cli.main,
                    [
                        "--project",
                        str(project),
                        "--config-file",
                        str(config),
                    ],
                )

            self.assertEqual(result.exit_code, 0, msg=result.output)
            run_cmd = next((cmd for cmd in commands if len(cmd) >= 2 and cmd[:2] == ["docker", "run"]), None)
            self.assertIsNotNone(run_cmd)
            assert run_cmd is not None
            self.assertIn("--volume", run_cmd)
            self.assertIn(f"{image_cli.DOCKER_SOCKET_PATH}:{image_cli.DOCKER_SOCKET_PATH}", run_cmd)
            self.assertIn("--tmpfs", run_cmd)
            self.assertIn(image_cli.TMP_DIR_TMPFS_SPEC, run_cmd)

    def test_cli_mounts_project_under_workspace_with_project_directory_name(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            project = tmp_path / "demo-project"
            project.mkdir(parents=True, exist_ok=True)
            config = tmp_path / "agent.config.toml"
            config.write_text("model = 'test'\n", encoding="utf-8")

            commands: list[list[str]] = []

            def fake_run(cmd: list[str], cwd: Path | None = None) -> None:
                del cwd
                commands.append(list(cmd))

            runner = CliRunner()
            with patch("agent_cli.cli.shutil.which", return_value="/usr/bin/docker"), patch(
                "agent_cli.cli._read_openai_api_key", return_value=None
            ), patch(
                "agent_cli.cli._docker_image_exists", return_value=True
            ), patch(
                "agent_cli.cli._run", side_effect=fake_run
            ):
                result = runner.invoke(
                    image_cli.main,
                    [
                        "--project",
                        str(project),
                        "--config-file",
                        str(config),
                    ],
                )

            self.assertEqual(result.exit_code, 0, msg=result.output)
            run_cmd = next((cmd for cmd in commands if len(cmd) >= 2 and cmd[:2] == ["docker", "run"]), None)
            self.assertIsNotNone(run_cmd)
            assert run_cmd is not None
            expected_container_project = f"{image_cli.DEFAULT_CONTAINER_HOME}/demo-project"
            self.assertIn("--workdir", run_cmd)
            self.assertIn(expected_container_project, run_cmd)
            self.assertIn(f"{project.resolve()}:{expected_container_project}", run_cmd)
            self.assertIn("CONTAINER_PROJECT_PATH=/workspace/demo-project", run_cmd)
            env_values = [
                run_cmd[index + 1]
                for index, part in enumerate(run_cmd[:-1])
                if part == "--env"
            ]
            self.assertIn(
                f"UV_PROJECT_ENVIRONMENT={expected_container_project}/.venv",
                env_values,
            )

    def test_cli_rejects_mount_targets_inside_project_mount_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            project = tmp_path / "project"
            project.mkdir(parents=True, exist_ok=True)
            config = tmp_path / "agent.config.toml"
            config.write_text("model = 'test'\n", encoding="utf-8")
            rw_mount = tmp_path / "rw-cache"
            rw_mount.mkdir(parents=True, exist_ok=True)

            commands: list[list[str]] = []

            def fake_run(cmd: list[str], cwd: Path | None = None) -> None:
                del cwd
                commands.append(list(cmd))

            runner = CliRunner()
            with patch("agent_cli.cli.shutil.which", return_value="/usr/bin/docker"), patch(
                "agent_cli.cli._read_openai_api_key", return_value=None
            ), patch(
                "agent_cli.cli._docker_image_exists", return_value=True
            ), patch(
                "agent_cli.cli._run", side_effect=fake_run
            ):
                result = runner.invoke(
                    image_cli.main,
                    [
                        "--project",
                        str(project),
                        "--config-file",
                        str(config),
                        "--rw-mount",
                        f"{rw_mount}:/workspace/project/.cache/sccache",
                    ],
                )

            self.assertNotEqual(result.exit_code, 0)
            self.assertIn("inside the project mount path", result.output)
            self.assertEqual(commands, [])

    def test_cli_sets_runtime_user_and_group_adds(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            project = tmp_path / "project"
            project.mkdir(parents=True, exist_ok=True)
            config = tmp_path / "agent.config.toml"
            config.write_text("model = 'test'\n", encoding="utf-8")

            commands: list[list[str]] = []

            def fake_run(cmd: list[str], cwd: Path | None = None) -> None:
                del cwd
                commands.append(list(cmd))

            runner = CliRunner()
            with patch("agent_cli.cli.shutil.which", return_value="/usr/bin/docker"), patch(
                "agent_cli.cli._read_openai_api_key", return_value=None
            ), patch(
                "agent_cli.cli._docker_image_exists", return_value=True
            ), patch(
                "agent_cli.cli._docker_socket_gid", return_value=4444
            ), patch(
                "agent_cli.cli._run", side_effect=fake_run
            ):
                result = runner.invoke(
                    image_cli.main,
                    [
                        "--project",
                        str(project),
                        "--config-file",
                        str(config),
                        "--local-uid",
                        "1234",
                        "--local-gid",
                        "2345",
                        "--local-supplementary-gids",
                        "3000,3001",
                    ],
                )

            self.assertEqual(result.exit_code, 0, msg=result.output)
            run_cmd = next((cmd for cmd in commands if len(cmd) >= 2 and cmd[:2] == ["docker", "run"]), None)
            self.assertIsNotNone(run_cmd)
            assert run_cmd is not None
            self.assertIn("--user", run_cmd)
            self.assertIn("1234:2345", run_cmd)

            self.assertIn("--group-add", run_cmd)
            self.assertIn("3000", run_cmd)
            self.assertIn("3001", run_cmd)
            self.assertIn("4444", run_cmd)

    def test_cli_adds_host_gateway_alias_on_linux(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            project = tmp_path / "project"
            project.mkdir(parents=True, exist_ok=True)
            config = tmp_path / "agent.config.toml"
            config.write_text("model = 'test'\n", encoding="utf-8")

            commands: list[list[str]] = []

            def fake_run(cmd: list[str], cwd: Path | None = None) -> None:
                del cwd
                commands.append(list(cmd))

            runner = CliRunner()
            with patch("agent_cli.cli.sys.platform", "linux"), patch(
                "agent_cli.cli.shutil.which",
                return_value="/usr/bin/docker",
            ), patch(
                "agent_cli.cli._read_openai_api_key",
                return_value=None,
            ), patch(
                "agent_cli.cli._docker_image_exists",
                return_value=True,
            ), patch(
                "agent_cli.cli._run",
                side_effect=fake_run,
            ):
                result = runner.invoke(
                    image_cli.main,
                    [
                        "--project",
                        str(project),
                        "--config-file",
                        str(config),
                    ],
                )

            self.assertEqual(result.exit_code, 0, msg=result.output)
            run_cmd = next((cmd for cmd in commands if len(cmd) >= 2 and cmd[:2] == ["docker", "run"]), None)
            self.assertIsNotNone(run_cmd)
            assert run_cmd is not None
            self.assertIn("--add-host", run_cmd)
            self.assertIn("host.docker.internal:host-gateway", run_cmd)

    def test_agent_hub_main_clean_start_invokes_state_cleanup(self) -> None:
        runner = CliRunner()
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            data_dir = tmp_path / "hub"
            config = tmp_path / "agent.config.toml"
            config.write_text("model = 'test'\n", encoding="utf-8")

            with patch("agent_hub.server.uvicorn.run", return_value=None), patch.object(
                hub_server.HubState,
                "clean_start",
                return_value={
                    "stopped_chats": 0,
                    "cleared_chats": 0,
                    "projects_reset": 0,
                    "docker_images_requested": 0,
                },
            ) as clean_patch:
                result = runner.invoke(
                    hub_server.main,
                    [
                        "--data-dir",
                        str(data_dir),
                        "--config-file",
                        str(config),
                        "--no-frontend-build",
                        "--clean-start",
                    ],
                )

            self.assertEqual(result.exit_code, 0, msg=result.output)
            self.assertEqual(clean_patch.call_count, 1)
            self.assertIn("Clean start completed", result.output)

    def test_agent_hub_main_respects_log_level_flag(self) -> None:
        runner = CliRunner()
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            data_dir = tmp_path / "hub"
            config = tmp_path / "agent.config.toml"
            config.write_text("model = 'test'\n", encoding="utf-8")

            with patch("agent_hub.server.uvicorn.run", return_value=None) as uvicorn_run:
                result = runner.invoke(
                    hub_server.main,
                    [
                        "--data-dir",
                        str(data_dir),
                        "--config-file",
                        str(config),
                        "--no-frontend-build",
                        "--log-level",
                        "warning",
                    ],
                )

            self.assertEqual(result.exit_code, 0, msg=result.output)
            self.assertEqual(uvicorn_run.call_count, 1)
            kwargs = uvicorn_run.call_args.kwargs
            self.assertEqual(kwargs.get("log_level"), "warning")

    def test_agent_hub_main_caps_uvicorn_log_level_at_info_for_debug(self) -> None:
        runner = CliRunner()
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            data_dir = tmp_path / "hub"
            config = tmp_path / "agent.config.toml"
            config.write_text("model = 'test'\n", encoding="utf-8")

            with patch("agent_hub.server.uvicorn.run", return_value=None) as uvicorn_run:
                result = runner.invoke(
                    hub_server.main,
                    [
                        "--data-dir",
                        str(data_dir),
                        "--config-file",
                        str(config),
                        "--no-frontend-build",
                        "--log-level",
                        "debug",
                    ],
                )

            self.assertEqual(result.exit_code, 0, msg=result.output)
            kwargs = uvicorn_run.call_args.kwargs
            self.assertEqual(kwargs.get("log_level"), "info")

    def test_agent_hub_main_passes_artifact_publish_base_url_to_state(self) -> None:
        runner = CliRunner()
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            data_dir = tmp_path / "hub"
            config = tmp_path / "agent.config.toml"
            config.write_text("model = 'test'\n", encoding="utf-8")

            with patch("agent_hub.server.HubState") as state_cls, patch(
                "agent_hub.server.uvicorn.run",
                return_value=None,
            ):
                result = runner.invoke(
                    hub_server.main,
                    [
                        "--data-dir",
                        str(data_dir),
                        "--config-file",
                        str(config),
                        "--no-frontend-build",
                        "--artifact-publish-base-url",
                        "http://172.17.0.4:8765/hub",
                    ],
                )

            self.assertEqual(result.exit_code, 0, msg=result.output)
            kwargs = state_cls.call_args.kwargs
            self.assertEqual(kwargs.get("artifact_publish_base_url"), "http://172.17.0.4:8765/hub")


class HubApiAsyncRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.tmp.name)
        self.data_dir = self.tmp_path / "hub"
        self.config = self.tmp_path / "agent.config.toml"
        self.config.write_text("model = 'test'\n", encoding="utf-8")
        self.runner = CliRunner()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _build_app(self):
        with patch("agent_hub.server.uvicorn.run", return_value=None) as uvicorn_run:
            result = self.runner.invoke(
                hub_server.main,
                [
                    "--data-dir",
                    str(self.data_dir),
                    "--config-file",
                    str(self.config),
                    "--no-frontend-build",
                ],
            )
        self.assertEqual(result.exit_code, 0, msg=result.output)
        return uvicorn_run.call_args.args[0]

    @staticmethod
    async def _fake_to_thread(func, *args, **kwargs):
        return func(*args, **kwargs)

    def test_auto_configure_route_runs_state_call_in_worker_thread(self) -> None:
        app = self._build_app()
        recommendation = {
            "default_branch": "main",
            "base_image_mode": "tag",
            "base_image_value": "ubuntu:22.04",
            "setup_script": "",
            "default_ro_mounts": [],
            "default_rw_mounts": [],
            "default_env_vars": [],
            "notes": "",
            "analysis_model": "chatgpt-account-codex",
        }

        with patch(
            "agent_hub.server.asyncio.to_thread",
            new=AsyncMock(side_effect=self._fake_to_thread),
        ) as to_thread, patch.object(
            hub_server.HubState,
            "auto_configure_project",
            return_value=recommendation,
        ) as auto_config:
            with TestClient(app) as client:
                response = client.post(
                    "/api/projects/auto-configure",
                    json={
                        "repo_url": "https://example.com/org/repo.git",
                        "default_branch": "main",
                        "request_id": "pending-auto-123",
                    },
                )

        self.assertEqual(response.status_code, 200, msg=response.text)
        self.assertEqual(response.json(), {"recommendation": recommendation})
        to_thread.assert_awaited_once()
        auto_config.assert_called_once_with(
            repo_url="https://example.com/org/repo.git",
            default_branch="main",
            request_id="pending-auto-123",
        )

    def test_create_project_route_runs_state_call_in_worker_thread(self) -> None:
        app = self._build_app()
        host_ro = self.tmp_path / "host_ro"
        host_rw = self.tmp_path / "host_rw"
        host_ro.mkdir(parents=True, exist_ok=True)
        host_rw.mkdir(parents=True, exist_ok=True)
        project = {
            "id": "project-1",
            "name": "demo",
            "repo_url": "https://example.com/org/repo.git",
            "default_branch": "main",
        }

        with patch(
            "agent_hub.server.asyncio.to_thread",
            new=AsyncMock(side_effect=self._fake_to_thread),
        ) as to_thread, patch.object(
            hub_server.HubState,
            "add_project",
            return_value=project,
        ) as add_project:
            with TestClient(app) as client:
                response = client.post(
                    "/api/projects",
                    json={
                        "repo_url": "https://example.com/org/repo.git",
                        "name": "demo",
                        "default_branch": "main",
                        "setup_script": "echo hello",
                        "base_image_mode": "tag",
                        "base_image_value": "ubuntu:22.04",
                        "default_ro_mounts": [f"{host_ro}:/container/ro"],
                        "default_rw_mounts": [f"{host_rw}:/container/rw"],
                        "default_env_vars": ["FOO=bar"],
                    },
                )

        self.assertEqual(response.status_code, 200, msg=response.text)
        self.assertEqual(response.json(), {"project": project})
        to_thread.assert_awaited_once()
        add_project.assert_called_once_with(
            repo_url="https://example.com/org/repo.git",
            name="demo",
            default_branch="main",
            setup_script="echo hello",
            base_image_mode="tag",
            base_image_value="ubuntu:22.04",
            default_ro_mounts=[f"{host_ro}:/container/ro"],
            default_rw_mounts=[f"{host_rw}:/container/rw"],
            default_env_vars=["FOO=bar"],
        )

    def test_project_chat_start_route_runs_state_call_in_worker_thread(self) -> None:
        app = self._build_app()
        chat = {"id": "chat-1", "status": "running"}

        with patch(
            "agent_hub.server.asyncio.to_thread",
            new=AsyncMock(side_effect=self._fake_to_thread),
        ) as to_thread, patch.object(
            hub_server.HubState,
            "create_and_start_chat",
            return_value=chat,
        ) as start_chat:
            with TestClient(app) as client:
                response = client.post(
                    "/api/projects/project-1/chats/start",
                    json={"agent_type": "codex", "agent_args": ["--model", "gpt-5.3-codex"]},
                )

        self.assertEqual(response.status_code, 200, msg=response.text)
        self.assertEqual(response.json(), {"chat": chat})
        to_thread.assert_awaited_once()
        start_chat.assert_called_once_with(
            "project-1",
            agent_args=["--model", "gpt-5.3-codex"],
            agent_type="codex",
        )

    def test_project_chat_start_route_uses_configured_default_agent_type_when_not_provided(self) -> None:
        app = self._build_app()
        chat = {"id": "chat-1", "status": "running"}

        with patch(
            "agent_hub.server.asyncio.to_thread",
            new=AsyncMock(side_effect=self._fake_to_thread),
        ) as to_thread, patch.object(
            hub_server.HubState,
            "default_chat_agent_type",
            return_value="claude",
        ) as default_agent_type, patch.object(
            hub_server.HubState,
            "create_and_start_chat",
            return_value=chat,
        ) as start_chat:
            with TestClient(app) as client:
                response = client.post(
                    "/api/projects/project-1/chats/start",
                    json={"agent_args": ["--model", "sonnet"]},
                )

        self.assertEqual(response.status_code, 200, msg=response.text)
        self.assertEqual(response.json(), {"chat": chat})
        to_thread.assert_awaited_once()
        default_agent_type.assert_called_once_with()
        start_chat.assert_called_once_with(
            "project-1",
            agent_args=["--model", "sonnet"],
            agent_type="claude",
        )

    def test_chat_refresh_container_route_calls_state_refresh(self) -> None:
        app = self._build_app()
        chat = {"id": "chat-1", "status": "running"}

        with patch.object(
            hub_server.HubState,
            "refresh_chat_container",
            return_value=chat,
        ) as refresh_chat:
            with TestClient(app) as client:
                response = client.post("/api/chats/chat-1/refresh-container")

        self.assertEqual(response.status_code, 200, msg=response.text)
        self.assertEqual(response.json(), {"chat": chat})
        refresh_chat.assert_called_once_with("chat-1")

    def test_settings_patch_route_updates_default_agent_type(self) -> None:
        app = self._build_app()
        updated_settings = {"default_agent_type": "gemini"}

        with patch.object(
            hub_server.HubState,
            "update_settings",
            return_value=updated_settings,
        ) as update_settings:
            with TestClient(app) as client:
                response = client.patch(
                    "/api/settings",
                    json={"default_agent_type": "gemini"},
                )

        self.assertEqual(response.status_code, 200, msg=response.text)
        self.assertEqual(response.json(), {"settings": updated_settings})
        update_settings.assert_called_once_with({"default_agent_type": "gemini"})

    def test_agent_capabilities_routes_return_cached_and_discovery_payloads(self) -> None:
        app = self._build_app()
        cached_payload = {
            "version": 1,
            "updated_at": "2026-01-02T03:04:05Z",
            "discovery_in_progress": False,
            "discovery_started_at": "",
            "discovery_finished_at": "2026-01-02T03:04:05Z",
            "agents": [
                {
                    "agent_type": "codex",
                    "label": "Codex",
                    "models": ["default", "gpt-6-codex"],
                    "reasoning_modes": ["default", "low", "medium", "high"],
                    "updated_at": "2026-01-02T03:04:05Z",
                    "last_error": "",
                }
            ],
        }
        discovery_payload = {
            **cached_payload,
            "discovery_in_progress": True,
            "discovery_started_at": "2026-01-02T03:10:00Z",
            "discovery_finished_at": "",
        }

        with patch.object(
            hub_server.HubState,
            "agent_capabilities_payload",
            return_value=cached_payload,
        ) as read_capabilities, patch.object(
            hub_server.HubState,
            "start_agent_capabilities_discovery",
            return_value=discovery_payload,
        ) as start_discovery:
            with TestClient(app) as client:
                get_response = client.get("/api/agent-capabilities")
                post_response = client.post("/api/agent-capabilities/discover")

        self.assertEqual(get_response.status_code, 200, msg=get_response.text)
        self.assertEqual(get_response.json(), cached_payload)
        self.assertEqual(post_response.status_code, 200, msg=post_response.text)
        self.assertEqual(post_response.json(), discovery_payload)
        read_capabilities.assert_called_once_with()
        start_discovery.assert_called_once_with()


class DockerEntrypointTests(unittest.TestCase):
    @staticmethod
    def _load_entrypoint_module():
        spec = importlib.util.spec_from_file_location("agent_hub_docker_entrypoint", DOCKER_ENTRYPOINT)
        if spec is None or spec.loader is None:
            raise RuntimeError("Failed to load docker entrypoint module for tests.")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_configure_git_identity_sets_global_git_config(self) -> None:
        module = self._load_entrypoint_module()
        with patch.object(module, "_run", return_value=SimpleNamespace(returncode=0)) as run_mock, patch.dict(
            os.environ,
            {
                "AGENT_HUB_GIT_USER_NAME": "Agent User",
                "AGENT_HUB_GIT_USER_EMAIL": "agentuser@example.com",
            },
            clear=False,
        ):
            module._configure_git_identity()

        self.assertEqual(run_mock.call_count, 2)
        run_mock.assert_has_calls(
            [
                call(["git", "config", "--global", "user.name", "Agent User"]),
                call(["git", "config", "--global", "user.email", "agentuser@example.com"]),
            ]
        )

    def test_configure_git_identity_requires_both_name_and_email(self) -> None:
        module = self._load_entrypoint_module()
        with patch.dict(
            os.environ,
            {
                "AGENT_HUB_GIT_USER_NAME": "Agent User",
                "AGENT_HUB_GIT_USER_EMAIL": "",
            },
            clear=False,
        ):
            with self.assertRaises(RuntimeError):
                module._configure_git_identity()

    def test_prepare_git_credentials_copies_source_and_sets_gh_token(self) -> None:
        module = self._load_entrypoint_module()
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            source_path = tmp_path / "source-credentials"
            target_path = tmp_path / "target-credentials"
            source_path.write_text(
                "https://agentuser:ghp_test_token_123@github.com\n",
                encoding="utf-8",
            )

            with patch.dict(
                os.environ,
                {
                    "AGENT_HUB_GIT_CREDENTIALS_SOURCE": str(source_path),
                    "AGENT_HUB_GIT_CREDENTIALS_FILE": str(target_path),
                    "AGENT_HUB_GIT_CREDENTIAL_HOST": "github.com",
                    "GH_TOKEN": "",
                    "GITHUB_TOKEN": "",
                },
                clear=False,
            ):
                module._prepare_git_credentials()
                self.assertEqual(os.environ.get("GH_TOKEN"), "ghp_test_token_123")
                self.assertEqual(os.environ.get("GITHUB_TOKEN"), "ghp_test_token_123")

            self.assertTrue(target_path.exists())
            self.assertEqual(
                target_path.read_text(encoding="utf-8"),
                source_path.read_text(encoding="utf-8"),
            )
            target_mode = target_path.stat().st_mode & 0o777
            self.assertEqual(target_mode, 0o600)

    def test_prepare_git_credentials_keeps_existing_gh_token(self) -> None:
        module = self._load_entrypoint_module()
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            source_path = tmp_path / "source-credentials"
            target_path = tmp_path / "target-credentials"
            source_path.write_text(
                "https://agentuser:ghp_different_value@github.com\n",
                encoding="utf-8",
            )

            with patch.dict(
                os.environ,
                {
                    "AGENT_HUB_GIT_CREDENTIALS_SOURCE": str(source_path),
                    "AGENT_HUB_GIT_CREDENTIALS_FILE": str(target_path),
                    "AGENT_HUB_GIT_CREDENTIAL_HOST": "github.com",
                    "GH_TOKEN": "already-set-token",
                    "GITHUB_TOKEN": "",
                },
                clear=False,
            ):
                module._prepare_git_credentials()
                self.assertEqual(os.environ.get("GH_TOKEN"), "already-set-token")
                self.assertEqual(os.environ.get("GITHUB_TOKEN"), "already-set-token")

    def test_prepare_git_credentials_sets_gh_host_for_enterprise(self) -> None:
        module = self._load_entrypoint_module()
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            source_path = tmp_path / "source-credentials"
            target_path = tmp_path / "target-credentials"
            source_path.write_text(
                "https://agentuser:ghp_enterprise_token@github.enterprise.local\n",
                encoding="utf-8",
            )

            with patch.dict(
                os.environ,
                {
                    "AGENT_HUB_GIT_CREDENTIALS_SOURCE": str(source_path),
                    "AGENT_HUB_GIT_CREDENTIALS_FILE": str(target_path),
                    "AGENT_HUB_GIT_CREDENTIAL_HOST": "github.enterprise.local",
                    "GH_TOKEN": "",
                    "GITHUB_TOKEN": "",
                    "GH_HOST": "",
                },
                clear=False,
            ):
                module._prepare_git_credentials()
                self.assertEqual(os.environ.get("GH_TOKEN"), "ghp_enterprise_token")
                self.assertEqual(os.environ.get("GH_HOST"), "github.enterprise.local")

    def test_ensure_claude_native_command_path_creates_home_symlink(self) -> None:
        module = self._load_entrypoint_module()
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            home_path = tmp_path / "home"
            source_path = tmp_path / "bin" / "claude"
            source_path.parent.mkdir(parents=True, exist_ok=True)
            source_path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            source_path.chmod(0o755)

            module._ensure_claude_native_command_path(
                command=["claude", "--help"],
                home=str(home_path),
                source_path=source_path,
            )

            target_path = home_path / ".local" / "bin" / "claude"
            self.assertTrue(target_path.is_symlink())
            self.assertEqual(target_path.resolve(), source_path.resolve())

    def test_ensure_claude_native_command_path_fails_when_source_missing(self) -> None:
        module = self._load_entrypoint_module()
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            home_path = tmp_path / "home"
            missing_source = tmp_path / "missing" / "claude"

            with self.assertRaisesRegex(RuntimeError, "source command is missing or not executable"):
                module._ensure_claude_native_command_path(
                    command=["claude"],
                    home=str(home_path),
                    source_path=missing_source,
                )

    def test_entrypoint_main_execs_default_codex_command(self) -> None:
        module = self._load_entrypoint_module()
        with patch.dict(
            os.environ,
            {
                "HOME": "/tmp/entrypoint-home",
                "LOCAL_UMASK": "0022",
            },
            clear=False,
        ), patch.object(module.sys, "argv", ["docker-entrypoint.py"]), patch.object(
            module, "_prepare_git_credentials", return_value=None
        ) as prepare_credentials, patch.object(
            module, "_configure_git_identity", return_value=None
        ) as configure_git, patch.object(
            module.os, "execvp", side_effect=SystemExit(0)
        ) as execvp:
            with self.assertRaises(SystemExit):
                module._entrypoint_main()

        prepare_credentials.assert_called_once_with()
        configure_git.assert_called_once_with()
        execvp.assert_called_once_with("codex", ["codex"])

    def test_entrypoint_main_execs_requested_command(self) -> None:
        module = self._load_entrypoint_module()
        observed_home = ""
        with patch.dict(
            os.environ,
            {
                "HOME": "",
                "LOCAL_HOME": "/tmp/entrypoint-local-home",
                "LOCAL_UMASK": "0022",
            },
            clear=False,
        ), patch.object(module.sys, "argv", ["docker-entrypoint.py", "bash", "-lc", "echo ok"]), patch.object(
            module, "_prepare_git_credentials", return_value=None
        ), patch.object(
            module, "_configure_git_identity", return_value=None
        ), patch.object(
            module.os, "execvp", side_effect=SystemExit(0)
        ) as execvp:
            with self.assertRaises(SystemExit):
                module._entrypoint_main()
            observed_home = str(os.environ.get("HOME") or "")

        self.assertEqual(observed_home, "/tmp/entrypoint-local-home")
        execvp.assert_called_once_with("bash", ["bash", "-lc", "echo ok"])

    def test_entrypoint_main_bootstraps_claude_native_command_path(self) -> None:
        module = self._load_entrypoint_module()
        with patch.dict(
            os.environ,
            {
                "HOME": "/tmp/entrypoint-home",
                "LOCAL_UMASK": "0022",
            },
            clear=False,
        ), patch.object(module.sys, "argv", ["docker-entrypoint.py", "claude", "--help"]), patch.object(
            module, "_ensure_claude_native_command_path", return_value=None
        ) as ensure_claude_native_path, patch.object(
            module, "_prepare_git_credentials", return_value=None
        ), patch.object(
            module, "_configure_git_identity", return_value=None
        ), patch.object(
            module.os, "execvp", side_effect=SystemExit(0)
        ) as execvp:
            with self.assertRaises(SystemExit):
                module._entrypoint_main()

        ensure_claude_native_path.assert_called_once_with(
            command=["claude", "--help"],
            home="/tmp/entrypoint-home",
        )
        execvp.assert_called_once_with("claude", ["claude", "--help"])


if __name__ == "__main__":
    unittest.main()
