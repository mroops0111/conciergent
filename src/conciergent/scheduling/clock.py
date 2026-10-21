import abc
import asyncio
import datetime
import typing


class Clock(abc.ABC):
    """The time source a trigger waits on, so a test can drive time instead of waiting for it."""

    @abc.abstractmethod
    def now(self) -> datetime.datetime:
        """The current time, timezone-aware."""

    @abc.abstractmethod
    async def sleep(self, seconds: float) -> None:
        """Wait ``seconds``, returning at once when it is not positive."""


class SystemClock(Clock):
    """The wall clock."""

    @typing.override
    def now(self) -> datetime.datetime:
        return datetime.datetime.now(datetime.UTC)

    @typing.override
    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(max(seconds, 0))
