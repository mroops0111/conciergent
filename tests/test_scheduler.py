import asyncio
import collections.abc
import contextlib
import datetime
import typing
import zoneinfo

import fastapi

from conciergent.chat_context import ScheduledChatContext
from conciergent.config import ScheduleSettings
from conciergent.identity import ChatSurface
from conciergent.runtime import HeadlessOAuthBridge, SkippedReply
from conciergent.scheduler import Scheduler
from conciergent.store.message import MessageStore
from conciergent.surfaces.base import PushTarget, Surface, SurfaceContext
from tests.conftest import RecordingSurface, StubRunner


_TAIPEI = zoneinfo.ZoneInfo('Asia/Taipei')
_FIRED_AT = datetime.datetime(2026, 10, 21, 21, tzinfo=_TAIPEI)


class PushingSurface(Surface):
    """A surface whose pushes land on one recording reply surface, scoped like a LINE group."""

    name = ChatSurface.line

    def __init__(self) -> None:
        super().__init__()
        self.pushed = RecordingSurface()
        self.targets: list[str] = []

    @typing.override
    def build_routers(self, context: SurfaceContext) -> list[fastapi.APIRouter]:
        return []

    @typing.override
    @contextlib.asynccontextmanager
    async def push_target(self, to: str) -> collections.abc.AsyncIterator[PushTarget]:
        self.targets.append(to)
        yield PushTarget(conversation=f'line:group:{to}', surface=self.pushed)


class FailingRunner(StubRunner):
    async def run(self, user_input: str, **options: typing.Any) -> typing.Any:
        raise RuntimeError('the MCP server is down')


class FakeClock:
    """A clock whose sleep jumps time forward, and that parks the scheduler for good after a set number of sleeps."""

    def __init__(self, start: datetime.datetime, *, sleeps: int) -> None:
        self.current = start
        self.remaining_sleeps = sleeps
        self.parked = asyncio.Event()

    def now(self) -> datetime.datetime:
        return self.current

    async def sleep(self, seconds: float) -> None:
        if self.remaining_sleeps == 0:
            self.parked.set()
            await asyncio.Event().wait()
        self.remaining_sleeps -= 1
        self.current += datetime.timedelta(seconds=seconds)


async def _run_until_parked(scheduler: Scheduler, clock: FakeClock) -> None:
    task = asyncio.create_task(scheduler.run())
    await clock.parked.wait()
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


def _texts(surface: PushingSurface) -> list[str]:
    return [payload for kind, payload in surface.pushed.calls if kind == 'text']


def _schedule(**overrides: typing.Any) -> ScheduleSettings:
    return ScheduleSettings.model_validate(
        {
            'name': 'daily-digest',
            'cron': '0 21 * * *',
            'timezone': 'Asia/Taipei',
            'prompt': 'Summarize today.',
            'target': {'surface': 'line', 'to': 'C1'},
            **overrides,
        }
    )


def _scheduler(
    message_store: MessageStore,
    *schedules: ScheduleSettings,
    runner: StubRunner | None = None,
    surface: PushingSurface | None = None,
    clock: FakeClock | None = None,
) -> Scheduler:
    return Scheduler(
        schedules or [_schedule()],
        surfaces={'line': surface or PushingSurface()},
        runner=(runner or StubRunner()).as_runner(),
        message_store=message_store,
        clock=clock,
    )


def test_next_firing_reads_the_cron_in_the_schedule_timezone(message_store: MessageStore):
    scheduler = _scheduler(message_store)

    # 12:00 UTC is 20:00 in Taipei, so the 21:00 firing is an hour later.
    after = datetime.datetime(2026, 10, 21, 12, tzinfo=datetime.UTC)

    assert scheduler.next_firing(_schedule(), after) == _FIRED_AT
    assert scheduler.next_firing(_schedule(), _FIRED_AT) == _FIRED_AT + datetime.timedelta(days=1)


