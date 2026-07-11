import collections.abc
import typing

from conciergent.store.message import MessageStore
from conciergent.surfaces.discord.surface import DiscordOAuthBridge
from tests.surfaces.discord.conftest import CHANNEL, USER, DiscordHarness


async def test_direct_message_runs_a_turn_and_replies(
    harness: DiscordHarness, message_event: collections.abc.Callable[..., dict[str, typing.Any]]
) -> None:
    await harness.gateway._handle_dispatch('MESSAGE_CREATE', message_event(content='hello'))
    assert harness.agent.inputs == ['hello']
    channel_id, payload = harness.messages[0]
    assert channel_id == CHANNEL
    assert payload['content'] == 'echo hello'
    # A plain message has no interaction to acknowledge, so the processing hint is a typing indicator.
    assert harness.typing_hints == [CHANNEL]


async def test_bot_messages_are_ignored(
    harness: DiscordHarness, message_event: collections.abc.Callable[..., dict[str, typing.Any]]
) -> None:
    await harness.gateway._handle_dispatch('MESSAGE_CREATE', message_event(author={'id': 'B1', 'bot': True}))
    assert harness.agent.inputs == []


async def test_guild_messages_are_ignored(
    harness: DiscordHarness, message_event: collections.abc.Callable[..., dict[str, typing.Any]]
) -> None:
    await harness.gateway._handle_dispatch('MESSAGE_CREATE', message_event(guild_id='G1'))
    assert harness.agent.inputs == []


async def test_a_duplicate_message_is_dropped(
    harness: DiscordHarness, message_event: collections.abc.Callable[..., dict[str, typing.Any]]
) -> None:
    event = message_event(message_id='M9')
    await harness.gateway._handle_dispatch('MESSAGE_CREATE', event)
    await harness.gateway._handle_dispatch('MESSAGE_CREATE', event)
    assert harness.agent.inputs == ['hello']


async def test_interaction_refeeds_the_prompt_and_acknowledges_by_disabling_buttons(
    harness: DiscordHarness, interaction_event: collections.abc.Callable[..., dict[str, typing.Any]]
) -> None:
    await harness.gateway._handle_dispatch('INTERACTION_CREATE', interaction_event('suggestion:open:0:0:Show details'))
    assert harness.agent.inputs == ['Show details']
    interaction_id, token, payload = harness.interaction_responses[0]
    assert (interaction_id, token) == ('I1', 'interaction-token')
    assert payload['type'] == 7
    assert payload['data']['components'] == []


async def test_a_non_component_interaction_is_ignored(
    harness: DiscordHarness, interaction_event: collections.abc.Callable[..., dict[str, typing.Any]]
) -> None:
    await harness.gateway._handle_dispatch(
        'INTERACTION_CREATE', interaction_event('suggestion:open:0:0:x', interaction_type=2)
    )
    assert harness.agent.inputs == []


async def test_a_duplicate_interaction_is_dropped(
    harness: DiscordHarness, interaction_event: collections.abc.Callable[..., dict[str, typing.Any]]
) -> None:
    event = interaction_event('suggestion:open:0:0:again', interaction_id='I9')
    await harness.gateway._handle_dispatch('INTERACTION_CREATE', event)
    await harness.gateway._handle_dispatch('INTERACTION_CREATE', event)
    assert harness.agent.inputs == ['again']


async def test_oauth_authorization_ui_posts_a_link_button(message_store: MessageStore) -> None:
    posted: list[tuple[str, dict[str, typing.Any]]] = []

    class RecordingMessenger:
        async def create_message(self, channel_id: str, payload: dict[str, typing.Any]) -> None:
            posted.append((channel_id, payload))

    bridge = DiscordOAuthBridge(message_store, typing.cast(typing.Any, RecordingMessenger()), channel_id=CHANNEL)
    await bridge._render_authorization_ui('https://auth.example.com/authorize?state=abc')
    channel_id, payload = posted[0]
    assert channel_id == CHANNEL
    button = payload['components'][0]['components'][0]
    assert button['style'] == 5
    assert button['url'] == 'https://auth.example.com/authorize?state=abc'


def test_user_identity_is_the_principal_segment() -> None:
    # A guard that the surface keys credentials by the Discord user, matching the reference principal shape.
    from conciergent.identity import ChatSurface, make_principal

    assert make_principal(ChatSurface.discord, USER) == f'discord:{USER}'
