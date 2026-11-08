# syntax=docker/dockerfile:1
FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.13.6 /uv /usr/local/bin/uv
ENV SERVICE_WEB_DIR=/app/web SERVICE_TOKENS_FILE=/data/tokens.json PYTHONUNBUFFERED=1 UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PROJECT_ENVIRONMENT=/opt/venv PATH="/opt/venv/bin:$PATH" HF_HOME=/models SERVICE_HOST=0.0.0.0 SERVICE_PORT=29321
WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev
COPY src ./src
COPY web ./web

EXPOSE 29321
CMD ["python", "src/app.py"]
