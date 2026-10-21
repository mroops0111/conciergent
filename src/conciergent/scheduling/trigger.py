import abc
import asyncio
import collections.abc
import datetime
import logging
import typing
import zoneinfo

import croniter

from conciergent.config import ScheduleSettings
from conciergent.scheduling.clock import Clock, SystemClock
from conciergent.scheduling.executor import ScheduleExecutor


logger = logging.getLogger(__name__)


class ScheduleTrigger(abc.ABC):
    """Decide when schedules fire, and hand each firing to the executor.

    The executor owns what a firing does, so a new way to start one,
    such as an external scheduler calling in, is another trigger and leaves firing untouched.
    """

    @abc.abstractmethod
    async def serve(self, executor: ScheduleExecutor) -> None:
        """Hand firings to ``executor`` until the app stops and cancels this."""


class CronTrigger(ScheduleTrigger):
    """Fire each schedule from its cron expression inside the app process.

    Every replica runs this trigger, and the executor's lock lets one of them run each firing.
    A firing up to the schedule's ``misfire_grace_seconds`` late still runs, which covers a restart around its time,
    while an older one is skipped rather than caught up.
    """

    def __init__(self, schedules: collections.abc.Sequence[ScheduleSettings], *, clock: Clock | None = None) -> None:
        self._schedules = list(schedules)
        self._clock = clock or SystemClock()

    @typing.override
    async def serve(self, executor: ScheduleExecutor) -> None:
        async with asyncio.TaskGroup() as group:
            for schedule in self._schedules:
                group.create_task(self._serve_schedule(schedule, executor))

    @staticmethod
    def next_firing(schedule: ScheduleSettings, after: datetime.datetime) -> datetime.datetime:
        """The first time strictly after ``after`` that ``schedule`` fires, in the schedule's timezone."""
        local = after.astimezone(zoneinfo.ZoneInfo(schedule.timezone))
        return croniter.croniter(schedule.cron, local).get_next(datetime.datetime)

    async def _serve_schedule(self, schedule: ScheduleSettings, executor: ScheduleExecutor) -> None:
        grace = datetime.timedelta(seconds=schedule.misfire_grace_seconds)
        # Starting a grace period back picks up a firing that a restart just missed.
        cursor = self._clock.now() - grace
        while True:
            fired_at = self.next_firing(schedule, cursor)
            # Each firing is due once, so a sleep that wakes early or a slow turn never finds the same one again.
            cursor = fired_at
            lateness = self._clock.now() - fired_at
            if lateness > grace:
                logger.warning('Schedule %s skipped its firing at %s, too late', schedule.name, fired_at.isoformat())
                continue
            await self._clock.sleep(-lateness.total_seconds())
            try:
                await executor.fire(schedule, fired_at)
            except Exception:
                # Even the failure message could not be pushed, so the log is all that is left.
                logger.exception('Schedule %s could not push to %s', schedule.name, schedule.target.to)
