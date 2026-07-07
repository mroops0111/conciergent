# Build conciergent with uv, then run it on a slim Python base.
# Includes the [gateway] extra so an OpenAPI spec can be embedded as MCP tools.

FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim AS build

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv

WORKDIR /app

# Install dependencies first, from the lockfile only, so this layer caches across code changes.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --extra gateway

# Then install the project itself.
COPY . .
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-editable --extra gateway


FROM python:3.13-slim-bookworm

# Run as a non-root user.
RUN useradd --create-home --uid 1000 app

COPY --from=build --chown=app:app /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

USER app
WORKDIR /home/app

EXPOSE 8000

# Mount your conciergent.yml at /home/app/conciergent.yml (see docker-compose.yml).
ENTRYPOINT ["conciergent"]
CMD ["run", "--config", "conciergent.yml"]
