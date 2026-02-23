# Agent CLI Runtime Container

This image is the chat runtime used by `agent_cli` for per-session containers.

`agent_cli` builds this Dockerfile automatically by default, so manual builds are usually unnecessary.

## Build Examples

```bash
# Codex runtime
docker build \
  -f docker/agent_cli/Dockerfile \
  --build-arg AGENT_PROVIDER=codex \
  -t agent-ubuntu2204-codex:latest .

# Claude runtime
docker build \
  -f docker/agent_cli/Dockerfile \
  --build-arg AGENT_PROVIDER=claude \
  -t agent-ubuntu2204-claude:latest .

# Gemini runtime
docker build \
  -f docker/agent_cli/Dockerfile \
  --build-arg AGENT_PROVIDER=gemini \
  -t agent-ubuntu2204-gemini:latest .

# Setup-only runtime (no provider CLI install)
docker build \
  -f docker/agent_cli/Dockerfile \
  --build-arg AGENT_PROVIDER=none \
  -t agent-ubuntu2204-setup:latest .
```

## Notes

- The image entrypoint is `docker/agent_cli/docker-entrypoint.py`.
- `hub_artifact` is installed into `/usr/local/bin/hub_artifact` for artifact publishing in hub-launched chats.
- Docker build layers run as `USER root` to keep package installation deterministic even when a setup snapshot was committed from a non-root runtime user.
- Runtime user identity is provided by `docker run --user <uid>:<gid>` and `--group-add`, not by mutating `/etc/passwd` at container startup.
