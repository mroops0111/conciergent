import abc
import typing

import fastapi

from conciergent.compactor import HistorySummarizer
from conciergent.defaults import DEFAULTS
from conciergent.runner import ChatRunner
from conciergent.stores.base import Store


class SurfaceContext(typing.NamedTuple):
    """Everything the application hands a surface when it mounts."""

    store: Store
    runner: ChatRunner
    compactor: HistorySummarizer | None
    base_url: str
    approval_ttl_seconds: int = DEFAULTS.conversation.approval_ttl_seconds
    history_ttl_seconds: int = DEFAULTS.conversation.history_ttl_seconds
    oauth_wait_timeout_seconds: float = DEFAULTS.conversation.oauth_wait_timeout_seconds


class Surface(abc.ABC):
    """One chat platform's contribution to the application.

    The application stays ignorant of concrete platforms, adding one means implementing this
    and passing an instance to ``App``, never editing the assembly.
    """

    @abc.abstractmethod
    def build_routers(self, context: SurfaceContext) -> list[fastapi.APIRouter]:
        """Return the webhook and auxiliary routes this platform needs."""
        ...
