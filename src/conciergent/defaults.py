import importlib.resources

import pydantic
import yaml


class ConversationDefaults(pydantic.BaseModel):
    approval_ttl_seconds: int
    history_ttl_seconds: int
    oauth_wait_timeout_seconds: float


class AgentDefaults(pydantic.BaseModel):
    mcp_read_timeout_seconds: float


class StoreDefaults(pydantic.BaseModel):
    max_turns: int


class SurfaceDefaults(pydantic.BaseModel):
    brand_color: str
    destructive_color: str
    api_timeout_seconds: float


class Defaults(pydantic.BaseModel):
    conversation: ConversationDefaults
    agent: AgentDefaults
    store: StoreDefaults
    surface: SurfaceDefaults


def _load() -> Defaults:
    text = importlib.resources.files('conciergent').joinpath('defaults.yml').read_text(encoding='utf-8')
    return Defaults.model_validate(yaml.safe_load(text))


DEFAULTS = _load()
