# Multi-stage image for the API + worker (same image, different command).
FROM node:22-slim AS ui
WORKDIR /ui
RUN corepack enable
COPY frontend/package.json frontend/pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile
COPY frontend/ ./
RUN pnpm build

FROM python:3.13-slim
RUN apt-get update && apt-get install -y --no-install-recommends postgresql-client && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
WORKDIR /app/backend
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY backend/ ./
RUN uv sync --frozen --no-dev
COPY --from=ui /ui/dist /app/frontend/dist
ENV HUB_FRONTEND_DIST=/app/frontend/dist HUB_DATA_DIR=/app/data PATH="/app/backend/.venv/bin:$PATH"
RUN useradd -m hub && mkdir -p /app/data && chown -R hub /app/data
USER hub
CMD ["hub", "serve"]
