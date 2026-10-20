import asyncio
import collections.abc
import datetime
import logging
import typing
import zoneinfo

import croniter

from conciergent.agent.compactor import HistorySummarizer
from conciergent.agent.runner import ChatRunner
from conciergent.chat_context import ScheduledChatContext
from conciergent.config import ScheduleSettings
from conciergent.defaults import DEFAULTS
from conciergent.runtime import HeadlessOAuthBridge
from conciergent.store.message import MessageStore
from conciergent.surfaces.base import PushTarget, Surface
from conciergent.turn import run_turn


logger = logging.getLogger(__name__)

# How long a firing's lock outlives it, long enough that no replica with a lagging clock fires it again.
_FIRING_LOCK_TTL_SECONDS = 86400


class ScheduleCannotRunError(Exception):
    """A scheduled turn cannot reach its tools, such as when an MCP server is unreachable."""


class Clock(typing.Protocol):
    """The time source a scheduler waits on, swapped for a fake one in tests."""

    def now(self) -> datetime.datetime:
        """The current time, timezone-aware."""
        ...

    async def sleep(self, seconds: float) -> None: ...


class SystemClock:
    """The wall clock."""

    def now(self) -> datetime.datetime:
        return datetime.datetime.now(datetime.UTC)

    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(seconds)


class Scheduler:
    """Fire each schedule's prompt as an unattended turn and push the reply into its target chat.

    A firing that the process sleeps through is skipped, not caught up.
    With several replicas, a Redis lock per firing lets exactly one of them run it.
    """

    def __init__(
        self,
        schedules: collections.abc.Sequence[ScheduleSettings],
        *,
        surfaces: collections.abc.Mapping[str, Surface],
        runner: ChatRunner,
        message_store: MessageStore,
        compactor: HistorySummarizer | None = None,
        history_ttl_seconds: int = DEFAULTS.conversation.history_ttl_seconds,
        clock: Clock | None = None,
    ) -> None:
        self._schedules = list(schedules)
        self._surfaces = surfaces
        self._runner = runner
        self._message_store = message_store
        self._compactor = compactor
        self._history_ttl_seconds = history_ttl_seconds
        self._clock = clock or SystemClock()

    async def run(self) -> None:
        """Fire every schedule on time until cancelled."""
        async with asyncio.TaskGroup() as group:
            for schedule in self._schedules:
                group.create_task(self._run_schedule(schedule))

    def next_firing(self, schedule: ScheduleSettings, after: datetime.datetime) -> datetime.datetime:
        """The first time strictly after ``after`` that ``schedule`` fires, in the schedule's timezone."""
        local = after.astimezone(zoneinfo.ZoneInfo(schedule.timezone))
        return croniter.croniter(schedule.cron, local).get_next(datetime.datetime)

    async def _run_schedule(self, schedule: ScheduleSettings) -> None:
        last_fired_at: datetime.datetime | None = None
        while True:
            now = self._clock.now()
            # A sleep that wakes a moment early must not find the same firing again.
            fired_at = self.next_firing(schedule, max(now, last_fired_at) if last_fired_at else now)
            await self._clock.sleep((fired_at - now).total_seconds())
            last_fired_at = fired_at
            try:
                await self.fire(schedule, fired_at)
            except Exception:
                # Even the failure message could not be pushed, so the log is all that is left.
                logger.exception('Schedule %s could not push to %s', schedule.name, schedule.target.to)

    async def fire(self, schedule: ScheduleSettings, fired_at: datetime.datetime) -> None:
        """Run one firing of ``schedule``, unless another replica already took it."""
        lock_key = f'schedule:{schedule.name}:{fired_at.isoformat()}'
        if await self._message_store.dedupe(lock_key, ttl_seconds=_FIRING_LOCK_TTL_SECONDS):
            return
        surface = self._surfaces[schedule.target.surface]
        async with surface.push_target(schedule.target.to) as target:
            try:
                await self._run_turn(schedule, fired_at, target)
            except Exception:
                logger.exception('Schedule %s failed at %s', schedule.name, fired_at.isoformat())
                if schedule.failure_message:
                    await target.surface.send_text(schedule.failure_message)

    async def _run_turn(self, schedule: ScheduleSettings, fired_at: datetime.datetime, target: PushTarget) -> None:
        chat_context = ScheduledChatContext(
            conversation=target.conversation,
            schedule=schedule.name,
            fired_at=fired_at,
            has_principal=schedule.principal is not None,
            skip_allowed=schedule.allow_skip,
            destructive_tool_policy='run' if schedule.allow_destructive else 'hide',
        )
        # run_turn drops a chat it cannot serve without a word, which here would hide an outage behind silence.
        if not await chat_context.can_run(self._runner):
            raise ScheduleCannotRunError('an MCP server is unreachable or needs a per-user authorization')
        await run_turn(
            schedule.prompt,
            # Without a principal the turn reaches no one's credentials, so this only labels it.
            principal=schedule.principal or f'schedule:{schedule.name}',
            chat_context=chat_context,
            runner=self._runner,
            surface=target.surface,
            message_store=self._message_store,
            bridge=HeadlessOAuthBridge() if schedule.principal is not None else None,
            compactor=self._compactor,
            history_ttl_seconds=self._history_ttl_seconds,
        )
