# GharBasket API image (ADR-0020). Built by Render from the repository root:
#   docker build -f backend/api/Dockerfile -t gharbasket-api .
# Migrations are not run here: the deploy workflow runs them on Neon before triggering the deploy.

FROM python:3.13-slim AS build
COPY --from=ghcr.io/astral-sh/uv:0.12.22 /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0
WORKDIR /app

# Dependencies first (cached until the lockfile changes), from the workspace's frozen lock.
COPY pyproject.toml uv.lock ./
COPY backend/api/pyproject.toml backend/api/README.md backend/api/
RUN uv sync --locked --no-dev --package gharbasket-api --no-install-workspace

# Then the API itself, installed into the virtualenv (not editable), so the source isn't needed.
COPY backend/api/src backend/api/src
RUN uv sync --locked --no-dev --package gharbasket-api --no-editable


FROM python:3.13-slim
# Render passes its RENDER_GIT_COMMIT to the build; baked in, /v1/health can report the commit.
ARG RENDER_GIT_COMMIT
ENV GIT_COMMIT=${RENDER_GIT_COMMIT}
RUN useradd --create-home --uid 10001 app
COPY --from=build --chown=app:app /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    APP_ENV=prod \
    LOG_JSON=true
USER app
WORKDIR /app
EXPOSE 8000

# Render sets $PORT. Behind its proxy, --proxy-headers makes request.client the real caller
# (rate limits and logs depend on it). One worker fits the free 512 MB instance.
CMD ["sh", "-c", "exec uvicorn gharbasket.main:create_app --factory --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips='*' --no-access-log"]
