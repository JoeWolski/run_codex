# Agent Hub

## Why use this instead of the OpenAI Codex web UI?

If you want full control over runtime, performance, and data locality, this project is built for that.

Compared with the hosted Codex web UI, Agent Hub lets you:

- Run agents on your own local hardware (including powerful multi-GPU workstations).
- Use your own Docker images, setup scripts, and mount topology.
- Keep code, secrets, datasets, and build caches on your machine.
- Reuse deterministic per-project snapshot images so new chats start from a known-good environment.
- Launch many chats quickly with isolated sandboxes (one workspace/runtime per chat) and optimistic UI creation.
- Use first-class file workflows: publish files from a chat, preview/download them in the UI, and keep artifact history tied to prompts.

In short: this is a local-first orchestration layer for Codex-style workflows, not just a chat window.

## What this project provides

This repo has two main tools:

- `agent_cli`: launches a containerized Codex runtime for a project.
- `agent_hub`: runs a local web control plane (FastAPI + React) for projects, chat sessions, auth, snapshots, and artifacts.

Core capabilities include:

- Project onboarding from Git URLs.
- Project setup snapshots (`--setup-script` + `--snapshot-image-tag`) with cached reuse.
- Base image selection by Docker tag or repo path to Dockerfile/directory.
- Per-project default volumes/env vars and per-chat overrides.
- Terminal streaming over WebSocket.
- OpenAI auth (API key and ChatGPT account login flows).
- GitHub App auth for in-container git credentials.
- Artifact publishing with secure per-chat tokens, preview, and download links.

## Architecture at a glance

- Backend: `src/agent_hub/server.py` (FastAPI + state manager + process orchestration).
- Runtime launcher: `src/agent_cli/cli.py` (Docker build/run + mount/env plumbing).
- Frontend: `web/` (React + Vite + xterm.js).
- Runtime image: `docker/Dockerfile` + `docker/docker-entrypoint.py`.
- Artifact client command inside containers: `docker/hub_artifact`.

## Requirements

- Linux/macOS with Docker CLI available.
- Docker daemon reachable from where you run `agent_cli`/`agent_hub`.
- Python 3.11+.
- `uv` (recommended launcher in this repo).
- Node.js + Corepack (only needed when frontend build is required).
- Optional NVIDIA GPU + `nvidia-container-toolkit` if you want GPU passthrough.

## Quick start

1. Start the hub:

```bash
uv run agent_hub
```

2. Open:

```text
http://127.0.0.1:8765
```

3. Add a project in the UI:
- repo URL
- optional base image source
- optional setup script
- optional default mounts/env vars

4. Wait for project image build to reach `Ready`, then create chats.

Notes:

- `agent_hub` auto-builds the frontend when needed.
- Wrapper scripts are also available:
  - `bin/agent_hub`
  - `bin/agent_cli`

## Parallel chats and sandboxing

Agent Hub is designed for high-throughput chat workflows:

- New chats can be created back-to-back with optimistic UI rows.
- Chat start requests are queue-managed per project to avoid conflicting setup operations.
- Different projects can start chats concurrently.
- Every chat gets its own workspace directory and runtime process, so sessions are isolated.

This gives fast multi-chat workflow without cross-chat state collisions.

## First-class file support

Inside a running chat container, publish generated files with:

```bash
hub_artifact publish <path> [<path> ...]
```

Optional:

```bash
hub_artifact publish report.md --name "Final Report"
```

Behavior:

- Accepts individual files or a flat directory (no subdirectories).
- Retries failed uploads with backoff.
- Retries only failed files, not files already uploaded.
- Registers artifacts in chat state with metadata.
- UI provides download links and image/video preview where applicable.
- Artifact history is preserved per prompt context.

## Running `agent_cli` directly

Minimal run:

```bash
uv run agent_cli --project /path/to/project
```

Common examples:

```bash
# Resume the last Codex session
uv run agent_cli --project /path/to/project --resume

# Use custom base Dockerfile
uv run agent_cli --project /path/to/project --base /path/to/base/Dockerfile

# Add mounts and env vars
uv run agent_cli \
  --project /path/to/project \
  --ro-mount /mnt/datasets:/mnt/datasets \
  --rw-mount /var/cache/build:/var/cache/build \
  --env-var WANDB_MODE=offline
```

## Docker-in-Docker and networking notes

This stack launches Docker containers from inside tools that may themselves run in a containerized/dev environment.

Critical assumptions:

- Docker socket access must be available (`/var/run/docker.sock`).
- Host mount paths must be valid from the Docker daemon host perspective.
- Artifact callback URL must be reachable from chat containers.

By default, artifact publish URL resolves to:

```text
http://host.docker.internal:<hub-port>
```

Override when needed:

```bash
uv run agent_hub --artifact-publish-base-url http://<reachable-host>:8765
```

On Linux, chat runtime adds:

```text
host.docker.internal:host-gateway
```

to improve host reachability from containers.

## Authentication

In `Settings` tab:

- OpenAI:
  - API key connect/disconnect (optional verification against OpenAI API).
  - ChatGPT account login (browser callback or device auth flow).
- GitHub:
  - GitHub App manifest setup.
  - Installation connect/disconnect.
  - Short-lived token-backed git credential injection for chat runtimes.

Secrets are stored server-side in the hub data directory with restricted file permissions.

## Validation commands

Backend + CLI tests:

```bash
uv run python -m unittest discover -s tests -v
```

Frontend build check:

```bash
cd web
corepack yarn install
corepack yarn build
```

## Repo map

- `src/agent_hub/server.py`: hub backend and API routes.
- `src/agent_cli/cli.py`: runtime launcher CLI.
- `web/src/App.jsx`: UI behavior (projects/chats/settings/artifacts).
- `docker/Dockerfile`: runtime image with Codex CLI and helper scripts.
- `docker/hub_artifact`: artifact publish utility available in chat containers.
- `tests/`: unit/integration coverage for hub, CLI, and artifact command behavior.

