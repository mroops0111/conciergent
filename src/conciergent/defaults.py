import importlib.resources
import typing

import pydantic
import yaml


class ServerDefaults(pydantic.BaseModel):
    host: str
    port: int


class AgentDefaults(pydantic.BaseModel):
    mcp_read_timeout_seconds: float
    client_name: str


class SurfaceDefaults(pydantic.BaseModel):
    brand_color: str
    destructive_color: str
    api_timeout_seconds: float


class StoreDefaults(pydantic.BaseModel):
    max_turns: int


class ConversationDefaults(pydantic.BaseModel):
    approval_ttl_seconds: int
    history_ttl_seconds: int
    oauth_wait_timeout_seconds: float


class LoggerDefaults(pydantic.BaseModel):
    level: str
    format: typing.Literal['text', 'json']


class Defaults(pydantic.BaseModel):
    server: ServerDefaults
    agent: AgentDefaults
    surface: SurfaceDefaults
    store: StoreDefaults
    conversation: ConversationDefaults
    logger: LoggerDefaults


def _load() -> Defaults:
    text = importlib.resources.files('conciergent').joinpath('defaults.yml').read_text(encoding='utf-8')
    return Defaults.model_validate(yaml.safe_load(text))


DEFAULTS = _load()
