import abc
import os
import pathlib
import re
import typing
import zoneinfo

import pydantic
import yaml

from conciergent.defaults import defaults_layer
from conciergent.group_chat import GroupChatPolicy, ReplyTrigger
from conciergent.identity import ChatSurface
from conciergent.surfaces.base import PushSurface, Surface
from conciergent.surfaces.discord.app import Discord
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
    mcp_probe_timeout_seconds: float
    mcp_probe_retry_seconds: float
    client_name: str

    @pydantic.field_validator('mcp_servers', mode='before')
    @classmethod
    def _empty_when_null(cls, value: typing.Any) -> typing.Any:
        # A bare `mcp_servers:` in YAML parses to None, which means no servers rather than an error.
        return [] if value is None else value


class GroupChatSettings(pydantic.BaseModel):
    """Opt-in group chats for one surface, off by default.

    A group turn runs with no per-user authorization,
    so group chats are served only when every MCP server works without a user's token,
    a public server or a gateway spec with a static ``bearer`` or ``api_key``.
    """

    enabled: bool = False
    reply_trigger: ReplyTrigger = 'mention'

    def policy(self) -> GroupChatPolicy:
        return GroupChatPolicy(enabled=self.enabled, reply_trigger=self.reply_trigger)


class SurfaceKindSettings(pydantic.BaseModel, abc.ABC):
    """What every chat platform's settings share, with the ``Surface`` class they build.

    A new platform subclasses this with its own credentials, so the checks that span platforms need no change.
    """

    # The Surface class these settings build, which the config asks what the platform can do before building it.
    surface_class: typing.ClassVar[type[Surface]]

    enabled: bool = False
    brand_color: str
    destructive_color: str
    api_timeout_seconds: float
    group_chats: GroupChatSettings = pydantic.Field(default_factory=GroupChatSettings)

    @abc.abstractmethod
    def build(self) -> Surface:
        """Build the platform's surface from these settings."""

    @property
    def group_chats_enabled(self) -> bool:
        return self.enabled and self.group_chats.policy().enabled


class SlackSettings(SurfaceKindSettings):
    """Slack app credentials, created once in the Slack app dashboard.

    UI text is not configured here, it lives in the locale catalog so it can be translated (see ``locales_dir``).
    """

    surface_class = Slack

    signing_secret: str = ''
    client_id: str = ''
    client_secret: str = ''
    bot_token: str = ''

    @pydantic.model_validator(mode='after')
    def _require_secret_when_enabled(self) -> typing.Self:
        # An empty secret would make every webhook signature forgeable, so an enabled surface must set one.
        if self.enabled and not self.signing_secret:
            raise ValueError('surface.slack.signing_secret is required when slack is enabled')
        return self

    @typing.override
    def build(self) -> Surface:
        return Slack(
            signing_secret=self.signing_secret,
            client_id=self.client_id,
            client_secret=self.client_secret,
            bot_token=self.bot_token,
            brand_color=self.brand_color,
            destructive_color=self.destructive_color,
            api_timeout_seconds=self.api_timeout_seconds,
            group_chat_policy=self.group_chats.policy(),
        )


class LineSettings(SurfaceKindSettings):
    """LINE Messaging API channel credentials, created once in the LINE developers console."""

    surface_class = Line

    channel_secret: str = ''
    channel_access_token: str = ''

    @pydantic.model_validator(mode='after')
    def _require_credentials_when_enabled(self) -> typing.Self:
        if self.enabled and not (self.channel_secret and self.channel_access_token):
            raise ValueError('surface.line channel_secret and channel_access_token are required when line is enabled')
        return self

    @typing.override
    def build(self) -> Surface:
        return Line(
            channel_secret=self.channel_secret,
            channel_access_token=self.channel_access_token,
            brand_color=self.brand_color,
            destructive_color=self.destructive_color,
            api_timeout_seconds=self.api_timeout_seconds,
            group_chat_policy=self.group_chats.policy(),
        )


class DiscordSettings(SurfaceKindSettings):
    """Discord app credentials, created once in the Discord developer portal.

    The bot connects over the gateway and replies in direct messages, so a single bot token is all it needs.
    """

    surface_class = Discord

    bot_token: str = ''

    @pydantic.model_validator(mode='after')
    def _require_token_when_enabled(self) -> typing.Self:
        if self.enabled and not self.bot_token:
            raise ValueError('surface.discord.bot_token is required when discord is enabled')
        return self

    @typing.override
    def build(self) -> Surface:
        return Discord(
            bot_token=self.bot_token,
            brand_color=self.brand_color,
            destructive_color=self.destructive_color,
            api_timeout_seconds=self.api_timeout_seconds,
            group_chat_policy=self.group_chats.policy(),
        )


