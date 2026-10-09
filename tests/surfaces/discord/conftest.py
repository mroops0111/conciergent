import collections.abc
import dataclasses
import typing

import pytest

from conciergent.store.credential import CredentialStore
from conciergent.store.message import MessageStore
from conciergent.surfaces.discord import gateway as gateway_module
from conciergent.surfaces.discord.gateway import DiscordGateway, DiscordGatewaySettings
from tests.conftest import StubRunner


USER = 'U1'
CHANNEL = 'D1'


@dataclasses.dataclass
class DiscordHarness:
    gateway: DiscordGateway
    agent: StubRunner
    messages: list[tuple[str, dict[str, typing.Any]]]
    interaction_responses: list[tuple[str, str, dict[str, typing.Any]]]
    typing_hints: list[str]
    message_store: MessageStore
    credential_store: CredentialStore

    async def send(self, message: dict[str, typing.Any]) -> None:
        """Deliver a MESSAGE_CREATE dispatch, as the gateway socket would."""
        await self.gateway._handle_dispatch('MESSAGE_CREATE', message)

    async def click(self, interaction: dict[str, typing.Any]) -> None:
        """Deliver an INTERACTION_CREATE dispatch, as the gateway socket would."""
        await self.gateway._handle_dispatch('INTERACTION_CREATE', interaction)


# A factory fixture's type, so a test can build a harness under its own settings.
BuildHarness = collections.abc.Callable[..., typing.Awaitable[DiscordHarness]]


@pytest.fixture
def discord_app(
    monkeypatch: pytest.MonkeyPatch, message_store: MessageStore, credential_store: CredentialStore
) -> BuildHarness:
    # A factory so a test can run the gateway under whatever DiscordGatewaySettings it needs.
    async def build(**settings_overrides: typing.Any) -> DiscordHarness:
        agent = StubRunner()
        messages: list[tuple[str, dict[str, typing.Any]]] = []
        interaction_responses: list[tuple[str, str, dict[str, typing.Any]]] = []
        typing_hints: list[str] = []

        class RecordingMessenger:
            def __init__(self, bot_token: str, *, timeout_seconds: float = 30.0) -> None:
                self.bot_token = bot_token

            async def __aenter__(self) -> 'RecordingMessenger':
                return self

            async def __aexit__(self, *exc_info: object) -> None:
                return None

            async def create_message(self, channel_id: str, payload: dict[str, typing.Any]) -> None:
                messages.append((channel_id, payload))

            async def trigger_typing(self, channel_id: str) -> None:
                typing_hints.append(channel_id)

            async def respond_to_interaction(
                self, interaction_id: str, token: str, payload: dict[str, typing.Any]
            ) -> None:
                interaction_responses.append((interaction_id, token, payload))

        monkeypatch.setattr(gateway_module, 'DiscordMessenger', RecordingMessenger)
        gateway = DiscordGateway(
            settings=DiscordGatewaySettings(bot_token='bot-token', **settings_overrides),
            message_store=message_store,
            runner=agent.as_runner(),
            credential_store=credential_store,
        )
        return DiscordHarness(
            gateway=gateway,
            agent=agent,
            messages=messages,
            interaction_responses=interaction_responses,
            typing_hints=typing_hints,
            message_store=message_store,
            credential_store=credential_store,
        )

    return build


@pytest.fixture
async def harness(discord_app: BuildHarness) -> DiscordHarness:
    return await discord_app()


# Gateway dispatch builders, each the shape Discord delivers, with a direct message as the default.


def message_event(*, message_id: str = 'M1', content: str = 'hello', **overrides: typing.Any) -> dict[str, typing.Any]:
    return {
        'id': message_id,
        'channel_id': CHANNEL,
        'author': {'id': USER, 'bot': False},
        'content': content,
        **overrides,
    }


def interaction_event(
    custom_id: str,
    *,
    interaction_id: str = 'I1',
    interaction_type: int = 3,
    locale: str | None = None,
    **overrides: typing.Any,
) -> dict[str, typing.Any]:
    event: dict[str, typing.Any] = {
        'id': interaction_id,
        'token': 'interaction-token',
        'type': interaction_type,
        'channel_id': CHANNEL,
        'user': {'id': USER},
        'data': {'custom_id': custom_id, 'component_type': 2},
        **overrides,
    }
    if locale is not None:
        event['locale'] = locale
    return event
