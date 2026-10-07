# syntax=docker/dockerfile:1.7
FROM ghcr.io/astral-sh/uv:0.11.7 AS uv

FROM node:22.22.0-bookworm-slim AS codex-cli
ARG CODEX_CLI_VERSION=0.154.0-alpha.6.2
RUN npm install --global "@openai/codex@${CODEX_CLI_VERSION}"

FROM python:3.12.2-slim AS python-workspace
COPY --from=uv /uv /uvx /bin/
WORKDIR /app

COPY pyproject.toml uv.lock README.md ./
COPY apps/context_service ./apps/context_service
COPY packages/auth_py ./packages/auth_py
COPY packages/context_client_py ./packages/context_client_py
COPY packages/context_core_py ./packages/context_core_py
COPY packages/contracts_py ./packages/contracts_py
COPY packages/database_py ./packages/database_py
COPY packages/skills_py ./packages/skills_py
COPY packages/runtime_genkit_py ./packages/runtime_genkit_py
COPY packages/runtime_py ./packages/runtime_py
COPY extension ./extension

RUN uv sync --locked --no-dev --all-packages

FROM python-workspace AS team-context
ENV CONTENT_PATH=/app/extension \
    CONTENT_REVISION=local-example \
    HTTP_HOST=0.0.0.0 \
    HTTP_PORT=3000 \
    PATH=/app/.venv/bin:$PATH
RUN useradd --create-home --uid 10001 app
USER app
EXPOSE 3000
HEALTHCHECK --interval=5s --timeout=3s --retries=20 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:3000/ready')"]
CMD ["team-context-service"]

FROM python-workspace AS coding-runner
ENV PATH=/app/.venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    CODEX_HOME=/home/runner/.codex
COPY --from=codex-cli /usr/local/bin/node /usr/local/bin/node
COPY --from=codex-cli /usr/local/lib/node_modules/@openai/codex /usr/local/lib/node_modules/@openai/codex
RUN ln -s /usr/local/lib/node_modules/@openai/codex/bin/codex.js /usr/local/bin/codex \
    && apt-get update \
    && apt-get install --yes --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir -p /opt/team-agent
RUN useradd --create-home --uid 10002 runner
USER runner
ENTRYPOINT []
CMD ["python", "-m", "team_agent_runtime.coding_runner"]
