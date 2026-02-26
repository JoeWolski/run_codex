ARG BASE_IMAGE=agent-cli-base
FROM ${BASE_IMAGE}
ARG AGENT_PROVIDER=codex
USER root

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
    docker.io \
 && rm -rf /var/lib/apt/lists/*

RUN case "${AGENT_PROVIDER}" in \
      codex) npm install -g @openai/codex ;; \
      claude) curl -fsSL https://claude.ai/install.sh | bash \
              && cp -L /root/.local/bin/claude /usr/local/bin/claude ;; \
      gemini) npm install -g @google/gemini-cli ;; \
      none) echo "Skipping agent CLI install for setup image." ;; \
      *) echo "Unsupported AGENT_PROVIDER='${AGENT_PROVIDER}'" >&2; exit 1 ;; \
    esac \
 && if command -v npm >/dev/null 2>&1; then npm cache clean --force; fi \
 && rm -rf /var/lib/apt/lists/*

RUN if ! id -u agent >/dev/null 2>&1; then \
      useradd --home-dir /home/agent --create-home --shell /bin/bash agent; \
    fi \
 && agent_group="$(id -gn agent)" \
 && mkdir -p /home/agent/projects /home/agent/.cache/uv /home/agent/.codex \
 && chown -R "agent:${agent_group}" /home/agent \
 && mkdir -p /workspace/.cache/uv /workspace/.codex /workspace/.config /workspace/tmp \
 && chown -R agent:${agent_group} /workspace \
 && chmod 777 /workspace \
 && chmod -R 777 /workspace/.cache /workspace/.codex /workspace/.config /workspace/tmp

COPY docker/agent_cli/docker-entrypoint.py /usr/local/bin/docker-entrypoint.py
RUN chmod +x /usr/local/bin/docker-entrypoint.py

WORKDIR /workspace
USER agent
ENTRYPOINT ["/usr/local/bin/docker-entrypoint.py"]
CMD ["codex"]
