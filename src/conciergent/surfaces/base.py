import abc
import typing

import fastapi

from ..runtime import ChatAgent, HistoryCompactor
from ..stores.base import Store


class SurfaceContext(typing.NamedTuple):
    """Everything the application hands a surface when it mounts."""

    store: Store
    agent: ChatAgent
    compactor: HistoryCompactor | None
    base_url: str


class Surface(abc.ABC):
    """One chat platform's contribution to the application.

    The application stays ignorant of concrete platforms, adding one means implementing this
    and passing an instance to ``App``, never editing the assembly.
    """

    @abc.abstractmethod
    def build_routers(self, context: SurfaceContext) -> list[fastapi.APIRouter]:
        """Return the webhook and auxiliary routes this platform needs."""
        ...
