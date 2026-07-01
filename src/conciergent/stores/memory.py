import collections.abc
import time
import typing

from .base import Store


class MemoryStore(Store):
    """In-memory ``Store`` for local development and single-process deployments.

    State is lost on restart and is not shared across processes.
    Use a networked backend for production multi-process deployments.
    """

    def __init__(self) -> None:
        self._history: dict[str, list[typing.Any]] = {}
        self._seen: dict[str, float] = {}
        self._approvals: dict[str, dict[str, typing.Any]] = {}

    async def load_history(self, principal: str) -> list[typing.Any]:
        return list(self._history.get(principal, []))

    async def save_history(self, principal: str, history: list[typing.Any]) -> None:
        self._history[principal] = list(history)

    async def seen(self, key: str, *, ttl_seconds: int) -> bool:
        now = time.monotonic()
        self._evict_expired(now)
        if key in self._seen:
            return True
        self._seen[key] = now + ttl_seconds
        return False

    async def park_approval(
        self, principal: str, state: collections.abc.Mapping[str, typing.Any], *, ttl_seconds: int
    ) -> None:
        self._approvals[principal] = dict(state)

    async def take_approval(self, principal: str) -> dict[str, typing.Any] | None:
        return self._approvals.pop(principal, None)

    def _evict_expired(self, now: float) -> None:
        expired = [key for key, expiry in self._seen.items() if expiry <= now]
        for key in expired:
            del self._seen[key]
