# syntax=docker/dockerfile:1.7

ARG PYTHON_IMAGE=python:3.11-slim-bookworm
FROM ${PYTHON_IMAGE}

ARG DEV_USER=agent
ARG DEV_UID=1000
ARG DEV_GID=1000

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/agent_hub/.venv \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

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

RUN curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin sh

RUN if ! command -v corepack >/dev/null 2>&1; then npm install -g corepack@0.31.0; fi \
 && corepack enable

RUN set -eux; \
    if ! getent group "${DEV_GID}" >/dev/null; then \
      groupadd --gid "${DEV_GID}" "${DEV_USER}"; \
    fi; \
    if getent passwd "${DEV_UID}" >/dev/null; then \
      existing_user="$(getent passwd "${DEV_UID}" | cut -d: -f1)"; \
      if [ "${existing_user}" != "${DEV_USER}" ]; then \
        echo "DEV_UID ${DEV_UID} already exists as user ${existing_user}. Use a different DEV_UID or DEV_USER." >&2; \
        exit 1; \
      fi; \
    elif getent passwd "${DEV_USER}" >/dev/null; then \
      usermod --uid "${DEV_UID}" --gid "${DEV_GID}" "${DEV_USER}"; \
    else \
      useradd --uid "${DEV_UID}" --gid "${DEV_GID}" --home-dir "/home/${DEV_USER}" --create-home --shell /bin/bash "${DEV_USER}"; \
    fi; \
    mkdir -p "/home/${DEV_USER}" /opt/agent_hub; \
    chown -R "${DEV_UID}:${DEV_GID}" "/home/${DEV_USER}" /opt/agent_hub

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
 && npx playwright install --with-deps firefox \
 && chown -R "${DEV_UID}:${DEV_GID}" /opt/agent_hub /ms-playwright

ENV HOME=/home/${DEV_USER}
USER ${DEV_UID}:${DEV_GID}

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["bash"]
