"""The pluggable state-store interface.

A ``Store`` holds the small amount of cross-turn and cross-request state the
runtime needs. The in-memory default requires no infrastructure; networked
backends implement the same interface. The interface grows as surfaces are
added; this module defines the parts the core runtime depends on.
"""

from __future__ import annotations

import abc
from collections.abc import Mapping
from typing import Any


class Store(abc.ABC):
    # --- conversation history ---

    @abc.abstractmethod
    async def load_history(self, principal: str) -> list[Any]: ...

    @abc.abstractmethod
    async def save_history(self, principal: str, history: list[Any]) -> None: ...

    # --- webhook idempotency ---

    @abc.abstractmethod
    async def seen(self, key: str, *, ttl_seconds: int) -> bool:
        """Atomically record ``key`` and report whether it had already been recorded.

        Returns True if this is a duplicate (already seen), False if it is new.
        """
        ...

    # --- human-in-the-loop parking ---

    @abc.abstractmethod
    async def park_approval(self, principal: str, state: Mapping[str, Any], *, ttl_seconds: int) -> None: ...

    @abc.abstractmethod
    async def take_approval(self, principal: str) -> dict[str, Any] | None:
        """Return and clear any parked approval state for ``principal``."""
        ...
