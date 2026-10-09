import asyncio
import typing

import pytest
from websockets.exceptions import ConnectionClosedError
from websockets.frames import Close

from conciergent import i18n
from conciergent.groups import GroupPolicy
from conciergent.surfaces.discord.gateway import (
    _INTENT_DIRECT_MESSAGES,
    _INTENT_GUILD_MESSAGES,
    _INTENT_MESSAGE_CONTENT,
)
from tests.surfaces.discord.conftest import USER, BuildHarness, DiscordHarness, interaction_event, message_event


GUILD = 'G1'
CHANNEL = 'C1'
BOT = 'B0'
ALLOW_CHANNEL = GroupPolicy(enabled=True, allowed=frozenset({CHANNEL}))


def _guild_message(
    content: str, *, message_id: str, mentions_bot: bool = True, channel: str = CHANNEL
) -> dict[str, typing.Any]:
    return message_event(
        message_id=message_id,
        content=content,
        guild_id=GUILD,
        channel_id=channel,
        author={'id': USER, 'username': 'amy_01', 'global_name': 'Amy'},
        member={'nick': 'Amy (ops)'},
        mentions=[{'id': BOT}] if mentions_bot else [],
    )


def _guild_click(prompt: str, *, interaction_id: str, user: str = USER) -> dict[str, typing.Any]:
    return interaction_event(
        f'suggestion:exclusive:0:0:{prompt}',
        interaction_id=interaction_id,
        guild_id=GUILD,
        channel_id=CHANNEL,
        user=None,
        member={'user': {'id': user, 'username': f'name-{user}'}},
    )


async def _ready(discord_app: BuildHarness, groups: GroupPolicy) -> DiscordHarness:
    # READY tells the gateway its own user id, which mention detection needs.
    harness = await discord_app(groups=groups)
    await harness.gateway._handle_dispatch('READY', {'session_id': 's', 'user': {'id': BOT}})
    return harness


@pytest.fixture
async def group_harness(discord_app: BuildHarness) -> DiscordHarness:
    return await _ready(discord_app, ALLOW_CHANNEL)


async def test_a_mention_runs_a_group_turn_with_the_mention_stripped(group_harness: DiscordHarness) -> None:
    await group_harness.send(_guild_message(f'<@{BOT}> hi there', message_id='M1'))

    assert group_harness.agent.inputs == ['hi there']
    call = group_harness.agent.calls[0]
    assert call['principal'] == f'discord:{USER}'
    assert call['speaker'] == 'Amy (ops)'
    assert await group_harness.message_store.load_history(f'discord:group:{CHANNEL}')
    channel, payload = group_harness.messages[0]
    assert channel == CHANNEL
    # The text reply is a native reply to the member's message.
    assert payload['message_reference'] == {'message_id': 'M1', 'fail_if_not_exists': False}


async def test_messages_without_a_mention_are_ignored_unless_reply_to_all(discord_app: BuildHarness) -> None:
    mention_only = await _ready(discord_app, ALLOW_CHANNEL)
    every_message = await _ready(discord_app, ALLOW_CHANNEL._replace(reply_to='all'))

    await mention_only.send(_guild_message('chatting', message_id='M2', mentions_bot=False))
    await every_message.send(_guild_message('chatting', message_id='M3', mentions_bot=False))

    assert mention_only.agent.inputs == []
    assert every_message.agent.inputs == ['chatting']


async def test_a_whole_server_can_be_allowed(discord_app: BuildHarness) -> None:
    harness = await _ready(discord_app, GroupPolicy(enabled=True, allowed=frozenset({GUILD})))

    await harness.send(_guild_message(f'<@!{BOT}> hi', message_id='M4', channel='thread-9'))

    assert harness.agent.inputs == ['hi']


async def test_channels_outside_the_allowlist_are_ignored(group_harness: DiscordHarness) -> None:
    await group_harness.send(_guild_message(f'<@{BOT}> hi', message_id='M5', channel='C9'))

    assert group_harness.agent.inputs == []


async def test_groups_are_ignored_when_a_server_needs_per_user_authorization(group_harness: DiscordHarness) -> None:
    group_harness.agent.groups_supported = False

    await group_harness.send(_guild_message(f'<@{BOT}> hi', message_id='M6'))

    assert group_harness.agent.inputs == []


async def test_a_click_while_groups_cannot_be_served_is_acknowledged_silently(group_harness: DiscordHarness) -> None:
    group_harness.agent.groups_supported = False

    await group_harness.click(_guild_click('More', interaction_id='I3'))

    assert group_harness.agent.inputs == []
    assert group_harness.interaction_responses == [('I3', 'interaction-token', {'type': 6})]


async def test_another_members_confirm_click_is_acknowledged_silently(group_harness: DiscordHarness) -> None:
    confirm = i18n.t('approval.confirm', None)
    await group_harness.message_store.park_approval(
        f'discord:group:{CHANNEL}', {'parked': True}, ttl_seconds=60, owner=f'discord:{USER}'
    )

    await group_harness.click(_guild_click(confirm, interaction_id='I1', user='U2'))
    await group_harness.click(_guild_click(confirm, interaction_id='I2'))

    interaction_id, _, acknowledgement = group_harness.interaction_responses[0]
    assert (interaction_id, acknowledgement) == ('I1', {'type': 6})
    assert group_harness.agent.inputs == [confirm]
    assert group_harness.agent.calls[0]['pending_approval'] == {'parked': True}
    assert 'message_reference' not in group_harness.messages[-1][1]


async def test_intents_widen_with_group_chats(discord_app: BuildHarness) -> None:
    direct_only = await discord_app()
    mention_groups = await discord_app(groups=ALLOW_CHANNEL)
    every_message = await discord_app(groups=ALLOW_CHANNEL._replace(reply_to='all'))

    assert direct_only.gateway._intents() == _INTENT_DIRECT_MESSAGES
    assert mention_groups.gateway._intents() == _INTENT_DIRECT_MESSAGES | _INTENT_GUILD_MESSAGES
    assert every_message.gateway._intents() & _INTENT_MESSAGE_CONTENT


async def test_a_refused_message_content_intent_falls_back_to_mentions(
    discord_app: BuildHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = await discord_app(groups=ALLOW_CHANNEL._replace(reply_to='all'))
    requested: list[int] = []

    async def connect_once() -> None:
        requested.append(harness.gateway._intents())
        if len(requested) == 1:
            raise ConnectionClosedError(Close(4014, 'Disallowed intent(s)'), None)
        # The second connect is the fallback, so end the test by stopping the loop.
        raise asyncio.CancelledError

    monkeypatch.setattr(harness.gateway, '_connect_once', connect_once)

    with pytest.raises(asyncio.CancelledError):
        await harness.gateway.run()

    assert requested[0] & _INTENT_MESSAGE_CONTENT
    assert requested[1] == _INTENT_DIRECT_MESSAGES | _INTENT_GUILD_MESSAGES
    assert harness.gateway._reply_to() == 'mention'