class SurfaceSettings(pydantic.BaseModel):
    """The chat surfaces, each turned on by its own ``enabled`` flag, and each field named for its ``ChatSurface``."""

    slack: SlackSettings
    line: LineSettings
    discord: DiscordSettings

    def kinds(self) -> list[SurfaceKindSettings]:
        return [self.settings_for(surface) for surface in ChatSurface]

    def settings_for(self, surface: ChatSurface) -> SurfaceKindSettings:
        return getattr(self, surface.value)

    def enabled_surfaces(self) -> list[Surface]:
        return [settings.build() for settings in self.kinds() if settings.enabled]

    def group_chats_enabled(self) -> bool:
        return any(settings.group_chats_enabled for settings in self.kinds())


class StoreSettings(pydantic.BaseModel):
    """Where state lives, split by sensitivity across two backends.

    Message-bearing state, such as history, approvals and the OAuth handoff, goes to the Redis ``messages_url``,
    where it ages out on its own.
    Long-lived credentials, the MCP tokens and clients and the bot tokens, go to the SQL ``credentials_url``,
    where they survive restarts.
    """

    messages_url: typing.Annotated[str, pydantic.Field(min_length=1)]
    credentials_url: typing.Annotated[str, pydantic.Field(min_length=1)]
    max_turns: int


class GatewaySpec(pydantic.BaseModel):
    """One OpenAPI spec exposed as MCP tools through the embedded gateway.

    Fields mirror openapi-mcp-gateway's per-server config, so an embedded spec supports the same auth,
    exposure, policy, per-operation shaping, headers, and instructions as running the gateway standalone.
    The mappings are validated by the gateway itself when it starts, so they follow its documented shapes.
    """

    name: str
    spec: str
    base_url: str | None = None
    path_prefix: str | None = None
    auth: dict[str, typing.Any] | None = None
    policy: dict[str, typing.Any] | None = None
    timeout: float = 90
    # ``{style: static | dynamic, promote_resources: bool}``.
    # A bare ``static`` or ``dynamic`` is shorthand for the style.
    exposure: dict[str, typing.Any] = pydantic.Field(default_factory=dict)
    # Per-operation shaping keyed by operationId, for example renaming a tool or fixing a parameter.
    operations: dict[str, typing.Any] = pydantic.Field(default_factory=dict)
    # Static headers sent on every upstream call.
    headers: dict[str, str] = pydantic.Field(default_factory=dict)
    # Guidance about the API as a whole, sent to the agent in the MCP initialize result.
    instructions: str | None = None

    @pydantic.field_validator('exposure', mode='before')
    @classmethod
    def _exposure_shorthand(cls, value: typing.Any) -> typing.Any:
        if value is None:
            return {}
        return {'style': value} if isinstance(value, str) else value

    def server_config(self) -> dict[str, typing.Any]:
        """This spec as an openapi-mcp-gateway ``servers`` entry, leaving out what is unset."""
        return self.model_dump(exclude_none=True, exclude_defaults=True) | {'name': self.name, 'spec': self.spec}

    @property
    def needs_user_authorization(self) -> bool:
        """Whether each user must authorize their own account, so the spec's tools cannot serve a group chat.

        A spec with no auth or a static ``bearer`` or ``api_key`` calls the API as one shared identity.
        So does one with an ``oauth2`` client-credentials service token.
        Any other ``oauth2`` flow, including an unset one the gateway detects from the spec, is treated as per-user.
        """
        auth = self.auth or {}
        auth_type = auth.get('type', 'none')
        if auth_type == 'oauth2':
            return auth.get('flow') != 'client_credentials'
        return False


class GatewaySettings(pydantic.BaseModel):
    """Embed openapi-mcp-gateway in process, so a spec file becomes MCP tools without a separate server."""

    enabled: bool = False
    redis_url: str = ''
    specs: list[GatewaySpec] = pydantic.Field(default_factory=list)

    @pydantic.field_validator('specs', mode='before')
    @classmethod
    def _empty_when_null(cls, value: typing.Any) -> typing.Any:
        # A bare `specs:` in YAML parses to None, which means no specs rather than an error.
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


