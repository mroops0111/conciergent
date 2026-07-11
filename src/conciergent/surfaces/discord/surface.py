import logging
import typing

import httpx

from conciergent import i18n
from conciergent.defaults import DEFAULTS
from conciergent.i18n.lang import Lang
from conciergent.reply import Card, Link, ReplySurface, Section
from conciergent.runtime import StatefulOAuthBridge
from conciergent.store.message import MessageStore
from conciergent.surfaces.discord import render


logger = logging.getLogger(__name__)

_API_BASE_URL = 'https://discord.com/api/v10'

# Interaction callback type 7 updates the clicked message in place, which also acknowledges the interaction.
_UPDATE_MESSAGE = 7


class DiscordMessenger:
    """A thin async client for the handful of Discord REST calls the surface needs."""

    def __init__(
        self, bot_token: str, *, timeout_seconds: float = DEFAULTS.surface.discord.api_timeout_seconds
    ) -> None:
        self._client = httpx.AsyncClient(
            base_url=_API_BASE_URL,
            timeout=timeout_seconds,
            headers={
                'Authorization': f'Bot {bot_token}',
                'Content-Type': 'application/json',
            },
        )

    async def __aenter__(self) -> 'DiscordMessenger':
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self._client.aclose()

    async def create_message(self, channel_id: str, payload: dict[str, typing.Any]) -> None:
        response = await self._client.post(f'/channels/{channel_id}/messages', json=payload)
        response.raise_for_status()

    async def trigger_typing(self, channel_id: str) -> None:
        response = await self._client.post(f'/channels/{channel_id}/typing')
        response.raise_for_status()

    async def respond_to_interaction(self, interaction_id: str, token: str, payload: dict[str, typing.Any]) -> None:
        response = await self._client.post(f'/interactions/{interaction_id}/{token}/callback', json=payload)
        response.raise_for_status()


class Interaction(typing.NamedTuple):
    """The button-click context a turn carries when it starts from a component rather than a typed message."""

    interaction_id: str
    token: str


class DiscordReplySurface(ReplySurface):
    """Render replies as Discord messages, embeds with button rows, in the user's direct-message channel."""

    def __init__(
        self,
        messenger: DiscordMessenger,
        *,
        channel_id: str,
        interaction: Interaction | None = None,
        lang: Lang | None = None,
        brand_color: str = render.BRAND_COLOR,
        destructive_color: str = render.DESTRUCTIVE_COLOR,
    ) -> None:
        self._messenger = messenger
        self._channel_id = channel_id
        self._interaction = interaction
        self._lang = lang
        self._brand_color = brand_color
        self._destructive_color = destructive_color

    @property
    @typing.override
    def text_formatting_instruction(self) -> str:
        return render.TEXT_FORMATTING_INSTRUCTION

    @property
    @typing.override
    def lang(self) -> Lang | None:
        return self._lang

    @typing.override
    async def send_text(self, text: str) -> None:
        await self._messenger.create_message(self._channel_id, render.build_text_message(text))

    @typing.override
    async def send_card(self, card: Card, *, destructive: bool = False) -> None:
        payload = render.build_card_message(
            card, destructive=destructive, brand_color=self._brand_color, destructive_color=self._destructive_color
        )
        await self._messenger.create_message(self._channel_id, payload)

    @typing.override
    async def send_carousel(self, cards: list[Card]) -> None:
        payload = render.build_carousel_message(cards, brand_color=self._brand_color)
        await self._messenger.create_message(self._channel_id, payload)

    @typing.override
    async def show_processing(self) -> None:
        """Acknowledge a button click by disabling its message's buttons, or show a typing hint on a plain message.

        Discord returns only a component's custom_id on click, not its label, so no per-button status line is shown;
        the reply itself, posted as a new message, is the confirmation. Both signals are cosmetic and never abort.
        """
        try:
            if self._interaction is not None:
                await self._messenger.respond_to_interaction(
                    self._interaction.interaction_id,
                    self._interaction.token,
                    {'type': _UPDATE_MESSAGE, 'data': render.strip_components()},
                )
            else:
                await self._messenger.trigger_typing(self._channel_id)
        except Exception:
            logger.debug('Discord processing hint failed', exc_info=True)


class DiscordOAuthBridge(StatefulOAuthBridge):
    """Push the authorize URL into the conversation as an embed with a link button."""

    def __init__(
        self,
        message_store: MessageStore,
        messenger: DiscordMessenger,
        *,
        channel_id: str,
        lang: Lang | None = None,
        wait_timeout_seconds: float | None = None,
        brand_color: str = render.BRAND_COLOR,
    ) -> None:
        if wait_timeout_seconds is not None:
            super().__init__(message_store, wait_timeout_seconds=wait_timeout_seconds)
        else:
            super().__init__(message_store)
        self._messenger = messenger
        self._channel_id = channel_id
        self._lang = lang
        self._brand_color = brand_color

    @typing.override
    async def _render_authorization_ui(self, authorize_url: str) -> None:
        card = Card(
            header=i18n.t('discord.oauth.header', self._lang),
            sections=[Section(text=i18n.t('discord.oauth.body', self._lang))],
            links=[Link(label=i18n.t('discord.oauth.button', self._lang), url=authorize_url)],
        )
        payload = render.build_card_message(card, brand_color=self._brand_color)
        await self._messenger.create_message(self._channel_id, payload)
