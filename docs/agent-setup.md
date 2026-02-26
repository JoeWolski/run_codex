# Agent Setup

## Startup checks

- Confirm Docker CLI and daemon reachability.
- In Docker-in-Docker mode, use daemon-visible host paths for mounts.
- Do not use container-local `/tmp` for mount-backed runtime inputs (`--data-dir`, `--config-file`, `--system-prompt-file`, workspace mounts).

## Validation expectations

- Run targeted build/test checks before handoff.
- Report exact commands with pass/fail.
- Fail fast on hard setup/build/test errors; do not mask failures.

## Git expectations

- Use feature branches.
- Do not push default branch unless explicitly requested.
- Follow repository rebase/commit-shape policy.
- Rebase onto latest default remote branch before final handoff.

## UI evidence expectations

- UI-rendering changes require refreshed evidence from real app + real backend.
- Keep screenshots untracked; attach to PR.
- Ensure PR images match current head commit and are free of unrelated auth/setup failures.
