import datetime
import typing

from conciergent.agent.mcp.tool_policy import HideDestructiveTools, RunDestructiveTools
from conciergent.chat_context import ScheduledChatContext
from conciergent.runtime import HeadlessOAuthBridge, SkippedReply
from conciergent.store.message import MessageStore
from tests.conftest import StubRunner
from tests.scheduling.conftest import FIRED_AT, RecordingPushSurface, executor, schedule


class FailingRunner(StubRunner):
    async def run(self, user_input: str, **options: typing.Any) -> typing.Any:
        raise RuntimeError('the MCP server is down')


async def test_a_firing_pushes_the_reply_and_joins_the_chat_history(message_store: MessageStore):
    runner = StubRunner('Fed 6 times today.')
    surface = RecordingPushSurface()

    await executor(message_store, runner=runner, surface=surface).fire(schedule(), FIRED_AT)

    assert surface.targets == ['C1']
    assert surface.pushed.calls == [('processing', None), ('text', 'Fed 6 times today.')]
    assert await message_store.load_history('line:group:C1') == [{'seen': 'Summarize today.'}]
    call = runner.calls[0]
    chat_context = call['chat_context']
    assert isinstance(chat_context, ScheduledChatContext)
    assert chat_context.schedule == 'daily-digest' and chat_context.fired_at == FIRED_AT
    assert not chat_context.uses_user_credentials
    assert isinstance(chat_context.destructive_tools, HideDestructiveTools)
    assert call['bridge'] is None


async def test_a_schedule_with_a_principal_uses_its_stored_credentials_without_asking(message_store: MessageStore):
    runner = StubRunner()

    await executor(message_store, runner=runner).fire(schedule(principal='line:U1', allow_destructive=True), FIRED_AT)

    call = runner.calls[0]
    assert call['principal'] == 'line:U1'
    assert isinstance(call['bridge'], HeadlessOAuthBridge)
    assert call['chat_context'].uses_user_credentials
    assert isinstance(call['chat_context'].destructive_tools, RunDestructiveTools)


async def test_each_firing_runs_once_across_replicas(message_store: MessageStore):
    runner = StubRunner()
    first = executor(message_store, runner=runner)
    second = executor(message_store, runner=runner)

    await first.fire(schedule(), FIRED_AT)
    await second.fire(schedule(), FIRED_AT)
    await second.fire(schedule(), FIRED_AT + datetime.timedelta(days=1))

    assert len(runner.calls) == 2


async def test_a_skipped_reply_sends_nothing_and_records_nothing(message_store: MessageStore):
    surface = RecordingPushSurface()
    runner = StubRunner(SkippedReply(reason='nothing due'))

    await executor(message_store, runner=runner, surface=surface).fire(schedule(allow_skip=True), FIRED_AT)

    assert surface.texts() == []
    assert await message_store.load_history('line:group:C1') == []


async def test_a_failed_firing_pushes_the_failure_message(message_store: MessageStore):
    surface = RecordingPushSurface()

    await executor(message_store, runner=FailingRunner(), surface=surface).fire(
        schedule(failure_message='baby is down'), FIRED_AT
    )

    assert surface.texts() == ['baby is down']


async def test_a_failed_firing_without_a_failure_message_stays_silent(message_store: MessageStore):
    surface = RecordingPushSurface()

    await executor(message_store, runner=FailingRunner(), surface=surface).fire(schedule(), FIRED_AT)

    assert surface.texts() == []


async def test_unreachable_tools_count_as_a_failure_not_a_silent_drop(message_store: MessageStore):
    runner = StubRunner()
    runner.credential_free = False
    surface = RecordingPushSurface()

    await executor(message_store, runner=runner, surface=surface).fire(
        schedule(failure_message='baby is down'), FIRED_AT
    )

    assert runner.calls == []
    assert surface.texts() == ['baby is down']