async def test_a_firing_pushes_the_reply_and_joins_the_chat_history(message_store: MessageStore):
    runner = StubRunner('Fed 6 times today.')
    surface = PushingSurface()
    scheduler = _scheduler(message_store, runner=runner, surface=surface)

    await scheduler.fire(_schedule(), _FIRED_AT)

    assert surface.targets == ['C1']
    assert surface.pushed.calls == [('processing', None), ('text', 'Fed 6 times today.')]
    assert await message_store.load_history('line:group:C1') == [{'seen': 'Summarize today.'}]
    call = runner.calls[0]
    chat_context = call['chat_context']
    assert isinstance(chat_context, ScheduledChatContext)
    assert chat_context.schedule == 'daily-digest' and chat_context.fired_at == _FIRED_AT
    assert not chat_context.uses_user_credentials and chat_context.destructive_tools == 'hide'
    assert call['bridge'] is None


async def test_a_schedule_with_a_principal_uses_its_stored_credentials_without_asking(message_store: MessageStore):
    runner = StubRunner()
    scheduler = _scheduler(message_store, runner=runner)

    await scheduler.fire(_schedule(principal='line:U1', allow_destructive=True), _FIRED_AT)

    call = runner.calls[0]
    assert call['principal'] == 'line:U1'
    assert isinstance(call['bridge'], HeadlessOAuthBridge)
    assert call['chat_context'].uses_user_credentials
    assert call['chat_context'].destructive_tools == 'run'


async def test_each_firing_runs_once_across_replicas(message_store: MessageStore):
    runner = StubRunner()
    first = _scheduler(message_store, runner=runner)
    second = _scheduler(message_store, runner=runner)

    await first.fire(_schedule(), _FIRED_AT)
    await second.fire(_schedule(), _FIRED_AT)
    await second.fire(_schedule(), _FIRED_AT + datetime.timedelta(days=1))

    assert len(runner.calls) == 2


async def test_a_skipped_reply_sends_nothing_and_records_nothing(message_store: MessageStore):
    surface = PushingSurface()
    scheduler = _scheduler(message_store, runner=StubRunner(SkippedReply(reason='nothing due')), surface=surface)

    await scheduler.fire(_schedule(allow_skip=True), _FIRED_AT)

    assert _texts(surface) == []
    assert await message_store.load_history('line:group:C1') == []


async def test_a_failed_firing_pushes_the_failure_message(message_store: MessageStore):
    surface = PushingSurface()
    scheduler = _scheduler(message_store, runner=FailingRunner(), surface=surface)

    await scheduler.fire(_schedule(failure_message='baby is down'), _FIRED_AT)

    assert _texts(surface) == ['baby is down']


async def test_a_failed_firing_without_a_failure_message_stays_silent(message_store: MessageStore):
    surface = PushingSurface()
    scheduler = _scheduler(message_store, runner=FailingRunner(), surface=surface)

    await scheduler.fire(_schedule(), _FIRED_AT)

    assert _texts(surface) == []


async def test_unreachable_tools_count_as_a_failure_not_a_silent_drop(message_store: MessageStore):
    runner = StubRunner()
    runner.group_chats_supported = False
    surface = PushingSurface()
    scheduler = _scheduler(message_store, runner=runner, surface=surface)

    await scheduler.fire(_schedule(failure_message='baby is down'), _FIRED_AT)

    assert runner.calls == []
    assert _texts(surface) == ['baby is down']


async def test_the_scheduler_fires_each_day_on_time(message_store: MessageStore):
    runner = StubRunner()
    clock = FakeClock(datetime.datetime(2026, 10, 21, 12, tzinfo=datetime.UTC), sleeps=2)
    scheduler = _scheduler(message_store, runner=runner, clock=clock)

    await _run_until_parked(scheduler, clock)

    assert [call['chat_context'].fired_at for call in runner.calls] == [
        _FIRED_AT,
        _FIRED_AT + datetime.timedelta(days=1),
    ]


async def test_a_failing_push_does_not_stop_later_firings(message_store: MessageStore):
    class BrokenPush(PushingSurface):
        @contextlib.asynccontextmanager
        async def push_target(self, to: str) -> collections.abc.AsyncIterator[PushTarget]:
            raise RuntimeError('LINE is down')
            yield

    clock = FakeClock(datetime.datetime(2026, 10, 21, 12, tzinfo=datetime.UTC), sleeps=2)
    scheduler = _scheduler(message_store, surface=BrokenPush(), clock=clock)

    await _run_until_parked(scheduler, clock)

    assert clock.current == _FIRED_AT + datetime.timedelta(days=1)
