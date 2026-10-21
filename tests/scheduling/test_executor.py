import collections.abc
import contextlib
import typing

import pytest

from conciergent.agent.mcp.tool_policy import HideDestructiveTools, RunDestructiveTools
from conciergent.chat_context import ScheduledChatContext
from conciergent.runtime import HeadlessOAuthBridge, SkippedReply
from conciergent.scheduling import FiringOutcome
from conciergent.store.message import MessageStore
from conciergent.surfaces.base import PushTarget
from tests.conftest import StubRunner
from tests.scheduling.conftest import FIRED_AT, RecordingPushSurface, executor, schedule


class FailingRunner(StubRunner):
    async def run(self, user_input: str, **options: typing.Any) -> typing.Any:
        raise RuntimeError('the MCP server is down')


async def test_a_firing_pushes_the_reply_and_joins_the_chat_history(message_store: MessageStore):
    runner = StubRunner('Fed 6 times today.')
    surface = RecordingPushSurface()

    outcome = await executor(message_store, runner=runner, surface=surface).fire(schedule(), FIRED_AT)

    assert outcome is FiringOutcome.delivered
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


async def test_a_skipped_reply_sends_nothing_and_records_nothing(message_store: MessageStore):
    surface = RecordingPushSurface()
    runner = StubRunner(SkippedReply(reason='nothing due'))

    outcome = await executor(message_store, runner=runner, surface=surface).fire(schedule(allow_skip=True), FIRED_AT)

    assert outcome is FiringOutcome.skipped
    assert surface.texts() == []
    assert await message_store.load_history('line:group:C1') == []


async def test_a_failed_firing_pushes_the_failure_message(message_store: MessageStore):
    surface = RecordingPushSurface()

    outcome = await executor(message_store, runner=FailingRunner(), surface=surface).fire(
        schedule(failure_message='baby is down'), FIRED_AT
    )

    assert outcome is FiringOutcome.failed
    assert surface.texts() == ['baby is down']


async def test_a_failed_firing_without_a_failure_message_stays_silent(message_store: MessageStore):
    surface = RecordingPushSurface()

    outcome = await executor(message_store, runner=FailingRunner(), surface=surface).fire(schedule(), FIRED_AT)

    assert outcome is FiringOutcome.failed
    assert surface.texts() == []


async def test_unreachable_tools_count_as_a_failure_not_a_silent_drop(message_store: MessageStore):
    runner = StubRunner()
    runner.credential_free = False
    surface = RecordingPushSurface()

    outcome = await executor(message_store, runner=runner, surface=surface).fire(
        schedule(failure_message='baby is down'), FIRED_AT
    )

    assert outcome is FiringOutcome.failed
    assert runner.calls == []
    assert surface.texts() == ['baby is down']


async def test_a_failure_after_the_reply_is_out_still_counts_as_delivered(
    message_store: MessageStore, monkeypatch: pytest.MonkeyPatch
):
    async def broken_history(*args: typing.Any, **kwargs: typing.Any) -> None:
        raise RuntimeError('Redis went away')

    monkeypatch.setattr(message_store, 'append_history', broken_history)
    surface = RecordingPushSurface()

    outcome = await executor(message_store, runner=StubRunner('Fed 6 times today.'), surface=surface).fire(
        schedule(failure_message='baby is down'), FIRED_AT
    )

    # A retry would push the reply again, so the firing reports success and sends no failure message.
    assert outcome is FiringOutcome.delivered
    assert surface.texts() == ['Fed 6 times today.']


async def test_an_unreachable_push_target_fails_the_firing(message_store: MessageStore):
    class BrokenPush(RecordingPushSurface):
        @contextlib.asynccontextmanager
        async def push_target(self, to: str) -> collections.abc.AsyncIterator[PushTarget]:
            raise RuntimeError('LINE is down')
            yield

    outcome = await executor(message_store, surface=BrokenPush()).fire(schedule(), FIRED_AT)

    assert outcome is FiringOutcome.failed
