import typing

import pytest

from conciergent import i18n
from conciergent.groups import GroupPolicy
from conciergent.surfaces.discord.gateway import (
    _INTENT_DIRECT_MESSAGES,
    _INTENT_GUILD_MESSAGES,
    _INTENT_MESSAGE_CONTENT,
)
from tests.surfaces.discord.conftest import USER, DiscordHarness


GUILD = 'G1'
CHANNEL = 'C1'
BOT = 'B0'


@pytest.fixture
async def group_harness(harness: DiscordHarness) -> DiscordHarness:
    _set_groups(harness, GroupPolicy(enabled=True, allowed=frozenset({CHANNEL})))
    await harness.gateway._handle_dispatch('READY', {'session_id': 's', 'user': {'id': BOT}})
    return harness


def _set_groups(harness: DiscordHarness, groups: GroupPolicy) -> None:
    harness.gateway._settings = harness.gateway._settings._replace(groups=groups)


def _guild_message(
    content: str, *, message_id: str, mentions_bot: bool = True, channel: str = CHANNEL
) -> dict[str, typing.Any]:
    return {
        'id': message_id,
        'guild_id': GUILD,
        'channel_id': channel,
        'author': {'id': USER, 'username': 'amy_01', 'global_name': 'Amy'},
        'member': {'nick': 'Amy (ops)'},
        'mentions': [{'id': BOT}] if mentions_bot else [],
        'content': content,
    }


def _guild_click(prompt: str, *, interaction_id: str, user: str = USER) -> dict[str, typing.Any]:
    return {
        'id': interaction_id,
        'token': 'tok',
        'type': 3,
        'guild_id': GUILD,
        'channel_id': CHANNEL,
        'member': {'user': {'id': user, 'username': f'name-{user}'}},
        'data': {'custom_id': f'suggestion:exclusive:0:0:{prompt}', 'component_type': 2},
    }


async def test_a_mention_runs_a_group_turn_with_the_mention_stripped(group_harness: DiscordHarness) -> None:
    await group_harness.gateway._handle_dispatch(
        'MESSAGE_CREATE', _guild_message(f'<@{BOT}> hi there', message_id='M1')
    )

    assert group_harness.agent.inputs == ['hi there']
    call = group_harness.agent.calls[0]
    assert call['principal'] == f'discord:{USER}'
    assert call['speaker'] == 'Amy (ops)'
    assert call['bridge'] is None
    assert await group_harness.message_store.load_history(f'discord:group:{CHANNEL}')
    channel, payload = group_harness.messages[0]
    assert channel == CHANNEL
    # The text reply is a native reply to the member's message.
    assert payload['message_reference'] == {'message_id': 'M1', 'fail_if_not_exists': False}


async def test_messages_without_a_mention_are_ignored_unless_reply_to_all(group_harness: DiscordHarness) -> None:
    await group_harness.gateway._handle_dispatch(
        'MESSAGE_CREATE', _guild_message('chatting', message_id='M2', mentions_bot=False)
    )
    _set_groups(group_harness, GroupPolicy(enabled=True, allowed=frozenset({CHANNEL}), reply_to='all'))
    await group_harness.gateway._handle_dispatch(
        'MESSAGE_CREATE', _guild_message('chatting', message_id='M3', mentions_bot=False)
    )

    assert group_harness.agent.inputs == ['chatting']


async def test_a_whole_server_can_be_allowed(group_harness: DiscordHarness) -> None:
    _set_groups(group_harness, GroupPolicy(enabled=True, allowed=frozenset({GUILD})))

    await group_harness.gateway._handle_dispatch(
        'MESSAGE_CREATE', _guild_message(f'<@!{BOT}> hi', message_id='M4', channel='thread-9')
    )

    assert group_harness.agent.inputs == ['hi']


async def test_channels_outside_the_allowlist_are_ignored(group_harness: DiscordHarness) -> None:
    await group_harness.gateway._handle_dispatch(
        'MESSAGE_CREATE', _guild_message(f'<@{BOT}> hi', message_id='M5', channel='C9')
    )

    assert group_harness.agent.inputs == []


async def test_groups_are_ignored_when_a_server_needs_per_user_authorization(group_harness: DiscordHarness) -> None:
    group_harness.agent.groups_supported = False

    await group_harness.gateway._handle_dispatch('MESSAGE_CREATE', _guild_message(f'<@{BOT}> hi', message_id='M6'))

    assert group_harness.agent.inputs == []


async def test_another_members_confirm_click_is_acknowledged_silently(group_harness: DiscordHarness) -> None:
    confirm = i18n.t('approval.confirm', None)
    owner = f'discord:{USER}'
    await group_harness.message_store.park_approval(
        f'discord:group:{CHANNEL}', {'parked': True}, ttl_seconds=60, owner=owner
    )

    await group_harness.gateway._handle_dispatch(
        'INTERACTION_CREATE', _guild_click(confirm, interaction_id='I1', user='U2')
    )
    await group_harness.gateway._handle_dispatch('INTERACTION_CREATE', _guild_click(confirm, interaction_id='I2'))

    interaction_id, _, acknowledgement = group_harness.interaction_responses[0]
    assert (interaction_id, acknowledgement) == ('I1', {'type': 6})
    assert group_harness.agent.inputs == [confirm]
    assert group_harness.agent.calls[0]['pending_approval'] == {'parked': True}
    assert 'message_reference' not in group_harness.messages[-1][1]


def test_intents_widen_with_group_chats(harness: DiscordHarness) -> None:
    assert harness.gateway._intents() == _INTENT_DIRECT_MESSAGES
    _set_groups(harness, GroupPolicy(enabled=True))
    assert harness.gateway._intents() == _INTENT_DIRECT_MESSAGES | _INTENT_GUILD_MESSAGES
    _set_groups(harness, GroupPolicy(enabled=True, reply_to='all'))
    assert harness.gateway._intents() & _INTENT_MESSAGE_CONTENT
