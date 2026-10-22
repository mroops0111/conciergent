import collections.abc
import contextlib
import typing

import pytest

from conciergent.agent.mcp.tool_policy import HideDestructiveToolPolicy, RunDestructiveToolPolicy
from conciergent.chat_context import UnattendedChatContext
from conciergent.push import PushOutcome
from conciergent.runtime import SkippedReply
from conciergent.store.message import MessageStore
from conciergent.surfaces.base import PushTarget
from tests.conftest import StubRunner
from tests.push.conftest import STARTED_AT, RecordingPushSurface, executor, push_settings


class FailingRunner(StubRunner):
    async def run(self, user_input: str, **options: typing.Any) -> typing.Any:
        raise RuntimeError('the MCP server is down')


async def test_a_push_pushes_the_reply_and_joins_the_chat_history(message_store: MessageStore):
    runner = StubRunner('Fed 6 times today.')
    surface = RecordingPushSurface()

    outcome = await executor(message_store, runner=runner, surface=surface).run(push_settings(), STARTED_AT)

    assert outcome is PushOutcome.delivered
    assert surface.targets == ['C1']
    assert surface.pushed.calls == [('processing', None), ('text', 'Fed 6 times today.')]
    assert await message_store.load_history('line:group:C1') == [{'seen': 'Summarize today.'}]
    call = runner.calls[0]
    chat_context = call['chat_context']
    assert isinstance(chat_context, UnattendedChatContext)
    assert chat_context.push_name == 'daily-digest' and chat_context.started_at == STARTED_AT
    assert not chat_context.uses_user_credentials
    assert isinstance(chat_context.destructive_tool_policy, HideDestructiveToolPolicy)
    assert call['bridge'] is None


async def test_a_push_allowed_destructive_tools_runs_them_unasked(message_store: MessageStore):
    runner = StubRunner()

    await executor(message_store, runner=runner).run(push_settings(allow_destructive=True), STARTED_AT)

    assert isinstance(runner.calls[0]['chat_context'].destructive_tool_policy, RunDestructiveToolPolicy)


async def test_a_skipped_reply_sends_nothing_and_records_nothing(message_store: MessageStore):
    surface = RecordingPushSurface()
    runner = StubRunner(SkippedReply(reason='nothing due'))

    outcome = await executor(message_store, runner=runner, surface=surface).run(
        push_settings(allow_skip=True), STARTED_AT
    )

    assert outcome is PushOutcome.skipped
    assert runner.calls[0]['chat_context'].allows_skip
    assert surface.texts() == []
    assert await message_store.load_history('line:group:C1') == []


async def test_a_failed_push_pushes_the_failure_message(message_store: MessageStore):
    surface = RecordingPushSurface()

    outcome = await executor(message_store, runner=FailingRunner(), surface=surface).run(
        push_settings(failure_message='baby is down'), STARTED_AT
    )

    assert outcome is PushOutcome.failed
    assert surface.texts() == ['baby is down']


async def test_a_failed_push_without_a_failure_message_stays_silent(message_store: MessageStore):
    surface = RecordingPushSurface()

    outcome = await executor(message_store, runner=FailingRunner(), surface=surface).run(push_settings(), STARTED_AT)

    assert outcome is PushOutcome.failed
    assert surface.texts() == []


async def test_unreachable_tools_count_as_a_failure_not_a_silent_drop(message_store: MessageStore):
    runner = StubRunner()
    runner.credential_free = False
    surface = RecordingPushSurface()

    outcome = await executor(message_store, runner=runner, surface=surface).run(
        push_settings(failure_message='baby is down'), STARTED_AT
    )

    assert outcome is PushOutcome.failed
    assert runner.calls == []
    assert surface.texts() == ['baby is down']


async def test_a_failure_after_the_reply_is_out_still_counts_as_delivered(
    message_store: MessageStore, monkeypatch: pytest.MonkeyPatch
):
    async def broken_history(*args: typing.Any, **kwargs: typing.Any) -> None:
        raise RuntimeError('Redis went away')

    monkeypatch.setattr(message_store, 'append_history', broken_history)
    surface = RecordingPushSurface()

    outcome = await executor(message_store, runner=StubRunner('Fed 6 times today.'), surface=surface).run(
        push_settings(failure_message='baby is down'), STARTED_AT
    )

    # A retry would push the reply again, so the push reports success and sends no failure message.
    assert outcome is PushOutcome.delivered
    assert surface.texts() == ['Fed 6 times today.']


async def test_an_unreachable_push_target_fails_the_push(message_store: MessageStore):
    class BrokenPush(RecordingPushSurface):
        @contextlib.asynccontextmanager
        async def push_target(self, to: str) -> collections.abc.AsyncIterator[PushTarget]:
            raise RuntimeError('LINE is down')
            yield

    outcome = await executor(message_store, surface=BrokenPush()).run(push_settings(), STARTED_AT)

    assert outcome is PushOutcome.failed


async def test_a_failure_closing_the_push_after_the_reply_is_out_still_counts_as_delivered(message_store: MessageStore):
    class ClosingFails(RecordingPushSurface):
        @contextlib.asynccontextmanager
        async def push_target(self, to: str) -> collections.abc.AsyncIterator[PushTarget]:
            async with super().push_target(to) as target:
                yield target
            raise RuntimeError('closing the LINE client failed')

    surface = ClosingFails()

    outcome = await executor(message_store, runner=StubRunner('Fed 6 times today.'), surface=surface).run(
        push_settings(), STARTED_AT
    )

    assert outcome is PushOutcome.delivered
    assert surface.texts() == ['Fed 6 times today.']


async def test_a_store_outage_pushes_the_failure_message(message_store: MessageStore, monkeypatch: pytest.MonkeyPatch):
    async def unreachable(*args: typing.Any, **kwargs: typing.Any) -> list[typing.Any]:
        raise ConnectionError('Redis is down')

    monkeypatch.setattr(message_store, 'load_history', unreachable)
    surface = RecordingPushSurface()

    outcome = await executor(message_store, surface=surface).run(
        push_settings(failure_message='baby is down'), STARTED_AT
    )

    assert outcome is PushOutcome.failed
    assert surface.texts() == ['baby is down']


async def test_a_push_never_takes_an_approval_waiting_in_the_users_direct_chat(message_store: MessageStore):
    # A push to a LINE user joins their direct chat, where their own pending approval is parked under no owner.
    await message_store.park_approval('line:U1', {'pending': 'delete'}, ttl_seconds=60)
    runner = StubRunner('Good morning.')

    class DirectChatPush(RecordingPushSurface):
        @classmethod
        def push_conversation(cls, to: str) -> str:
            return f'line:{to}'

    await executor(message_store, runner=runner, surface=DirectChatPush()).run(
        push_settings(target={'surface': 'line', 'to': 'U1'}), STARTED_AT
    )

    assert runner.calls[0]['pending_approval'] is None
    assert await message_store.take_approval('line:U1') == {'pending': 'delete'}
