# Project Development Container

This image is a full-project development environment for `agent_hub`.

It installs tooling needed for development and demo workflows, including:

- `uv`
- Docker CLI/daemon package (`docker.io`)
- Node.js + Corepack
- Playwright Firefox browser + dependencies
- `ffmpeg`, `jq`, `xvfb`, `xdotool`, `xauth`
- Build-time verification script for screenshot/video capture readiness (`docker/development/verify-demo-tooling.sh`)

Unlike the production `agent_hub` image, this container does not pre-build the frontend bundle.

## Build

```bash
docker build \
  -f docker/development/Dockerfile \
  -t agent-hub:dev .
```

The build runs `docker/development/verify-demo-tooling.sh` and fails fast if any demo capture dependency is missing.

## Run

```bash
docker run --rm -it \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -v "$(pwd):/opt/agent_hub" \
  -w /opt/agent_hub \
  agent-hub:dev
```

Because this environment may launch nested Docker containers, the mounted project path should be host-reachable with the same absolute path when possible.

## Verify Tooling Manually

Run this inside the dev container to re-check screenshot/video prerequisites after local changes:

```bash
bash docker/development/verify-demo-tooling.sh
```
