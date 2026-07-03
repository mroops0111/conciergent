import logging
import typing

import httpx

from conciergent import i18n
from conciergent.defaults import DEFAULTS
from conciergent.i18n.lang import Lang
from conciergent.reply import Card, Link, ReplySurface, Section
from conciergent.runtime import StatefulOAuthBridge
from conciergent.store.message import MessageStore
from conciergent.surfaces.slack import render


logger = logging.getLogger(__name__)

_API_BASE_URL = 'https://slack.com/api'


class SlackMessenger:
    """A thin async client for the handful of Slack Web API calls the surface needs."""

    def __init__(self, bot_token: str, *, timeout_seconds: float = DEFAULTS.surface.api_timeout_seconds) -> None:
        self._client = httpx.AsyncClient(
            base_url=_API_BASE_URL,
            timeout=timeout_seconds,
            headers={
                'Authorization': f'Bearer {bot_token}',
                'Content-Type': 'application/json; charset=utf-8',
            },
        )

    async def __aenter__(self) -> 'SlackMessenger':
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self._client.aclose()

    async def post_message(self, channel: str, payload: dict[str, typing.Any], *, thread_ts: str | None = None) -> None:
        body = {'channel': channel, **payload}
        if thread_ts is not None:
            body['thread_ts'] = thread_ts
        response = await self._client.post('/chat.postMessage', json=body)
        _raise_on_error(response, 'chat.postMessage')

    async def respond_via_response_url(self, response_url: str, payload: dict[str, typing.Any]) -> None:
        response = await self._client.post(response_url, json=payload)
        response.raise_for_status()

    async def get_lang(self, user_id: str) -> Lang | None:
        """Resolve the user's UI language from their Slack profile locale, or None when unavailable."""
        try:
            # include_locale=true forces users.info to return the locale, e.g. "zh-TW".
            response = await self._client.get('/users.info', params={'user': user_id, 'include_locale': 'true'})
            response.raise_for_status()
            data = response.json()
            if not data.get('ok'):
                return None
            locale = (data.get('user') or {}).get('locale') or ''
        except httpx.HTTPError:
            return None
        try:
            return Lang(locale) if locale else None
        except ValueError:
            return None


class SlackReplySurface(ReplySurface):
    """Render replies as Slack Block Kit messages, threaded to the triggering message."""

    def __init__(
        self,
        messenger: SlackMessenger,
        *,
        channel: str,
        thread_ts: str | None = None,
        response_url: str | None = None,
        interacted_message: dict[str, typing.Any] | None = None,
        button_label: str = '',
        lang: Lang | None = None,
        brand_color: str = render.BRAND_COLOR,
        destructive_color: str = render.DESTRUCTIVE_COLOR,
    ) -> None:
        self._messenger = messenger
        self._channel = channel
        self._thread_ts = thread_ts
        self._response_url = response_url
        self._interacted_message = interacted_message
        self._button_label = button_label
        self._lang = lang
        self._brand_color = brand_color
        self._destructive_color = destructive_color
        # Set once show_processing patches the clicked message; the next send finalizes it to "selected".
        self._processing_active = False

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
        await self._finalize_processing_if_active()
        await self._messenger.post_message(self._channel, {'text': text}, thread_ts=self._thread_ts)

    @typing.override
    async def send_card(self, card: Card, *, destructive: bool = False) -> None:
        await self._finalize_processing_if_active()
        payload = render.build_card_payload(
            card, destructive=destructive, brand_color=self._brand_color, destructive_color=self._destructive_color
        )
        await self._messenger.post_message(self._channel, payload, thread_ts=self._thread_ts)

    @typing.override
    async def send_carousel(self, cards: list[Card]) -> None:
        await self._finalize_processing_if_active()
        payload = render.build_carousel_payload(cards, brand_color=self._brand_color)
        await self._messenger.post_message(self._channel, payload, thread_ts=self._thread_ts)

    @typing.override
    async def show_processing(self) -> None:
        """Patch the clicked message to disable its buttons and show a processing line, a no-op on plain events.

        The next reply replaces that line with a "selected" line via the finalize helper.
        """
        if self._response_url is None or self._interacted_message is None:
            return
        # The patch is cosmetic and must never abort the turn.
        try:
            status_text = i18n.t('interaction.processing', self._lang, label=self._button_label)
            patch = render.build_processing_patch(self._interacted_message, status_text)
            await self._messenger.respond_via_response_url(self._response_url, patch)
        except Exception:
            logger.warning('Slack processing patch failed', exc_info=True)
            return
        self._processing_active = True

    async def _finalize_processing_if_active(self) -> None:
        # Replace the "processing" line show_processing appended with the "selected" line, then clear the flag.
        if not self._processing_active or self._response_url is None or self._interacted_message is None:
            return
        self._processing_active = False
        try:
            status_text = i18n.t('interaction.selected', self._lang, label=self._button_label)
            patch = render.build_processing_patch(self._interacted_message, status_text)
            await self._messenger.respond_via_response_url(self._response_url, patch)
        except Exception:
            logger.warning('Slack selected patch failed', exc_info=True)


class SlackOAuthBridge(StatefulOAuthBridge):
    """Push the authorize URL into the conversation as a card with a primary link button."""

    def __init__(
        self,
        message_store: MessageStore,
        messenger: SlackMessenger,
        *,
        channel: str,
        thread_ts: str | None = None,
        lang: Lang | None = None,
        wait_timeout_seconds: float | None = None,
        brand_color: str = render.BRAND_COLOR,
    ) -> None:
        if wait_timeout_seconds is not None:
            super().__init__(message_store, wait_timeout_seconds=wait_timeout_seconds)
        else:
            super().__init__(message_store)
        self._messenger = messenger
        self._channel = channel
        self._thread_ts = thread_ts
        self._lang = lang
        self._brand_color = brand_color

    @typing.override
    async def _render_authorization_ui(self, authorize_url: str) -> None:
        card = Card(
            header=i18n.t('slack.oauth.header', self._lang),
            sections=[Section(text=i18n.t('slack.oauth.body', self._lang))],
            links=[Link(label=i18n.t('slack.oauth.button', self._lang), url=authorize_url)],
        )
        payload = render.build_card_payload(card, brand_color=self._brand_color)
        # Slack shows the top-level text as the push-notification preview, so use the dedicated notification copy.
        payload['text'] = i18n.t('slack.oauth.notification', self._lang)
        await self._messenger.post_message(self._channel, payload, thread_ts=self._thread_ts)


def _raise_on_error(response: httpx.Response, call: str) -> None:
    # Slack reports failures as HTTP 200 with ok=false, so both layers need checking.
    response.raise_for_status()
    data = response.json()
    if not data.get('ok'):
        raise RuntimeError(f'Slack {call} failed: {data.get("error")}')
