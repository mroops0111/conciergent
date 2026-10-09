import typing

import pytest
from pydantic_ai.models.test import TestModel

from conciergent import i18n
from conciergent.agent.runner import ChatRunner
from conciergent.groups import GroupPolicy
from tests.conftest import destructive_server
from tests.surfaces.line.conftest import (
    USER,
    BuildHarness,
    LineHarness,
    group_source,
    join_event,
    message_event,
    postback_event,
    room_source,
    self_mention,
    user_source,
)


GROUP = 'Cgroup1'
ALLOW_GROUP = GroupPolicy(enabled=True, allowed=frozenset({GROUP}))


def _group_message(
    text: str, *, event_id: str, mentions: list[dict[str, typing.Any]] | None = None, group: str = GROUP
) -> dict[str, typing.Any]:
    fields: dict[str, typing.Any] = {'quoteToken': f'q-{event_id}'}
    if mentions is not None:
        fields['mention'] = {'mentionees': mentions}
    return message_event(text, event_id=event_id, source=group_source(group), **fields)


def _addressed(text: str, *, event_id: str, group: str = GROUP) -> dict[str, typing.Any]:
    """A group message that opens by mentioning the bot as ``@Bot``."""
    return _group_message(f'@Bot {text}', event_id=event_id, mentions=[self_mention(0, 4)], group=group)


@pytest.fixture
async def group_harness(line_app: BuildHarness) -> LineHarness:
    harness = await line_app(groups=ALLOW_GROUP)
    harness.display_names[USER] = 'Amy'
    return harness


async def test_a_mention_runs_a_group_turn_with_the_mention_stripped(group_harness: LineHarness) -> None:
    await group_harness.post(_addressed('what is up', event_id='g1'))

    assert group_harness.agent.inputs == ['what is up']
    call = group_harness.agent.calls[0]
    assert call['principal'] == f'line:{USER}'
    assert call['speaker'] == 'Amy'
    assert await group_harness.message_store.load_history(f'line:group:{GROUP}')
    assert group_harness.loadings == [], 'the loading animation only exists in one-on-one chats'
    # The text reply quotes the member's message.
    assert group_harness.replies[0]['quoteToken'] == 'q-g1'


async def test_a_mention_after_an_emoji_is_stripped_by_utf16_offset(group_harness: LineHarness) -> None:
    # The emoji is two UTF-16 units, so the mention starts at unit 3 but at code point 2.
    await group_harness.post(_group_message('😀 @Bot hi', event_id='g2', mentions=[self_mention(3, 4)]))

    assert group_harness.agent.inputs == ['😀 hi']


async def test_group_chatter_is_dropped_before_any_check_or_lookup(group_harness: LineHarness) -> None:
    someone_else = {'index': 0, 'length': 4, 'type': 'user', 'userId': 'Uother'}

    await group_harness.post(
        _group_message('just chatting', event_id='g3'),
        _group_message('@Bob hello', event_id='g4', mentions=[someone_else]),
    )

    assert group_harness.agent.inputs == []
    assert group_harness.lang_lookups == []
    assert group_harness.agent.group_checks == 0


async def test_reply_to_all_answers_every_message(line_app: BuildHarness) -> None:
    harness = await line_app(groups=ALLOW_GROUP._replace(reply_to='all'))

    await harness.post(_group_message('no mention needed', event_id='g5'))

    assert harness.agent.inputs == ['no mention needed']


async def test_groups_outside_the_allowlist_are_ignored(group_harness: LineHarness) -> None:
    await group_harness.post(_addressed('hi', event_id='g6', group='Cother'))

    assert group_harness.agent.inputs == []


async def test_groups_are_ignored_while_disabled(line_app: BuildHarness) -> None:
    harness = await line_app(groups=ALLOW_GROUP._replace(enabled=False))

    await harness.post(_addressed('hi', event_id='g7'))

    assert harness.agent.inputs == []


async def test_groups_are_ignored_when_a_server_needs_per_user_authorization(group_harness: LineHarness) -> None:
    group_harness.agent.groups_supported = False

    await group_harness.post(_addressed('hi', event_id='g8'))

    assert group_harness.agent.inputs == []


async def test_a_suggestion_tap_needs_no_mention(group_harness: LineHarness) -> None:
    await group_harness.post(postback_event('suggestion:Show more', source=group_source(GROUP)))

    assert group_harness.agent.inputs == ['Show more']
    assert group_harness.agent.calls[0]['speaker'] == 'Amy'
    # A postback carries no message to quote.
    assert 'quoteToken' not in group_harness.replies[0]


async def test_a_direct_suggestion_tap_runs_the_prompt(group_harness: LineHarness) -> None:
    await group_harness.post(postback_event('suggestion:Confirm', source=user_source()))

    assert group_harness.agent.inputs == ['Confirm']
    assert group_harness.agent.calls[0]['group'] is None


async def test_the_push_fallback_targets_the_group(group_harness: LineHarness) -> None:
    event = _addressed('hi', event_id='g10')
    event['replyToken'] = None

    await group_harness.post(event)

    assert group_harness.pushes and group_harness.pushes[0]['to'] == GROUP


async def test_a_room_is_its_own_conversation(line_app: BuildHarness) -> None:
    harness = await line_app(groups=GroupPolicy(enabled=True, allowed=frozenset({'Rroom1'}), reply_to='all'))

    await harness.post(message_event('hi', event_id='g11', source=room_source('Rroom1')))

    assert await harness.message_store.load_history('line:room:Rroom1')
    # The member's profile is unreadable here, so a stable anonymous label stands in.
    assert harness.agent.calls[0]['speaker'] == f'user-{USER[-4:]}'


async def test_a_join_event_logs_the_group_and_starts_no_turn(
    group_harness: LineHarness, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level('INFO'):
        await group_harness.post(join_event('Cnew'))

    assert group_harness.agent.inputs == []
    assert 'Cnew' in caplog.text


async def test_a_real_approval_waits_for_the_member_who_requested_it(line_app: BuildHarness) -> None:
    calls: list[int] = []
    runner = ChatRunner(model=TestModel(), system_prompt='be helpful', mcp_servers=[destructive_server(calls)])
    harness = await line_app(runner=runner, groups=ALLOW_GROUP)
    confirm = f'suggestion:{i18n.t("approval.confirm", None)}'

    # Alice asks, and the destructive tool parks behind a confirmation card in the group.
    await harness.post(_addressed('delete it', event_id='r1'))
    assert calls == []
    assert harness.replies[-1]['type'] == 'flex'

    # Bob taps Alice's Confirm, which neither runs the tool nor uses up her approval.
    await harness.post(postback_event(confirm, event_id='r2', source=group_source(GROUP, user='Ubob')))
    assert calls == []

    # Alice taps Confirm, and only now the tool runs, once.
    await harness.post(postback_event(confirm, event_id='r3', source=group_source(GROUP)))
    assert len(calls) == 1
