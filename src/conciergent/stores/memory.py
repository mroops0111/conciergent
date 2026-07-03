import asyncio
import collections.abc
import contextlib
import time
import typing

from .base import Store


_DEFAULT_MAX_TURNS = 10
_OAUTH_CODE_TTL_SECONDS = 300.0


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
        self._bot_tokens: dict[tuple[str, str], str] = {}
        self._oauth_codes: dict[str, tuple[asyncio.Future[str], float]] = {}

    @typing.override
    async def load_history(self, conversation: str) -> list[typing.Any]:
        turns = self._live_turns(conversation)
        return [message for _, messages in turns for message in messages]

    @typing.override
    async def append_history(self, conversation: str, messages: list[typing.Any], *, ttl_seconds: int) -> None:
        turns = self._live_turns(conversation)
        turns.append((time.monotonic() + ttl_seconds, list(messages)))
        self._history[conversation] = turns[-self._max_turns :]

    @typing.override
    async def replace_history(self, conversation: str, messages: list[typing.Any], *, ttl_seconds: int) -> None:
        self._history[conversation] = [(time.monotonic() + ttl_seconds, list(messages))]

    def _live_turns(self, conversation: str) -> list[tuple[float, list[typing.Any]]]:
        now = time.monotonic()
        return [turn for turn in self._history.get(conversation, []) if turn[0] > now]

    @typing.override
    async def dedupe(self, key: str, *, ttl_seconds: int) -> bool:
        now = time.monotonic()
        self._evict_expired(now)
        if key in self._dedup_keys:
            return True
        self._dedup_keys[key] = now + ttl_seconds
        return False

    @typing.override
    async def park_approval(
        self, conversation: str, state: collections.abc.Mapping[str, typing.Any], *, ttl_seconds: int
    ) -> None:
        self._approvals[conversation] = (dict(state), time.monotonic() + ttl_seconds)

    @typing.override
    async def take_approval(self, conversation: str) -> dict[str, typing.Any] | None:
        entry = self._approvals.pop(conversation, None)
        if entry is None:
            return None
        state, expiry = entry
        if expiry <= time.monotonic():
            return None
        return state

    @typing.override
    async def get_mcp_token(self, server: str, principal: str) -> dict[str, typing.Any] | None:
        token = self._mcp_tokens.get((server, principal))
        return dict(token) if token is not None else None

    @typing.override
    async def set_mcp_token(self, server: str, principal: str, token: collections.abc.Mapping[str, typing.Any]) -> None:
        self._mcp_tokens[server, principal] = dict(token)

    @typing.override
    async def get_mcp_client(self, server: str) -> dict[str, typing.Any] | None:
        client = self._mcp_clients.get(server)
        return dict(client) if client is not None else None

    @typing.override
    async def set_mcp_client(self, server: str, client: collections.abc.Mapping[str, typing.Any]) -> None:
        self._mcp_clients[server] = dict(client)

    @typing.override
    async def resolve_bot_token(self, surface: str, tenant: str) -> str | None:
        return self._bot_tokens.get((surface, tenant))

    @typing.override
    async def set_bot_token(self, surface: str, tenant: str, token: str) -> None:
        self._bot_tokens[surface, tenant] = token

    @typing.override
    async def deliver_oauth_code(self, state: str, code: str) -> None:
        future = self._oauth_slot(state)
        if not future.done():
            future.set_result(code)

    @typing.override
    async def await_oauth_code(self, state: str, *, timeout_seconds: float) -> str | None:
        future = self._oauth_slot(state)
        try:
            return await asyncio.wait_for(asyncio.shield(future), timeout=timeout_seconds)
        except TimeoutError:
            return None
        finally:
            with contextlib.suppress(KeyError):
                del self._oauth_codes[state]

    def _oauth_slot(self, state: str) -> asyncio.Future[str]:
        # Stale slots are evicted on access, so codes delivered to a waiter that never comes
        # do not accumulate for the life of the process.
        now = time.monotonic()
        self._oauth_codes = {key: slot for key, slot in self._oauth_codes.items() if slot[1] > now}
        future, _ = self._oauth_codes.setdefault(
            state, (asyncio.get_running_loop().create_future(), now + _OAUTH_CODE_TTL_SECONDS)
        )
        return future

    def _evict_expired(self, now: float) -> None:
        expired = [key for key, expiry in self._dedup_keys.items() if expiry <= now]
        for key in expired:
            del self._dedup_keys[key]
