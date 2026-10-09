import collections.abc
import typing

import pytest
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic_ai.models.test import TestModel

from conciergent import i18n
from conciergent.agent.runner import ChatRunner
from conciergent.groups import GroupPolicy
from tests.surfaces.line.conftest import REPLY_TOKEN, USER, LineHarness


SignHeaders = collections.abc.Callable[[bytes], dict[str, str]]
BuildBody = collections.abc.Callable[..., bytes]
BuildHarness = collections.abc.Callable[..., typing.Awaitable[LineHarness]]

GROUP = 'Cgroup1'


def _group_message(
    text: str, *, event_id: str, mentions: list[dict[str, typing.Any]] | None = None, group: str = GROUP
) -> dict[str, typing.Any]:
    message: dict[str, typing.Any] = {'type': 'text', 'text': text}
    if mentions is not None:
        message['mention'] = {'mentionees': mentions}
    return {
        'type': 'message',
        'webhookEventId': event_id,
        'replyToken': REPLY_TOKEN,
        'source': {'type': 'group', 'groupId': group, 'userId': USER},
        'message': message,
    }


def _self_mention(index: int, length: int) -> dict[str, typing.Any]:
    return {'index': index, 'length': length, 'type': 'user', 'userId': 'Ubot', 'isSelf': True}


@pytest.fixture
async def group_harness(line_app: BuildHarness) -> LineHarness:
    harness = await line_app(groups=GroupPolicy(enabled=True, allowed=frozenset({GROUP})))
    harness.display_names[USER] = 'Amy'
    return harness


async def _post(harness: LineHarness, sign_headers: SignHeaders, line_body: BuildBody, *events: typing.Any) -> None:
    body = line_body(*events)
    await harness.client.post('/line/events', content=body, headers=sign_headers(body))


async def test_a_mention_runs_a_shared_turn_with_the_mention_stripped(
    group_harness: LineHarness, sign_headers: SignHeaders, line_body: BuildBody
) -> None:
    event = _group_message('@Bot what is up', event_id='g1', mentions=[_self_mention(0, 4)])

    await _post(group_harness, sign_headers, line_body, event)

    assert group_harness.agent.inputs == ['what is up']
    call = group_harness.agent.calls[0]
    assert call['principal'] == f'line:{USER}'
    assert call['shared'] is True
    assert call['speaker'] == 'Amy'
    assert call['bridge'] is None
    assert await group_harness.message_store.load_history(f'line:group:{GROUP}')
    assert group_harness.loadings == [], 'the loading animation only exists in one-on-one chats'


async def test_a_mention_after_an_emoji_is_stripped_by_utf16_offset(
    group_harness: LineHarness, sign_headers: SignHeaders, line_body: BuildBody
) -> None:
    # The emoji is two UTF-16 units, so the mention starts at unit 3 but at code point 2.
    event = _group_message('😀 @Bot hi', event_id='g2', mentions=[_self_mention(3, 4)])

    await _post(group_harness, sign_headers, line_body, event)

    assert group_harness.agent.inputs == ['😀 hi']


async def test_messages_without_a_mention_of_the_bot_are_ignored(
    group_harness: LineHarness, sign_headers: SignHeaders, line_body: BuildBody
) -> None:
    other = {'index': 0, 'length': 4, 'type': 'user', 'userId': 'Uother'}
    events = [
        _group_message('just chatting', event_id='g3'),
        _group_message('@Bob hello', event_id='g4', mentions=[other]),
    ]

    await _post(group_harness, sign_headers, line_body, *events)

    assert group_harness.agent.inputs == []


async def test_reply_to_all_answers_every_message(
    line_app: BuildHarness, sign_headers: SignHeaders, line_body: BuildBody
) -> None:
    harness = await line_app(groups=GroupPolicy(enabled=True, allowed=frozenset({GROUP}), reply_to='all'))

    await _post(harness, sign_headers, line_body, _group_message('no mention needed', event_id='g5'))

    assert harness.agent.inputs == ['no mention needed']


async def test_groups_outside_the_allowlist_are_ignored(
    group_harness: LineHarness, sign_headers: SignHeaders, line_body: BuildBody
) -> None:
    event = _group_message('@Bot hi', event_id='g6', mentions=[_self_mention(0, 4)], group='Cother')

    await _post(group_harness, sign_headers, line_body, event)

    assert group_harness.agent.inputs == []


async def test_groups_are_ignored_while_disabled(
    line_app: BuildHarness, sign_headers: SignHeaders, line_body: BuildBody
) -> None:
    harness = await line_app(groups=GroupPolicy(enabled=False, allowed=frozenset({GROUP})))

    await _post(
        harness, sign_headers, line_body, _group_message('@Bot hi', event_id='g7', mentions=[_self_mention(0, 4)])
    )

    assert harness.agent.inputs == []


