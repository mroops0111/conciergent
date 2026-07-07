import os
import pathlib
import re
import typing

import pydantic
import yaml

from conciergent.defaults import defaults_layer
from conciergent.surfaces.base import Surface
from conciergent.surfaces.line.app import Line
from conciergent.surfaces.slack.app import Slack


class ServerSettings(pydantic.BaseModel):
    """Where the webhook app listens, and the public URL external services reach it at."""

    host: str
    port: int
    url: str = ''

    @pydantic.model_validator(mode='after')
    def _default_url(self) -> typing.Self:
        if not self.url:
            host = 'localhost' if self.host == '0.0.0.0' else self.host
            self.url = f'http://{host}:{self.port}'
        return self


class AgentSettings(pydantic.BaseModel):
    """The batteries-included agent, a model plus a prompt plus MCP server URLs."""

    model: str
    system_prompt: str
    mcp_servers: list[str] = pydantic.Field(default_factory=list)
    input_token_limit: int | None = None
    mcp_read_timeout_seconds: float
    client_name: str

    @pydantic.field_validator('mcp_servers', mode='before')
    @classmethod
    def _empty_when_null(cls, value: typing.Any) -> typing.Any:
        # A bare `mcp_servers:` in YAML parses to None; treat that as no servers rather than an error.
        return [] if value is None else value


class SlackSettings(pydantic.BaseModel):
    """Slack app credentials, created once in the Slack app dashboard.

    UI text is not configured here, it lives in the locale catalog so it can be translated (see ``locales_dir``).
    """

    enabled: bool = False
    signing_secret: str = ''
    client_id: str = ''
    client_secret: str = ''
    bot_token: str = ''
    brand_color: str
    destructive_color: str
    api_timeout_seconds: float

    @pydantic.model_validator(mode='after')
    def _require_secret_when_enabled(self) -> typing.Self:
        # An empty secret would make every webhook signature forgeable, so an enabled surface must set one.
        if self.enabled and not self.signing_secret:
            raise ValueError('surface.slack.signing_secret is required when slack is enabled')
        return self

    def build(self) -> Surface:
        return Slack(
            signing_secret=self.signing_secret,
            client_id=self.client_id,
            client_secret=self.client_secret,
            bot_token=self.bot_token,
            brand_color=self.brand_color,
            destructive_color=self.destructive_color,
            api_timeout_seconds=self.api_timeout_seconds,
        )


class LineSettings(pydantic.BaseModel):
    """LINE Messaging API channel credentials, created once in the LINE developers console."""

    enabled: bool = False
    channel_secret: str = ''
    channel_access_token: str = ''
    brand_color: str
    destructive_color: str
    api_timeout_seconds: float

    @pydantic.model_validator(mode='after')
    def _require_credentials_when_enabled(self) -> typing.Self:
        if self.enabled and not (self.channel_secret and self.channel_access_token):
            raise ValueError('surface.line channel_secret and channel_access_token are required when line is enabled')
        return self

    def build(self) -> Surface:
        return Line(
            channel_secret=self.channel_secret,
            channel_access_token=self.channel_access_token,
            brand_color=self.brand_color,
            destructive_color=self.destructive_color,
            api_timeout_seconds=self.api_timeout_seconds,
        )


class SurfaceSettings(pydantic.BaseModel):
    """The chat surfaces, each turned on by its own ``enabled`` flag."""

    slack: SlackSettings
    line: LineSettings

    def enabled_surfaces(self) -> list[Surface]:
        return [settings.build() for settings in (self.slack, self.line) if settings.enabled]


class StoreSettings(pydantic.BaseModel):
    """Where state lives, split by sensitivity across two backends.

    Message-bearing state (history, approvals, dedupe, OAuth handoff) goes to the Redis ``messages_url``
    and ages out on its own, while long-lived credentials (MCP tokens and clients, bot tokens) go to the
    SQL ``credentials_url`` and survive restarts.
    """

    messages_url: typing.Annotated[str, pydantic.Field(min_length=1)]
    credentials_url: typing.Annotated[str, pydantic.Field(min_length=1)]
    max_turns: int


class GatewaySpec(pydantic.BaseModel):
    """One OpenAPI spec exposed as MCP tools through the embedded gateway.

    Fields mirror openapi-mcp-gateway's per-server config, so an embedded spec supports the same auth,
    exposure, and policy as running the gateway standalone.
    """

    name: str
    spec: str
    base_url: str | None = None
    path_prefix: str | None = None
    auth: dict[str, typing.Any] | None = None
    policy: dict[str, typing.Any] | None = None
    timeout: float = 90
    exposure: typing.Literal['static', 'dynamic'] = 'static'


class GatewaySettings(pydantic.BaseModel):
    """Embed openapi-mcp-gateway in process, so a spec file becomes MCP tools without a separate server."""

    enabled: bool = False
    redis_url: str = ''
    specs: list[GatewaySpec] = pydantic.Field(default_factory=list)

    @pydantic.field_validator('specs', mode='before')
    @classmethod
    def _empty_when_null(cls, value: typing.Any) -> typing.Any:
        # A bare `specs:` in YAML parses to None; treat that as no specs rather than an error.
        return [] if value is None else value

    @pydantic.model_validator(mode='after')
    def _require_redis_when_enabled(self) -> typing.Self:
        # The gateway persists its OAuth client registrations in Redis, so an unset URL would lose them on a restart.
        # It would then reject a client id the agent still holds, so an enabled gateway must name its Redis.
        if self.enabled and not self.redis_url:
            raise ValueError('gateway.redis_url is required when the gateway is enabled')
        return self


class ConversationSettings(pydantic.BaseModel):
    """How long conversation state lives, matching the reference defaults."""

    approval_ttl_seconds: int
    history_ttl_seconds: int
    oauth_wait_timeout_seconds: float


class LoggerSettings(pydantic.BaseModel):
    """How the process logs, applied once at startup by ``conciergent.logger.setup``."""

    level: str
    format: typing.Literal['text', 'json']
    file: str | None = None


class AppConfig(pydantic.BaseModel):
    """The whole conciergent configuration, one agent plus surfaces plus a server."""

    server: ServerSettings
    agent: AgentSettings
    surface: SurfaceSettings
    store: StoreSettings
    gateway: GatewaySettings
    conversation: ConversationSettings
    logger: LoggerSettings
    # A directory of ``{lang}.yml`` files whose keys override the shipped UI text, for rebranding or new languages.
    locales_dir: str | None = None

    @pydantic.model_validator(mode='after')
    def _mcp_read_timeout_outlasts_oauth_wait(self) -> typing.Self:
        # A missing token runs the OAuth flow inside the MCP connect, so the read timeout must outlast the handoff wait.
        # Otherwise it fires first and a slow or absent authorization reads as a hard MCP error.
        if self.agent.mcp_read_timeout_seconds <= self.conversation.oauth_wait_timeout_seconds:
            raise ValueError(
                'agent.mcp_read_timeout_seconds must exceed conversation.oauth_wait_timeout_seconds, '
                'so an in-chat authorization ends as a clean handoff expiry rather than an MCP timeout'
            )
        return self


def yaml_layer(path: str | pathlib.Path) -> dict[str, typing.Any]:
    """Load one YAML config layer, resolving ``${ENV_VAR}`` and ``${ENV_VAR:-default}`` references."""
    raw = yaml.safe_load(pathlib.Path(path).expanduser().read_text()) or {}
    return _resolve_env(raw)


def build_app_config(*layers: dict[str, typing.Any]) -> AppConfig:
    """Deep-merge the given layers over the shipped defaults, later layers win, then validate the result."""
    merged = defaults_layer()
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
