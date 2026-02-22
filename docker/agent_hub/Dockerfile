# syntax=docker/dockerfile:1.7

ARG PYTHON_IMAGE=python:3.11-slim-bookworm
ARG NODE_IMAGE=node:20-bookworm-slim

FROM ${NODE_IMAGE} AS frontend-builder
WORKDIR /opt/agent_hub/web
COPY web/package.json web/yarn.lock web/index.html web/vite.config.js ./
COPY web/src ./src
RUN corepack enable \
 && corepack yarn install --frozen-lockfile \
 && corepack yarn build

FROM ${PYTHON_IMAGE} AS production
ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/agent_hub/.venv \
    PATH=/opt/agent_hub/.venv/bin:/usr/local/bin:${PATH}

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
    bash \
    ca-certificates \
    curl \
    docker.io \
    git \
    openssh-client \
    tini \
 && rm -rf /var/lib/apt/lists/*

RUN curl -LsSf https://astral.sh/uv/install.sh -o /tmp/uv-installer.sh \
 && UV_INSTALL_DIR=/usr/local/bin sh /tmp/uv-installer.sh \
 && rm -f /tmp/uv-installer.sh \
 && command -v uv \
 && uv --version

WORKDIR /opt/agent_hub

COPY pyproject.toml uv.lock README.md ./
COPY bin ./bin
COPY config ./config
COPY src ./src
COPY docker/agent_hub/run-agent-hub.sh /usr/local/bin/run-agent-hub.sh
COPY --from=frontend-builder /opt/agent_hub/web/dist ./web/dist

RUN chmod +x /opt/agent_hub/bin/agent_hub /usr/local/bin/run-agent-hub.sh \
 && uv sync --frozen --no-dev

EXPOSE 8765
ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/run-agent-hub.sh"]
CMD []