async def test_groups_are_ignored_when_a_server_needs_per_user_authorization(
    group_harness: LineHarness, sign_headers: SignHeaders, line_body: BuildBody
) -> None:
    group_harness.agent.groups_supported = False

    await _post(
        group_harness, sign_headers, line_body, _group_message('@Bot hi', event_id='g8', mentions=[_self_mention(0, 4)])
    )

    assert group_harness.agent.inputs == []


async def test_a_suggestion_tap_needs_no_mention(
    group_harness: LineHarness, sign_headers: SignHeaders, line_body: BuildBody
) -> None:
    event = {
        'type': 'postback',
        'webhookEventId': 'g9',
        'replyToken': REPLY_TOKEN,
        'source': {'type': 'group', 'groupId': GROUP, 'userId': USER},
        'postback': {'data': 'suggestion:Show more'},
    }

    await _post(group_harness, sign_headers, line_body, event)

    assert group_harness.agent.inputs == ['Show more']
    assert group_harness.agent.calls[0]['shared'] is True


async def test_the_push_fallback_targets_the_group(
    group_harness: LineHarness, sign_headers: SignHeaders, line_body: BuildBody
) -> None:
    event = _group_message('@Bot hi', event_id='g10', mentions=[_self_mention(0, 4)])
    event['replyToken'] = None

    await _post(group_harness, sign_headers, line_body, event)

    assert group_harness.pushes and group_harness.pushes[0]['to'] == GROUP


async def test_a_room_is_its_own_conversation(
    line_app: BuildHarness, sign_headers: SignHeaders, line_body: BuildBody
) -> None:
    harness = await line_app(groups=GroupPolicy(enabled=True, allowed=frozenset({'Rroom1'}), reply_to='all'))
    event = {
        'type': 'message',
        'webhookEventId': 'g11',
        'replyToken': REPLY_TOKEN,
        'source': {'type': 'room', 'roomId': 'Rroom1', 'userId': USER},
        'message': {'type': 'text', 'text': 'hi'},
    }

    await _post(harness, sign_headers, line_body, event)

    assert await harness.message_store.load_history('line:room:Rroom1')
    # The member's profile is unreadable here, so a stable anonymous label stands in.
    assert harness.agent.calls[0]['speaker'] == f'user-{USER[-4:]}'


async def test_a_join_event_starts_no_turn(
    group_harness: LineHarness, sign_headers: SignHeaders, line_body: BuildBody, caplog: pytest.LogCaptureFixture
) -> None:
    event = {
        'type': 'join',
        'webhookEventId': 'g12',
        'replyToken': REPLY_TOKEN,
        'source': {'type': 'group', 'groupId': 'Cnew'},
    }

    with caplog.at_level('INFO'):
        await _post(group_harness, sign_headers, line_body, event)

    assert group_harness.agent.inputs == []
    assert 'Cnew' in caplog.text


async def test_a_direct_suggestion_tap_runs_the_prompt(
    group_harness: LineHarness, sign_headers: SignHeaders, line_body: BuildBody
) -> None:
    event = {
        'type': 'postback',
        'webhookEventId': 'g13',
        'replyToken': REPLY_TOKEN,
        'source': {'type': 'user', 'userId': USER},
        'postback': {'data': 'suggestion:Confirm'},
    }

    await _post(group_harness, sign_headers, line_body, event)

    assert group_harness.agent.inputs == ['Confirm']
    assert group_harness.agent.calls[0]['shared'] is False
    assert group_harness.agent.calls[0]['bridge'] is not None


async def test_a_real_approval_waits_for_the_member_who_requested_it(
    line_app: BuildHarness, sign_headers: SignHeaders, line_body: BuildBody
) -> None:
    calls: list[int] = []
    server = FastMCP('test')

    @server.tool(annotations=ToolAnnotations(destructiveHint=True))
    def delete_it(x: int) -> str:
        calls.append(x)
        return 'deleted'

    runner = ChatRunner(model=TestModel(), system_prompt='be helpful', mcp_servers=[server])
    harness = await line_app(runner=runner, groups=GroupPolicy(enabled=True, allowed=frozenset({GROUP})))
    confirm = i18n.t('approval.confirm', None)

    def tap(user: str, event_id: str) -> dict[str, typing.Any]:
        return {
            'type': 'postback',
            'webhookEventId': event_id,
            'replyToken': REPLY_TOKEN,
            'source': {'type': 'group', 'groupId': GROUP, 'userId': user},
            'postback': {'data': f'suggestion:{confirm}'},
        }

    # Alice asks, and the destructive tool parks behind a confirmation card in the group.
    alice_asks = _group_message('@Bot delete it', event_id='r1', mentions=[_self_mention(0, 4)])
    await _post(harness, sign_headers, line_body, alice_asks)
    assert calls == []
    assert harness.replies[-1]['type'] == 'flex'

    # Bob taps Alice's Confirm, which neither runs the tool nor uses up her approval.
    await _post(harness, sign_headers, line_body, tap('Ubob', 'r2'))
    assert calls == []

    # Alice taps Confirm, and only now the tool runs, once.
    await _post(harness, sign_headers, line_body, tap(USER, 'r3'))
    assert len(calls) == 1
