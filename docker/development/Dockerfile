# syntax=docker/dockerfile:1.7

ARG UV_DISTROLESS_IMAGE=ghcr.io/astral-sh/uv:latest
FROM ${UV_DISTROLESS_IMAGE} AS uv_distroless

FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    PATH=/opt/agent_hub/.venv/bin:/usr/local/bin:${PATH}

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
    python-is-python3 \
    python3 \
    python3-pip \
    python3-venv \
    tini \
    xauth \
    xdotool \
    xvfb \
 && rm -rf /var/lib/apt/lists/*

COPY --from=uv_distroless /uv /uvx /usr/local/bin/

RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
 && apt-get update \
 && apt-get install -y --no-install-recommends nodejs \
 && npm cache clean --force \
 && rm -rf /var/lib/apt/lists/*

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
      /opt/agent_hub/docker/development/verify-demo-tooling.sh \
 && UV_PROJECT_ENVIRONMENT=/opt/agent_hub/.venv uv sync --frozen --no-dev \
 && cd /opt/agent_hub/web \
 && corepack yarn install --frozen-lockfile \
 && cd /opt/agent_hub/tools/demo \
 && npm ci \
 && npx playwright install --with-deps firefox \
 && /opt/agent_hub/docker/development/verify-demo-tooling.sh

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["bash"]
