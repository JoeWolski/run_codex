# Agent Hub Production Container

This image runs the `agent_hub` control plane with a prebuilt frontend bundle.

It is separate from:

- `docker/agent_cli/Dockerfile`: chat runtime image used by `agent_cli`.
- `docker/development/Dockerfile`: full-project development container image.

## Build

```bash
docker build \
  -f docker/agent_hub/Dockerfile \
  -t agent-hub:prod .
```

## Run

Because `agent_hub` launches nested chat containers through the host Docker daemon, host paths must be reachable by the host daemon with the same absolute path values used inside this container.

```bash
export AGENT_HUB_SHARED_ROOT=/tmp/agent_hub_shared
mkdir -p "${AGENT_HUB_SHARED_ROOT}"

docker run --rm -it \
  -p 8765:8765 \
  -e AGENT_HUB_SHARED_ROOT="${AGENT_HUB_SHARED_ROOT}" \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -v "${AGENT_HUB_SHARED_ROOT}:${AGENT_HUB_SHARED_ROOT}" \
  agent-hub:prod
```

Then open `http://127.0.0.1:8765`.

Entry-point behavior:

- validates Docker socket presence
- validates `AGENT_HUB_SHARED_ROOT` same-path bind mount (unless explicitly bypassed)
- initializes config/data/home under `${AGENT_HUB_SHARED_ROOT}`
- executes `./bin/agent_hub --no-frontend-build`
