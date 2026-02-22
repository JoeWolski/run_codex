# Project Development Container

This image is a full-project development environment for `agent_hub`.

It installs tooling needed for development and demo workflows, including:

- `uv`
- Docker CLI/daemon package (`docker.io`)
- Node.js + Corepack
- Playwright Firefox browser + dependencies
- `ffmpeg`, `jq`, `xvfb`, `xdotool`, `xauth`

Unlike the production `agent_hub` image, this container does not pre-build the frontend bundle.

## Build

```bash
docker build \
  -f docker/development/Dockerfile \
  --build-arg DEV_USER="$(id -un)" \
  --build-arg DEV_UID="$(id -u)" \
  --build-arg DEV_GID="$(id -g)" \
  -t agent-hub:dev .
```

## Run

```bash
SOCK_GID="$(stat -c '%g' /var/run/docker.sock 2>/dev/null || stat -f '%g' /var/run/docker.sock)"

docker run --rm -it \
  --group-add "${SOCK_GID}" \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -v "$(pwd):/opt/agent_hub" \
  -e AGENT_HUB_SHARED_ROOT=/tmp/agent_hub_shared \
  -v /tmp/agent_hub_shared:/tmp/agent_hub_shared \
  -w /opt/agent_hub \
  agent-hub:dev
```

Because this environment may launch nested Docker containers, the mounted project path should be host-reachable with the same absolute path when possible.
For nested `agent_hub` use cases, `AGENT_HUB_SHARED_ROOT` should be mounted at the same absolute path on host and in-container.
