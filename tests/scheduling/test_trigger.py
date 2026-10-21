import asyncio
import collections.abc
import contextlib
import datetime
import typing

from conciergent.scheduling import Clock, CronTrigger
from conciergent.store.message import MessageStore
from conciergent.surfaces.base import PushTarget
from tests.conftest import StubRunner
from tests.scheduling.conftest import FIRED_AT, RecordingPushSurface, executor, schedule


class FakeClock(Clock):
    """A clock whose sleep jumps time forward, and that parks the trigger for good after a set number of sleeps."""

    def __init__(self, start: datetime.datetime, *, sleeps: int) -> None:
        self.current = start
        self.remaining_sleeps = sleeps
        self.parked = asyncio.Event()

    @typing.override
    def now(self) -> datetime.datetime:
        return self.current

    @typing.override
    async def sleep(self, seconds: float) -> None:
        if self.remaining_sleeps == 0:
            self.parked.set()
            await asyncio.Event().wait()
        self.remaining_sleeps -= 1
        self.current += datetime.timedelta(seconds=max(seconds, 0))


async def _serve_until_parked(
    trigger: CronTrigger, clock: FakeClock, message_store: MessageStore, **options: typing.Any
) -> None:
    task = asyncio.create_task(trigger.serve(executor(message_store, **options)))
    await clock.parked.wait()
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


def test_next_firing_reads_the_cron_in_the_schedule_timezone():
    # 12:00 UTC is 20:00 in Taipei, so the 21:00 firing is an hour later.
    after = datetime.datetime(2026, 10, 21, 12, tzinfo=datetime.UTC)

    assert CronTrigger.next_firing(schedule(), after) == FIRED_AT
    assert CronTrigger.next_firing(schedule(), FIRED_AT) == FIRED_AT + datetime.timedelta(days=1)


async def test_the_trigger_fires_each_day_on_time(message_store: MessageStore):
    runner = StubRunner()
    clock = FakeClock(datetime.datetime(2026, 10, 21, 12, tzinfo=datetime.UTC), sleeps=2)

    await _serve_until_parked(CronTrigger([schedule()], clock=clock), clock, message_store, runner=runner)

    assert [call['chat_context'].fired_at for call in runner.calls] == [
        FIRED_AT,
        FIRED_AT + datetime.timedelta(days=1),
    ]


async def test_a_firing_missed_by_a_restart_runs_within_the_grace_period(message_store: MessageStore):
    runner = StubRunner()
    # Two minutes after 21:00 Taipei, inside the default five-minute grace.
    clock = FakeClock(FIRED_AT + datetime.timedelta(minutes=2), sleeps=1)

    await _serve_until_parked(CronTrigger([schedule()], clock=clock), clock, message_store, runner=runner)

    assert [call['chat_context'].fired_at for call in runner.calls] == [FIRED_AT]


async def test_a_firing_older_than_the_grace_period_is_skipped(message_store: MessageStore):
    runner = StubRunner()
    clock = FakeClock(FIRED_AT + datetime.timedelta(minutes=10), sleeps=1)

    await _serve_until_parked(CronTrigger([schedule()], clock=clock), clock, message_store, runner=runner)

    assert [call['chat_context'].fired_at for call in runner.calls] == [FIRED_AT + datetime.timedelta(days=1)]


async def test_a_failing_push_does_not_stop_later_firings(message_store: MessageStore):
    class BrokenPush(RecordingPushSurface):
        @contextlib.asynccontextmanager
        async def push_target(self, to: str) -> collections.abc.AsyncIterator[PushTarget]:
            raise RuntimeError('LINE is down')
            yield

    clock = FakeClock(datetime.datetime(2026, 10, 21, 12, tzinfo=datetime.UTC), sleeps=2)

    await _serve_until_parked(CronTrigger([schedule()], clock=clock), clock, message_store, surface=BrokenPush())

    assert clock.current == FIRED_AT + datetime.timedelta(days=1)
