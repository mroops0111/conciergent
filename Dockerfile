# Build conciergent with uv, then run it on a slim Python base.
# Includes the [gateway] extra so an OpenAPI spec can be embedded as MCP tools,
# and the [charts] extra so the agent can answer with a chart.

FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim AS build

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv

WORKDIR /app

# Install dependencies first, from the lockfile only, so this layer caches across code changes.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --extra gateway --extra charts

# Then install the project itself.
COPY . .
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-editable --extra gateway --extra charts


FROM python:3.13-slim-bookworm

# The slim base has no fonts, and a chart whose font is missing draws no text at all.
# Noto Sans CJK covers Latin as well as Chinese, Japanese, and Korean.
# Charts draw in sans only, so the package's serif faces are removed.
RUN apt-get update \
    && apt-get install --yes --no-install-recommends fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/* /usr/share/fonts/opentype/noto/NotoSerifCJK-*

# Run as a non-root user.
RUN useradd --create-home --uid 1000 app

COPY --from=build --chown=app:app /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

USER app
WORKDIR /home/app

EXPOSE 8000

# Mount your manifest.yml at /home/app/manifest.yml (see docker-compose.yml).
ENTRYPOINT ["conciergent"]
CMD ["run", "--config", "manifest.yml"]