class ScheduleTarget(pydantic.BaseModel):
    """Where a schedule's reply goes, a surface and the user or chat id it pushes to.

    Whether the surface can push, and to which ids, is the surface's own answer, checked with the whole config.
    """

    surface: ChatSurface
    to: typing.Annotated[str, pydantic.Field(min_length=1)]


class ScheduleSettings(pydantic.BaseModel):
    """A prompt the agent runs on demand from ``conciergent fire``, pushing its reply into a chat nobody spoke in.

    When it fires is up to the caller, such as cron or a Kubernetes CronJob.
    The turn joins the target chat's history, so a follow-up question there has the reply as context.
    """

    name: typing.Annotated[str, pydantic.Field(min_length=1)]
    # An IANA zone name the prompt header gives the firing time in, such as Asia/Taipei.
    timezone: str = 'UTC'
    prompt: typing.Annotated[str, pydantic.Field(min_length=1)]
    target: ScheduleTarget
    # Whose stored credentials reach the MCP servers, such as line:U123.
    # Without one the turn runs with no one's authorization, like a group chat.
    principal: str | None = None
    # Whether the agent may end the turn with skip_reply and send nothing, when the prompt finds nothing to say.
    allow_skip: bool = False
    # Run destructive tools without a confirmation nobody is present to give.
    # Unsafe, so off by default, which leaves those tools out of the turn.
    allow_destructive: bool = False
    # Text pushed as-is when the turn fails, such as when an MCP server is down, so a failure is not silent.
    failure_message: str | None = None

    @pydantic.field_validator('timezone')
    @classmethod
    def _timezone_exists(cls, value: str) -> str:
        try:
            zoneinfo.ZoneInfo(value)
        except (zoneinfo.ZoneInfoNotFoundError, ValueError) as error:
            raise ValueError(f'not a known IANA timezone: {value!r}') from error
        return value


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
    schedules: list[ScheduleSettings] = pydantic.Field(default_factory=list)
    # A directory of ``{lang}.yml`` files whose keys override the shipped UI text, for rebranding or new languages.
    locales_dir: str | None = None

    @pydantic.field_validator('schedules', mode='before')
    @classmethod
    def _no_schedules_when_null(cls, value: typing.Any) -> typing.Any:
        # A bare `schedules:` in YAML parses to None, which means no schedules rather than an error.
        return [] if value is None else value

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

    @pydantic.model_validator(mode='after')
    def _group_chats_need_shared_identity_specs(self) -> typing.Self:
        # A group turn holds no one's authorization, so a per-user spec could never run there.
        # Gateway specs are known up front and fail here, other MCP servers are probed when the app starts.
        if not (self.gateway.enabled and self.surface.group_chats_enabled()):
            return self
        per_user = [spec.name for spec in self.gateway.specs if spec.needs_user_authorization]
        if per_user:
            raise ValueError(
                'group chats need every tool to work without a per-user authorization, '
                f'but these gateway specs use per-user oauth2: {", ".join(per_user)}'
            )
        return self

    @pydantic.model_validator(mode='after')
    def _schedules_fit_the_app(self) -> typing.Self:
        names = [schedule.name for schedule in self.schedules]
        duplicates = sorted({name for name in names if names.count(name) > 1})
        if duplicates:
            raise ValueError(f'schedule names must be unique, repeated: {", ".join(duplicates)}')
        for schedule in self.schedules:
            target = schedule.target
            settings = self.surface.settings_for(target.surface)
            if not settings.enabled:
                raise ValueError(f'schedule {schedule.name} targets {target.surface}, which is not enabled')
            if not issubclass(settings.surface_class, PushSurface):
                raise ValueError(f'schedule {schedule.name} targets {target.surface}, which cannot push messages yet')
            try:
                settings.surface_class.push_conversation(target.to)
            except ValueError as error:
                raise ValueError(f'schedule {schedule.name}: {error}') from error
        # A schedule with no principal holds no one's authorization, the same requirement group chats have.
        unattended = [schedule.name for schedule in self.schedules if schedule.principal is None]
        per_user = [spec.name for spec in self.gateway.specs if spec.needs_user_authorization]
        if unattended and self.gateway.enabled and per_user:
            raise ValueError(
                f'schedules without a principal ({", ".join(unattended)}) need every tool to work without a per-user '
                f'authorization, but these gateway specs use per-user oauth2: {", ".join(per_user)}'
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
