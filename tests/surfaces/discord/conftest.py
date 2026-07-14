import collections.abc
import dataclasses
import typing

import pytest

from conciergent.agent.runner import ChatRunner
from conciergent.store.credential import CredentialStore
from conciergent.store.message import MessageStore
from conciergent.surfaces.discord import gateway as gateway_module
from conciergent.surfaces.discord.gateway import DiscordGateway, DiscordGatewaySettings
from tests.surfaces.conftest import EchoAgent


USER = 'U1'
CHANNEL = 'D1'


@dataclasses.dataclass
class DiscordHarness:
    gateway: DiscordGateway
    agent: EchoAgent
    messages: list[tuple[str, dict[str, typing.Any]]]
    interaction_responses: list[tuple[str, str, dict[str, typing.Any]]]
    typing_hints: list[str]
    message_store: MessageStore
    credential_store: CredentialStore


@pytest.fixture
async def harness(
    monkeypatch: pytest.MonkeyPatch, message_store: MessageStore, credential_store: CredentialStore
) -> DiscordHarness:
    agent = EchoAgent()
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

        async def respond_to_interaction(self, interaction_id: str, token: str, payload: dict[str, typing.Any]) -> None:
            interaction_responses.append((interaction_id, token, payload))

    monkeypatch.setattr(gateway_module, 'DiscordMessenger', RecordingMessenger)
    gateway = DiscordGateway(
        settings=DiscordGatewaySettings(bot_token='bot-token'),
        message_store=message_store,
        runner=typing.cast(ChatRunner, agent),
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


@pytest.fixture
def message_event() -> collections.abc.Callable[..., dict[str, typing.Any]]:
    def _event(*, message_id: str = 'M1', content: str = 'hello', **overrides: typing.Any) -> dict[str, typing.Any]:
        return {
            'id': message_id,
            'channel_id': CHANNEL,
            'author': {'id': USER, 'bot': False},
            'content': content,
            **overrides,
        }

    return _event


@pytest.fixture
def interaction_event() -> collections.abc.Callable[..., dict[str, typing.Any]]:
    def _event(
        custom_id: str,
        *,
        interaction_id: str = 'I1',
        interaction_type: int = 3,
        locale: str | None = None,
    ) -> dict[str, typing.Any]:
        event: dict[str, typing.Any] = {
            'id': interaction_id,
            'token': 'interaction-token',
            'type': interaction_type,
            'channel_id': CHANNEL,
            'user': {'id': USER},
            'data': {'custom_id': custom_id, 'component_type': 2},
        }
        if locale is not None:
            event['locale'] = locale
        return event

    return _event
