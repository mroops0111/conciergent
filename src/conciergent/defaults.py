import copy
import importlib.resources
import typing

import pydantic
import yaml


class _Lenient(pydantic.BaseModel):
    # The defaults file doubles as the merge base, so it carries fields, like secrets and enabled flags,
    # that this view does not model; ignore them and expose only the tunable values downstream falls back on.
    model_config = pydantic.ConfigDict(extra='ignore')


class ServerDefaults(_Lenient):
    host: str
    port: int


class AgentDefaults(_Lenient):
    mcp_read_timeout_seconds: float
    client_name: str


class SurfaceKindDefaults(_Lenient):
    brand_color: str
    destructive_color: str
    api_timeout_seconds: float


class SurfaceDefaults(_Lenient):
    slack: SurfaceKindDefaults
    line: SurfaceKindDefaults


class StoreDefaults(_Lenient):
    max_turns: int


class ConversationDefaults(_Lenient):
    approval_ttl_seconds: int
    history_ttl_seconds: int
    oauth_wait_timeout_seconds: float


class LoggerDefaults(_Lenient):
    level: str
    format: typing.Literal['text', 'json']


class Defaults(_Lenient):
    server: ServerDefaults
    agent: AgentDefaults
    surface: SurfaceDefaults
    store: StoreDefaults
    conversation: ConversationDefaults
    logger: LoggerDefaults


def _load() -> dict[str, typing.Any]:
    text = importlib.resources.files('conciergent').joinpath('defaults.yml').read_text(encoding='utf-8')
    return yaml.safe_load(text)


_BASE = _load()


def defaults_layer() -> dict[str, typing.Any]:
    """A fresh copy of the base config layer, deep-merged under the user's config."""
    return copy.deepcopy(_BASE)


DEFAULTS = Defaults.model_validate(_BASE)
