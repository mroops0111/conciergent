import os
import pathlib
import re
import typing

import pydantic
import yaml

from conciergent.defaults import DEFAULTS


class AgentSettings(pydantic.BaseModel):
    """The batteries-included agent, a model plus a prompt plus MCP server URLs."""

    model: str
    system_prompt: str
    mcp_servers: list[str] = pydantic.Field(default_factory=list)
    input_token_limit: int | None = None
    mcp_read_timeout_seconds: float = DEFAULTS.agent.mcp_read_timeout_seconds
    client_name: str = DEFAULTS.agent.client_name


class SlackSettings(pydantic.BaseModel):
    """Slack app credentials, created once in the Slack app dashboard.

    An empty secret would make every webhook signature forgeable, so required fields reject it,
    catching an unset environment variable at startup instead. UI text is not configured here,
    it lives in the locale catalog so it can be translated (see ``locales_dir``).
    """

    signing_secret: typing.Annotated[str, pydantic.Field(min_length=1)]
    client_id: str = ''
    client_secret: str = ''
    bot_token: str = ''
    brand_color: str = DEFAULTS.surface.brand_color
    destructive_color: str = DEFAULTS.surface.destructive_color
    api_timeout_seconds: float = DEFAULTS.surface.api_timeout_seconds


class LineSettings(pydantic.BaseModel):
    """LINE Messaging API channel credentials, created once in the LINE developers console.

    An empty secret would make every webhook signature forgeable, so both fields reject it,
    catching an unset environment variable at startup instead. UI text lives in the locale catalog.
    """

    channel_secret: typing.Annotated[str, pydantic.Field(min_length=1)]
    channel_access_token: typing.Annotated[str, pydantic.Field(min_length=1)]
    brand_color: str = DEFAULTS.surface.brand_color
    destructive_color: str = DEFAULTS.surface.destructive_color
    api_timeout_seconds: float = DEFAULTS.surface.api_timeout_seconds


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
    max_turns: int = DEFAULTS.store.max_turns

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


class ConversationSettings(pydantic.BaseModel):
    """How long conversation state lives, matching the reference defaults."""

    approval_ttl_seconds: int = DEFAULTS.conversation.approval_ttl_seconds
    history_ttl_seconds: int = DEFAULTS.conversation.history_ttl_seconds
    oauth_wait_timeout_seconds: float = DEFAULTS.conversation.oauth_wait_timeout_seconds


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
    conversation: ConversationSettings = pydantic.Field(default_factory=ConversationSettings)
    gateway: GatewaySettings | None = None
    server: ServerSettings = pydantic.Field(default_factory=ServerSettings)
    # A directory of ``{lang}.yml`` files whose keys override the shipped UI text, for rebranding or new languages.
    locales_dir: str | None = None


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
