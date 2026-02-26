# Agent CLI Runtime Container

This image is the chat runtime used by `agent_cli` for per-session containers.

`agent_cli` builds this Dockerfile automatically by default, so manual builds are usually unnecessary.

## Build Examples

```bash
# Build base first
docker build \
  -f docker/agent_cli/Dockerfile.base \
  -t agent-cli-base:latest .

# Codex runtime
docker build \
  -f docker/agent_cli/Dockerfile \
  --build-arg AGENT_PROVIDER=codex \
  --build-arg BASE_IMAGE=agent-cli-base:latest \
  -t agent-ubuntu2204-codex:latest .

# Claude runtime
docker build \
  -f docker/agent_cli/Dockerfile \
  --build-arg AGENT_PROVIDER=claude \
  --build-arg BASE_IMAGE=agent-cli-base:latest \
  -t agent-ubuntu2204-claude:latest .

# Gemini runtime
docker build \
  -f docker/agent_cli/Dockerfile \
  --build-arg AGENT_PROVIDER=gemini \
  --build-arg BASE_IMAGE=agent-cli-base:latest \
  -t agent-ubuntu2204-gemini:latest .

# Setup-only runtime (no provider CLI install)
docker build \
  -f docker/agent_cli/Dockerfile \
  --build-arg AGENT_PROVIDER=none \
  --build-arg BASE_IMAGE=agent-cli-base:latest \
  -t agent-ubuntu2204-setup:latest .
```

## Notes

- The image entrypoint is `docker/agent_cli/docker-entrypoint.py`.
- Default runtime home/workspace root is `/workspace`.
- Hub-launched chats get the `agent_tools` MCP runtime with `submit_artifact` for durable artifact uploads.
- Docker build layers run as `USER root` to keep package installation deterministic even when a setup snapshot was committed from a non-root runtime user.
- Setup snapshot commits normalize image metadata (`USER`, `WORKDIR`, `ENTRYPOINT`, `CMD`) before provider overlays are built.
- Runtime user identity is provided by `docker run --user <uid>:<gid>` and `--group-add`, not by mutating `/etc/passwd` at container startup.
