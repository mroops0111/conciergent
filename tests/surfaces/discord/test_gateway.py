import collections.abc
import typing

import pytest
from websockets.exceptions import ConnectionClosedError
from websockets.frames import Close

from conciergent.i18n.lang import Lang
from conciergent.store.message import MessageStore
from conciergent.surfaces.discord.gateway import _FATAL_CLOSE_CODES, _received_close_code
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


async def test_an_interaction_persists_and_overwrites_the_users_locale(
    harness: DiscordHarness, interaction_event: collections.abc.Callable[..., dict[str, typing.Any]]
) -> None:
    principal = f'discord:{USER}'
    await harness.gateway._handle_dispatch(
        'INTERACTION_CREATE', interaction_event('suggestion:open:0:0:x', locale='en')
    )
    assert await harness.credential_store.get_locale(principal) == 'en'
    # A later interaction in another language overwrites it, so a language change takes effect.
    await harness.gateway._handle_dispatch(
        'INTERACTION_CREATE', interaction_event('suggestion:open:0:0:x', interaction_id='I2', locale='fr')
    )
    assert await harness.credential_store.get_locale(principal) == 'fr'


async def test_resolve_lang_reads_the_stored_locale_then_none(harness: DiscordHarness) -> None:
    await harness.credential_store.set_locale(f'discord:{USER}', 'en')
    # A typed message carries no locale, so the one stored from an earlier interaction is reused.
    assert await harness.gateway._resolve_lang(f'discord:{USER}', None) == Lang('en')
    # A user with nothing stored resolves to None, so the reply mirrors the user's own message.
    assert await harness.gateway._resolve_lang('discord:never', None) is None


def test_a_fatal_close_code_is_recognized() -> None:
    error = ConnectionClosedError(Close(4004, 'Authentication failed'), None)
    assert _received_close_code(error) in _FATAL_CLOSE_CODES


def test_a_transient_close_code_is_not_fatal() -> None:
    # A generic drop should reconnect, so its code must stay out of the fatal set.
    error = ConnectionClosedError(Close(4000, 'Unknown error'), None)
    assert _received_close_code(error) not in _FATAL_CLOSE_CODES


async def test_run_stops_on_a_fatal_close_instead_of_reconnecting(
    harness: DiscordHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def raise_fatal() -> None:
        raise ConnectionClosedError(Close(4004, 'Authentication failed'), None)

    monkeypatch.setattr(harness.gateway, '_connect_once', raise_fatal)
    # A fatal close re-raises rather than looping, so the awaited run() returns control instead of hanging.
    with pytest.raises(ConnectionClosedError):
        await harness.gateway.run()
