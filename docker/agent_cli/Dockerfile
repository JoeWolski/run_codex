ARG BASE_IMAGE=nvidia/cuda:12.2.2-cudnn8-devel-ubuntu22.04
FROM ${BASE_IMAGE}
ARG AGENT_PROVIDER=codex

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
    bash \
    ca-certificates \
    curl \
    gh \
    gosu \
    git \
    less \
    openssh-client \
    python3 \
    passwd \
    sudo \
 && rm -rf /var/lib/apt/lists/*

RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
 && apt-get update \
 && apt-get install -y --no-install-recommends nodejs \
 && case "${AGENT_PROVIDER}" in \
      codex) npm install -g @openai/codex ;; \
      claude) npm install -g @anthropic-ai/claude-code ;; \
      gemini) npm install -g @google/gemini-cli ;; \
      none) echo "Skipping agent CLI install for setup image." ;; \
      *) echo "Unsupported AGENT_PROVIDER='${AGENT_PROVIDER}'" >&2; exit 1 ;; \
    esac \
 && npm cache clean --force \
 && rm -rf /var/lib/apt/lists/*

RUN if ! id -u agent >/dev/null 2>&1; then \
      groupadd --gid 1000 agent || true; \
      useradd --uid 1000 --gid 1000 --home-dir /home/agent --create-home --shell /bin/bash agent; \
    fi \
 && mkdir -p /home/agent/projects /home/agent/.cache/uv /home/agent/.codex \
 && chown -R agent:agent /home/agent

COPY docker/agent_cli/docker-entrypoint.py /usr/local/bin/docker-entrypoint.py
COPY docker/agent_cli/hub_artifact /usr/local/bin/hub_artifact
RUN chmod +x /usr/local/bin/docker-entrypoint.py /usr/local/bin/hub_artifact

WORKDIR /home/agent/projects
ENTRYPOINT ["/usr/local/bin/docker-entrypoint.py"]
CMD ["codex"]
