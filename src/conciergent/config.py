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
    token_limit: int | None = None


class SlackSettings(pydantic.BaseModel):
    """Slack app credentials, created once in the Slack app dashboard."""

    signing_secret: str
    client_id: str = ''
    client_secret: str = ''
    scopes: list[str] = pydantic.Field(
        default_factory=lambda: ['chat:write', 'im:history', 'im:read', 'im:write', 'users:read']
    )
    bot_token: str = ''


class LineSettings(pydantic.BaseModel):
    """LINE Messaging API channel credentials, created once in the LINE developers console."""

    channel_secret: str
    channel_access_token: str


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
