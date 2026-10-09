import typing

import pytest

from conciergent import i18n
from conciergent.groups import GroupPolicy
from conciergent.surfaces.slack.app import Slack
from tests.surfaces.slack.conftest import BOT_USER, TEAM, USER, BuildHarness, SlackHarness, event, interaction


CHANNEL = 'C1'
TS = '200.300'
ALLOW_CHANNEL = GroupPolicy(enabled=True, allowed=frozenset({CHANNEL}))


def _mention(text: str, *, event_id: str, channel: str = CHANNEL) -> dict[str, typing.Any]:
    return event(
        event_id=event_id,
        text=f'<@{BOT_USER}> {text}',
        type='app_mention',
        channel=channel,
        channel_type='channel',
        ts=TS,
    )


def _channel_message(text: str, *, event_id: str) -> dict[str, typing.Any]:
    return event(event_id=event_id, text=text, channel=CHANNEL, channel_type='channel', ts=TS)


def _click(value: str, *, user: str = USER, channel: str = CHANNEL, scope: str = 'exclusive') -> dict[str, typing.Any]:
    return interaction(
        f'suggestion:{scope}:0:0', value=value, user=user, channel=channel, message={'ts': TS, 'thread_ts': TS}
    )


async def _installed(slack_app: BuildHarness, groups: GroupPolicy) -> SlackHarness:
    harness = await slack_app(groups=groups)
    await harness.install()
    return harness


@pytest.fixture
async def group_harness(slack_app: BuildHarness) -> SlackHarness:
    return await _installed(slack_app, ALLOW_CHANNEL)


async def test_a_mention_runs_a_group_turn_in_its_thread(group_harness: SlackHarness) -> None:
    await group_harness.post_event(_mention('what is up', event_id='E1'))

    assert group_harness.agent.inputs == ['what is up']
    call = group_harness.agent.calls[0]
    assert call['principal'] == f'slack:{TEAM}:{USER}'
    assert call['speaker'] == f'name-{USER}'
    assert await group_harness.message_store.load_history(f'slack:{TEAM}:group:{CHANNEL}:{TS}')
    channel, payload = group_harness.posts[0]
    assert channel == CHANNEL and payload['thread_ts'] == TS
    # The text reply mentions the member it answers.
    assert payload['text'] == f'<@{USER}> echo what is up'


async def test_channels_outside_the_allowlist_are_ignored(group_harness: SlackHarness) -> None:
    await group_harness.post_event(_mention('hi', event_id='E2', channel='C9'))

    assert group_harness.agent.inputs == []


async def test_plain_channel_messages_need_reply_to_all(slack_app: BuildHarness) -> None:
    mention_only = await _installed(slack_app, ALLOW_CHANNEL)
    every_message = await _installed(slack_app, ALLOW_CHANNEL._replace(reply_to='all'))

    await mention_only.post_event(_channel_message('hello all', event_id='E3'))
    await every_message.post_event(_channel_message('hello all', event_id='E4'))
    # In reply_to=all the mention also arrives as a message event, so app_mention is skipped to answer once.
    await every_message.post_event(_mention('hi', event_id='E5'))

    assert mention_only.agent.inputs == []
    assert every_message.agent.inputs == ['hello all']


async def test_groups_are_ignored_when_a_server_needs_per_user_authorization(group_harness: SlackHarness) -> None:
    group_harness.agent.groups_supported = False

    await group_harness.post_event(_mention('hi', event_id='E6'))

    assert group_harness.agent.inputs == []


async def test_another_members_confirm_tap_is_ignored_and_leaves_the_card_for_its_owner(
    group_harness: SlackHarness,
) -> None:
    confirm = i18n.t('approval.confirm', None)
    owner = f'slack:{TEAM}:{USER}'
    await group_harness.message_store.park_approval(
        f'slack:{TEAM}:group:{CHANNEL}:{TS}', {'parked': True}, ttl_seconds=60, owner=owner
    )

    await group_harness.post_interaction(_click(confirm, user='U2'))
    await group_harness.post_interaction(_click(confirm))

    assert group_harness.agent.inputs == [confirm]
    assert group_harness.agent.calls[0]['pending_approval'] == {'parked': True}
    # A tap has no message of its own to answer, so its text reply mentions no one.
    assert group_harness.posts[-1][1]['text'] == f'echo {confirm}'


async def test_clicks_in_channels_outside_the_allowlist_are_ignored(group_harness: SlackHarness) -> None:
    await group_harness.post_interaction(_click('More', channel='C9', scope='open'))

    assert group_harness.agent.inputs == []


def test_group_chats_widen_the_install_scopes() -> None:
    assert 'app_mentions:read' not in Slack.default_scopes()
    assert 'app_mentions:read' in Slack.default_scopes(ALLOW_CHANNEL)
    assert 'channels:history' in Slack.default_scopes(ALLOW_CHANNEL._replace(reply_to='all'))
