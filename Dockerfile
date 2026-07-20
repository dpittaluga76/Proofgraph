# syntax=docker/dockerfile:1.7

FROM node:22-alpine AS web-builder
WORKDIR /build/web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

FROM python:3.13-slim AS python-builder
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy
WORKDIR /build
RUN python -m pip install --no-cache-dir uv==0.11.24
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

FROM python:3.13-slim AS runtime
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DJANGO_SETTINGS_MODULE=proofgraph.settings
WORKDIR /app

RUN addgroup --system proofgraph \
    && adduser --system --ingroup proofgraph --home /app proofgraph

COPY --chown=proofgraph:proofgraph --from=python-builder /build/.venv /app/.venv
COPY --chown=proofgraph:proofgraph manage.py ./
COPY --chown=proofgraph:proofgraph proofgraph/ ./proofgraph/
COPY --chown=proofgraph:proofgraph fixtures/ ./fixtures/
COPY --chown=proofgraph:proofgraph --from=web-builder /build/web/dist/ ./web/dist/

USER proofgraph

CMD ["python", "-m", "uvicorn", "proofgraph.asgi:application", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
