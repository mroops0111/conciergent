import os
import pathlib
import re
import typing

import pydantic
import yaml


class AgentSettings(pydantic.BaseModel):
    """The batteries-included agent, a model plus a prompt plus MCP server URLs."""

    model: str
    system_prompt: str
    mcp_servers: list[str] = pydantic.Field(default_factory=list)
    input_token_limit: int | None = None


class SlackSettings(pydantic.BaseModel):
    """Slack app credentials, created once in the Slack app dashboard.

    An empty secret would make every webhook signature forgeable, so required fields reject it,
    catching an unset environment variable at startup instead.
    """

    signing_secret: typing.Annotated[str, pydantic.Field(min_length=1)]
    client_id: str = ''
    client_secret: str = ''
    scopes: list[str] = pydantic.Field(
        default_factory=lambda: ['chat:write', 'im:history', 'im:read', 'im:write', 'users:read']
    )
    bot_token: str = ''
    text_formatting_instruction: str = ''


class LineSettings(pydantic.BaseModel):
    """LINE Messaging API channel credentials, created once in the LINE developers console.

    An empty secret would make every webhook signature forgeable, so both fields reject it,
    catching an unset environment variable at startup instead.
    """

    channel_secret: typing.Annotated[str, pydantic.Field(min_length=1)]
    channel_access_token: typing.Annotated[str, pydantic.Field(min_length=1)]
    welcome_text: str = ''
    ready_text: str = ''
    text_formatting_instruction: str = ''


class StoreSettings(pydantic.BaseModel):
    """Which state backend to run on, the in-memory default needs no infrastructure.

    The composite type splits by sensitivity, message-bearing state (history, approvals, dedupe,
    OAuth handoff) goes to ``messages`` and long-lived credentials go to ``credentials``,
    so conversations stay on expiring storage while tokens survive restarts.
    """

    type: typing.Literal['memory', 'redis', 'postgres', 'composite'] = 'memory'
    url: str = ''
    messages: str = ''
    credentials: str = ''

    @pydantic.model_validator(mode='after')
    def _require_backend_urls(self) -> typing.Self:
        if self.type in ('redis', 'postgres') and not self.url:
            raise ValueError(f'store.url is required for the {self.type} backend')
        if self.type == 'composite' and (not self.messages or not self.credentials):
            raise ValueError('the composite store needs both store.messages and store.credentials URLs')
        return self


class GatewaySpec(pydantic.BaseModel):
    """One OpenAPI spec to expose as MCP tools through the embedded gateway."""

    name: str
    spec: str
    base_url: str | None = None


class GatewaySettings(pydantic.BaseModel):
    """Embed openapi-mcp-gateway in process, so a spec file becomes MCP tools without a separate server."""

    specs: list[GatewaySpec]


class ServerSettings(pydantic.BaseModel):
    """Where the webhook app listens, and the public URL external services reach it at."""

    host: str = '127.0.0.1'
    port: int = 8000
    url: str = ''

    @pydantic.model_validator(mode='after')
    def _default_url(self) -> typing.Self:
        if not self.url:
            host = 'localhost' if self.host == '0.0.0.0' else self.host
            self.url = f'http://{host}:{self.port}'
        return self


class AppConfig(pydantic.BaseModel):
    """The whole conciergent configuration, one agent plus surfaces plus a server."""

    agent: AgentSettings
    slack: SlackSettings | None = None
    line: LineSettings | None = None
    store: StoreSettings = pydantic.Field(default_factory=StoreSettings)
    gateway: GatewaySettings | None = None
    server: ServerSettings = pydantic.Field(default_factory=ServerSettings)


def yaml_layer(path: str | pathlib.Path) -> dict[str, typing.Any]:
    """Load one YAML config layer, resolving ``${ENV_VAR}`` and ``${ENV_VAR:-default}`` references."""
    raw = yaml.safe_load(pathlib.Path(path).expanduser().read_text()) or {}
    return _resolve_env(raw)


def build_app_config(*layers: dict[str, typing.Any]) -> AppConfig:
    """Merge config layers left to right, later layers win, then validate the result."""
    merged: dict[str, typing.Any] = {}
    for layer in layers:
        merged = _deep_merge(merged, layer)
    return AppConfig.model_validate(merged)


_ENV_PATTERN = re.compile(r'\$\{(\w+)(?::-(.*))?\}')


def _resolve_env(value: typing.Any) -> typing.Any:
    if isinstance(value, dict):
        return {key: _resolve_env(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_resolve_env(item) for item in value]
    if isinstance(value, str):
        match = _ENV_PATTERN.fullmatch(value)
        if match:
            env_value = os.environ.get(match.group(1))
            if env_value is not None:
                return env_value
            return match.group(2) if match.group(2) is not None else ''
    return value


def _deep_merge(base: dict[str, typing.Any], override: dict[str, typing.Any]) -> dict[str, typing.Any]:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged
