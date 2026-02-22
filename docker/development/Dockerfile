# syntax=docker/dockerfile:1.7

ARG PYTHON_IMAGE=python:3.11-slim-bookworm
FROM ${PYTHON_IMAGE}

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/agent_hub/.venv \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    PATH=/opt/agent_hub/.venv/bin:/root/.local/bin:${PATH}

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
    bash \
    ca-certificates \
    curl \
    docker.io \
    ffmpeg \
    git \
    jq \
    openssh-client \
    tini \
    xauth \
    xdotool \
    xvfb \
 && rm -rf /var/lib/apt/lists/*

RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
 && apt-get update \
 && apt-get install -y --no-install-recommends nodejs \
 && npm cache clean --force \
 && rm -rf /var/lib/apt/lists/*

RUN curl -LsSf https://astral.sh/uv/install.sh | sh

RUN if ! command -v corepack >/dev/null 2>&1; then npm install -g corepack@0.31.0; fi \
 && corepack enable

WORKDIR /opt/agent_hub

COPY pyproject.toml uv.lock README.md ./
COPY bin ./bin
COPY config ./config
COPY docker ./docker
COPY src ./src
COPY tests ./tests
COPY tools/demo ./tools/demo
COPY web ./web

RUN chmod +x \
      /opt/agent_hub/bin/agent_cli \
      /opt/agent_hub/bin/agent_hub \
      /opt/agent_hub/docker/agent_cli/hub_artifact \
 && uv sync --frozen --no-dev \
 && cd /opt/agent_hub/web \
 && corepack yarn install --frozen-lockfile \
 && cd /opt/agent_hub/tools/demo \
 && npm ci \
 && npx playwright install --with-deps firefox

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["bash"]
