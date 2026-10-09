import collections.abc
import json
import typing
import urllib.parse

import pytest

from conciergent import i18n
from conciergent.groups import GroupPolicy
from conciergent.surfaces.slack.app import Slack
from tests.surfaces.slack.conftest import TEAM, USER, SlackHarness


SignHeaders = collections.abc.Callable[[bytes], dict[str, str]]
BuildHarness = collections.abc.Callable[..., typing.Awaitable[SlackHarness]]

CHANNEL = 'C1'
BOT = 'UBOT'
TS = '200.300'


def _event(*, event_id: str, text: str, channel: str = CHANNEL, **overrides: typing.Any) -> bytes:
    event = {'type': 'app_mention', 'user': USER, 'channel': channel, 'ts': TS, 'text': text, **overrides}
    payload = {
        'type': 'event_callback',
        'event_id': event_id,
        'team_id': TEAM,
        'authorizations': [{'user_id': BOT, 'is_bot': True}],
        'event': event,
    }
    return json.dumps(payload).encode()


def _click(*, value: str, user: str = USER, channel: str = CHANNEL, scope: str = 'exclusive') -> bytes:
    payload = {
        'type': 'block_actions',
        'team': {'id': TEAM},
        'user': {'id': user},
        'channel': {'id': channel},
        'message': {'ts': TS, 'thread_ts': TS},
        'actions': [{'action_id': f'suggestion:{scope}:0:0', 'value': value}],
    }
    return urllib.parse.urlencode({'payload': json.dumps(payload)}).encode()


@pytest.fixture
async def group_harness(slack_app: BuildHarness) -> SlackHarness:
    harness = await slack_app(groups=GroupPolicy(enabled=True, allowed=frozenset({CHANNEL})))
    await harness.install()
    return harness


async def _post(harness: SlackHarness, sign_headers: SignHeaders, path: str, body: bytes) -> None:
    await harness.client.post(path, content=body, headers=sign_headers(body))


async def test_a_mention_runs_a_group_turn_in_its_thread(
    group_harness: SlackHarness, sign_headers: SignHeaders
) -> None:
    await _post(group_harness, sign_headers, '/slack/events', _event(event_id='E1', text=f'<@{BOT}> what is up'))

    assert group_harness.agent.inputs == ['what is up']
    call = group_harness.agent.calls[0]
    assert call['principal'] == f'slack:{TEAM}:{USER}'
    assert call['speaker'] == f'name-{USER}'
    assert call['bridge'] is None
    assert await group_harness.message_store.load_history(f'slack:{TEAM}:group:{CHANNEL}:{TS}')
    channel, payload = group_harness.posts[0]
    assert channel == CHANNEL and payload['thread_ts'] == TS


async def test_channels_outside_the_allowlist_are_ignored(
    group_harness: SlackHarness, sign_headers: SignHeaders
) -> None:
    await _post(group_harness, sign_headers, '/slack/events', _event(event_id='E2', text=f'<@{BOT}> hi', channel='C9'))

    assert group_harness.agent.inputs == []


async def test_plain_channel_messages_need_reply_to_all(slack_app: BuildHarness, sign_headers: SignHeaders) -> None:
    mention_only = await slack_app(groups=GroupPolicy(enabled=True, allowed=frozenset({CHANNEL})))
    await mention_only.install()
    every_message = await slack_app(groups=GroupPolicy(enabled=True, allowed=frozenset({CHANNEL}), reply_to='all'))
    message = _event(event_id='E3', text='hello all', type='message', channel_type='channel')

    await _post(mention_only, sign_headers, '/slack/events', message)
    await _post(
        every_message,
        sign_headers,
        '/slack/events',
        _event(event_id='E4', text='hello all', type='message', channel_type='channel'),
    )
    # In reply_to=all the mention also arrives as a message event, so app_mention is skipped to answer once.
    await _post(every_message, sign_headers, '/slack/events', _event(event_id='E5', text=f'<@{BOT}> hi'))

    assert mention_only.agent.inputs == []
    assert every_message.agent.inputs == ['hello all']


async def test_groups_are_ignored_when_a_server_needs_per_user_authorization(
    group_harness: SlackHarness, sign_headers: SignHeaders
) -> None:
    group_harness.agent.groups_supported = False

    await _post(group_harness, sign_headers, '/slack/events', _event(event_id='E6', text=f'<@{BOT}> hi'))

    assert group_harness.agent.inputs == []


async def test_another_members_confirm_tap_is_answered_privately_and_leaves_the_card_for_its_owner(
    group_harness: SlackHarness, sign_headers: SignHeaders
) -> None:
    confirm = i18n.t('approval.confirm', None)
    conversation = f'slack:{TEAM}:group:{CHANNEL}:{TS}'
    owner = f'slack:{TEAM}:{USER}'
    await group_harness.message_store.park_approval(conversation, {'parked': True}, ttl_seconds=60, owner=owner)

    await _post(group_harness, sign_headers, '/slack/interactions', _click(value=confirm, user='U2'))
    await _post(group_harness, sign_headers, '/slack/interactions', _click(value=confirm))

    assert group_harness.ephemerals == [('U2', i18n.t('approval.unavailable', None))]
    assert group_harness.agent.inputs == [confirm]
    assert group_harness.agent.calls[0]['pending_approval'] == {'parked': True}


async def test_clicks_in_channels_outside_the_allowlist_are_ignored(
    group_harness: SlackHarness, sign_headers: SignHeaders
) -> None:
    await _post(group_harness, sign_headers, '/slack/interactions', _click(value='More', channel='C9', scope='open'))

    assert group_harness.agent.inputs == []


def test_group_chats_widen_the_install_scopes() -> None:
    assert 'app_mentions:read' not in Slack.default_scopes()
    assert 'app_mentions:read' in Slack.default_scopes(GroupPolicy(enabled=True))
    assert 'channels:history' in Slack.default_scopes(GroupPolicy(enabled=True, reply_to='all'))
