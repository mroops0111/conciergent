import abc
import collections.abc
import typing


class Store(abc.ABC):
    """Persist the small amount of state the runtime needs across turns and requests.

    The in-memory default needs no infrastructure, and networked backends implement the same interface.
    The interface grows as surfaces are added, so this defines only the parts the core runtime depends on.
    """

    @abc.abstractmethod
    async def load_history(self, principal: str) -> list[typing.Any]: ...

    @abc.abstractmethod
    async def save_history(self, principal: str, history: list[typing.Any]) -> None: ...

    @abc.abstractmethod
    async def dedupe(self, key: str, *, ttl_seconds: int) -> bool:
        """Record ``key`` and report whether it had already been recorded within the ttl window.

        Returns True when ``key`` is a repeat and False when it is new, which lets a caller drop
        duplicate work such as a redelivered webhook. Recording the key is a side effect of the check.
        """
        ...

    @abc.abstractmethod
    async def park_approval(
        self, principal: str, state: collections.abc.Mapping[str, typing.Any], *, ttl_seconds: int
    ) -> None: ...

    @abc.abstractmethod
    async def take_approval(self, principal: str) -> dict[str, typing.Any] | None:
        """Return and clear any parked approval state for ``principal``."""
        ...
