from __future__ import annotations

import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INSTALL_SCRIPT = ROOT / "bin" / "install"


class InstallScriptTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.temp_path = Path(self.temp_dir.name)
        self.home = self.temp_path / "home"
        self.home.mkdir(parents=True, exist_ok=True)

        self.env = dict(os.environ)
        self.env["HOME"] = str(self.home)
        self.env["XDG_CACHE_HOME"] = str(self.home / ".cache")

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _run_install(
        self,
        *args: str,
        env_overrides: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        env = dict(self.env)
        if env_overrides:
            env.update(env_overrides)
        return subprocess.run(
            [str(INSTALL_SCRIPT), *args],
            cwd=str(ROOT),
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )

    def test_requires_update_method_positional_argument(self) -> None:
        result = self._run_install()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Usage: ./bin/install [--add-path | --skip-add-path] <Update Method>", result.stderr)

    def test_rejects_invalid_update_method(self) -> None:
        result = self._run_install("invalid")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Update Method must be one of: none, head", result.stderr)

    def test_rejects_conflicting_add_path_flags(self) -> None:
        result = self._run_install("--add-path", "--skip-add-path", "none")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--add-path and --skip-add-path cannot be used together", result.stderr)

    def test_installs_launchers_with_none_update_method(self) -> None:
        result = self._run_install("--skip-add-path", "none")
        self.assertEqual(result.returncode, 0, msg=result.stderr)

        managed_repo = self.home / ".local" / "share" / "agent_hub" / "repo"
        agent_cli_path = self.home / ".local" / "bin" / "agent_cli"
        agent_hub_path = self.home / ".local" / "bin" / "agent_hub"
        self.assertTrue(managed_repo.exists())
        self.assertTrue((managed_repo / ".git").exists())
        self.assertTrue(agent_cli_path.exists())
        self.assertTrue(agent_hub_path.exists())
        self.assertTrue(agent_cli_path.stat().st_mode & stat.S_IXUSR)
        self.assertTrue(agent_hub_path.stat().st_mode & stat.S_IXUSR)

        cli_content = agent_cli_path.read_text(encoding="utf-8")
        hub_content = agent_hub_path.read_text(encoding="utf-8")
        self.assertIn('UPDATE_METHOD=none', cli_content)
        self.assertIn('UPDATE_METHOD=none', hub_content)
        self.assertIn('TOOL_NAME=agent_cli', cli_content)
        self.assertIn('TOOL_NAME=agent_hub', hub_content)
        self.assertIn(f'SOURCE_REPO_ROOT={managed_repo}', cli_content)
        self.assertIn(f'SOURCE_REPO_ROOT={managed_repo}', hub_content)
        self.assertNotIn(f'SOURCE_REPO_ROOT={ROOT}', cli_content)
        self.assertIn('exec "${repo_root}/bin/${TOOL_NAME}" "$@"', cli_content)

    def test_installs_launchers_with_head_update_method(self) -> None:
        result = self._run_install("--skip-add-path", "head")
        self.assertEqual(result.returncode, 0, msg=result.stderr)

        managed_repo = self.home / ".local" / "share" / "agent_hub" / "repo"
        agent_cli_path = self.home / ".local" / "bin" / "agent_cli"
        self.assertTrue(managed_repo.exists())
        self.assertTrue(agent_cli_path.exists())

        cli_content = agent_cli_path.read_text(encoding="utf-8")
        self.assertIn('UPDATE_METHOD=head', cli_content)
        self.assertIn(f'SOURCE_REPO_ROOT={managed_repo}', cli_content)
        self.assertIn('worktree_is_clean() {', cli_content)
        self.assertIn('git -C "${worktree_path}" rev-parse --verify HEAD^{commit} 2>/dev/null', cli_content)
        self.assertIn('git -C "${worktree_path}" diff --no-ext-diff --quiet --exit-code', cli_content)
        self.assertIn('git -C "${worktree_path}" diff --no-ext-diff --cached --quiet --exit-code', cli_content)
        self.assertIn('git -C "${worktree_path}" ls-files --others --exclude-standard', cli_content)
        self.assertIn('if ! worktree_is_clean "${worktree_path}" "${target_commit}"; then', cli_content)
        self.assertIn('git -C "${SOURCE_REPO_ROOT}" fetch --quiet --prune origin "${default_branch}"', cli_content)
        self.assertIn(
            'git -C "${SOURCE_REPO_ROOT}" worktree add --quiet --detach "${worktree_path}" "${target_commit}"',
            cli_content,
        )

    def test_add_path_updates_rc_file_for_supported_shells(self) -> None:
        cases = [
            ("/bin/bash", ".bashrc"),
            ("/bin/zsh", ".zshrc"),
            ("/bin/sh", ".profile"),
        ]
        for shell_path, rc_name in cases:
            with self.subTest(shell=shell_path):
                shell_home = self.temp_path / rc_name.replace(".", "home_")
                shell_home.mkdir(parents=True, exist_ok=True)
                result = self._run_install(
                    "--add-path",
                    "none",
                    env_overrides={
                        "HOME": str(shell_home),
                        "XDG_CACHE_HOME": str(shell_home / ".cache"),
                        "PATH": "/usr/bin:/bin",
                        "SHELL": shell_path,
                    },
                )
                self.assertEqual(result.returncode, 0, msg=result.stderr)
                rc_file = shell_home / rc_name
                self.assertTrue(rc_file.exists())
                content = rc_file.read_text(encoding="utf-8")
                self.assertIn('export PATH="$HOME/.local/bin:$PATH"', content)

    def test_skip_add_path_does_not_touch_shell_rc_file(self) -> None:
        result = self._run_install(
            "--skip-add-path",
            "none",
            env_overrides={
                "PATH": "/usr/bin:/bin",
                "SHELL": "/bin/bash",
            },
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertFalse((self.home / ".bashrc").exists())

    def test_add_path_fails_for_unsupported_shell(self) -> None:
        result = self._run_install(
            "--add-path",
            "none",
            env_overrides={
                "PATH": "/usr/bin:/bin",
                "SHELL": "/bin/fish",
            },
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Supported shells for PATH updates are: sh, bash, zsh.", result.stderr)

    def test_prompt_mode_skips_path_update_when_stdin_is_not_interactive(self) -> None:
        result = self._run_install(
            "none",
            env_overrides={
                "PATH": "/usr/bin:/bin",
                "SHELL": "/bin/bash",
            },
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("Re-run with --add-path or --skip-add-path.", result.stderr)
        self.assertFalse((self.home / ".bashrc").exists())


if __name__ == "__main__":
    unittest.main()
