"""In-memory ``Store`` implementation, the zero-infrastructure default.

Suitable for local development and single-process deployments. State is lost on
restart and is not shared across processes; use a networked backend for
production multi-process deployments.
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from typing import Any

from .base import Store


class MemoryStore(Store):
    def __init__(self) -> None:
        self._history: dict[str, list[Any]] = {}
        self._seen: dict[str, float] = {}
        self._approvals: dict[str, dict[str, Any]] = {}
        self._mcp_client_info: dict[tuple[str, str], dict[str, Any]] = {}
        self._mcp_tokens: dict[tuple[str, str], dict[str, Any]] = {}

    async def load_history(self, principal: str) -> list[Any]:
        return list(self._history.get(principal, []))

    async def save_history(self, principal: str, history: list[Any]) -> None:
        self._history[principal] = list(history)

    async def seen(self, key: str, *, ttl_seconds: int) -> bool:
        now = time.monotonic()
        self._evict_expired(now)
        if key in self._seen:
            return True
        self._seen[key] = now + ttl_seconds
        return False

    async def park_approval(self, principal: str, state: Mapping[str, Any], *, ttl_seconds: int) -> None:
        # Expiry is not tracked in memory; parked approvals live until taken.
        self._approvals[principal] = dict(state)

    async def take_approval(self, principal: str) -> dict[str, Any] | None:
        return self._approvals.pop(principal, None)

    async def get_mcp_client_info(self, server: str, principal: str) -> dict[str, Any] | None:
        info = self._mcp_client_info.get((server, principal))
        return dict(info) if info is not None else None

    async def set_mcp_client_info(self, server: str, principal: str, info: Mapping[str, Any]) -> None:
        self._mcp_client_info[(server, principal)] = dict(info)

    async def get_mcp_token(self, server: str, principal: str) -> dict[str, Any] | None:
        token = self._mcp_tokens.get((server, principal))
        return dict(token) if token is not None else None

    async def set_mcp_token(self, server: str, principal: str, token: Mapping[str, Any]) -> None:
        self._mcp_tokens[(server, principal)] = dict(token)

    def _evict_expired(self, now: float) -> None:
        expired = [key for key, expiry in self._seen.items() if expiry <= now]
        for key in expired:
            del self._seen[key]
