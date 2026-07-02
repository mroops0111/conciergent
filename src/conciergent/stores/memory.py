import collections.abc
import time
import typing

from .base import Store


_DEFAULT_MAX_TURNS = 10


class MemoryStore(Store):
    """In-memory ``Store`` for local development and single-process deployments.

    State is lost on restart and is not shared across processes.
    Use a networked backend for production multi-process deployments.
    """

    def __init__(self, *, max_turns: int = _DEFAULT_MAX_TURNS) -> None:
        self._max_turns = max_turns
        self._history: dict[str, list[tuple[float, list[typing.Any]]]] = {}
        self._dedup_keys: dict[str, float] = {}
        self._approvals: dict[str, tuple[dict[str, typing.Any], float]] = {}
        self._mcp_tokens: dict[tuple[str, str], dict[str, typing.Any]] = {}
        self._mcp_clients: dict[str, dict[str, typing.Any]] = {}

    async def load_history(self, principal: str) -> list[typing.Any]:
        turns = self._live_turns(principal)
        return [message for _, messages in turns for message in messages]

    async def append_history(self, principal: str, messages: list[typing.Any], *, ttl_seconds: int) -> None:
        turns = self._live_turns(principal)
        turns.append((time.monotonic() + ttl_seconds, list(messages)))
        self._history[principal] = turns[-self._max_turns :]

    def _live_turns(self, principal: str) -> list[tuple[float, list[typing.Any]]]:
        now = time.monotonic()
        return [turn for turn in self._history.get(principal, []) if turn[0] > now]

    async def dedupe(self, key: str, *, ttl_seconds: int) -> bool:
        now = time.monotonic()
        self._evict_expired(now)
        if key in self._dedup_keys:
            return True
        self._dedup_keys[key] = now + ttl_seconds
        return False

    async def park_approval(
        self, principal: str, state: collections.abc.Mapping[str, typing.Any], *, ttl_seconds: int
    ) -> None:
        self._approvals[principal] = (dict(state), time.monotonic() + ttl_seconds)

    async def take_approval(self, principal: str) -> dict[str, typing.Any] | None:
        entry = self._approvals.pop(principal, None)
        if entry is None:
            return None
        state, expiry = entry
        if expiry <= time.monotonic():
            return None
        return state

    async def get_mcp_token(self, server: str, principal: str) -> dict[str, typing.Any] | None:
        token = self._mcp_tokens.get((server, principal))
        return dict(token) if token is not None else None

    async def set_mcp_token(self, server: str, principal: str, token: collections.abc.Mapping[str, typing.Any]) -> None:
        self._mcp_tokens[server, principal] = dict(token)

    async def get_mcp_client(self, server: str) -> dict[str, typing.Any] | None:
        client = self._mcp_clients.get(server)
        return dict(client) if client is not None else None

    async def set_mcp_client(self, server: str, client: collections.abc.Mapping[str, typing.Any]) -> None:
        self._mcp_clients[server] = dict(client)

    def _evict_expired(self, now: float) -> None:
        expired = [key for key, expiry in self._dedup_keys.items() if expiry <= now]
        for key in expired:
            del self._dedup_keys[key]
