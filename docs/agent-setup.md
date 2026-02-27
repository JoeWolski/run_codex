# Agent Setup

## Startup checks

- Confirm Docker CLI and daemon reachability.
- In Docker-in-Docker mode, use daemon-visible host paths for mounts.
- Do not use container-local `/tmp` for mount-backed runtime inputs (`--data-dir`, `--config-file`, `--system-prompt-file`, workspace mounts).
- `agent_hub` now provisions runtime temp mounts under the hub data directory:
  - project snapshot/build: `<data-dir>/tmp/projects/<project-id>/workspace -> /workspace/tmp`
  - chat runtime: `<data-dir>/tmp/projects/<project-id>/chats/<chat-id> -> /workspace/tmp`
- For nested Docker workflows, use `AGENT_HUB_TMP_HOST_PATH` (injected into runtime containers) as the daemon-visible host source when launching inner containers.

## First-run deterministic integration flags (Docker-in-Docker)

- Start hub with `--host 0.0.0.0`.
- Set `--artifact-publish-base-url` to a host/IP reachable from runtime containers in this daemon namespace.
- Keep runtime inputs under daemon-visible paths, preferably `/workspace/tmp`.

Example:

```bash
HUB_IP="$(hostname -I | awk '{print $1}')"
mkdir -p /workspace/tmp/agent-hub-inputs
cp config/agent.config.toml /workspace/tmp/agent-hub-inputs/agent.config.toml
cp SYSTEM_PROMPT.md /workspace/tmp/agent-hub-inputs/SYSTEM_PROMPT.md

HOME=/workspace/tmp/agent-hub-home \
uv run agent_hub \
  --host 0.0.0.0 \
  --port 8876 \
  --artifact-publish-base-url "http://${HUB_IP}:8876" \
  --data-dir /workspace/tmp/agent-hub-data \
  --config-file /workspace/tmp/agent-hub-inputs/agent.config.toml \
  --system-prompt-file /workspace/tmp/agent-hub-inputs/SYSTEM_PROMPT.md \
  --no-frontend-build
```

Run setup preflight:

```bash
uv run python tools/testing/preflight_integration_env.py
```

Run integration with preflight:

```bash
uv run python tools/testing/run_integration.py --mode hub-api-e2e --preflight --dry-run
```

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
