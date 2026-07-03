import logging
import typing

import httpx

from conciergent import i18n
from conciergent.defaults import DEFAULTS
from conciergent.i18n.lang import Lang
from conciergent.reply import Card, Link, ReplySurface
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
        lang: Lang | None = None,
        brand_color: str = render.BRAND_COLOR,
        destructive_color: str = render.DESTRUCTIVE_COLOR,
    ) -> None:
        self._messenger = messenger
        self._channel = channel
        self._thread_ts = thread_ts
        self._response_url = response_url
        self._interacted_message = interacted_message
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
        await self._messenger.post_message(self._channel, {'text': text}, thread_ts=self._thread_ts)

    @typing.override
    async def send_card(self, card: Card, *, destructive: bool = False) -> None:
        payload = render.build_card_payload(
            card, destructive=destructive, brand_color=self._brand_color, destructive_color=self._destructive_color
        )
        await self._messenger.post_message(self._channel, payload, thread_ts=self._thread_ts)

    @typing.override
    async def send_carousel(self, cards: list[Card]) -> None:
        payload = render.build_carousel_payload(cards, brand_color=self._brand_color)
        await self._messenger.post_message(self._channel, payload, thread_ts=self._thread_ts)

    @typing.override
    async def show_processing(self) -> None:
        """Patch the interacted message to disable its buttons, a no-op on plain message events."""
        if self._response_url is None or self._interacted_message is None:
            return
        # The patch is cosmetic and must never abort the turn.
        try:
            status_text = i18n.t('slack.processing', self._lang)
            patch = render.build_processing_patch(self._interacted_message, status_text)
            await self._messenger.respond_via_response_url(self._response_url, patch)
        except Exception:
            logger.warning('Slack processing patch failed', exc_info=True)


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
            title=i18n.t('authorization.header', self._lang),
            links=[Link(text=i18n.t('authorization.button', self._lang), url=authorize_url)],
        )
        payload = render.build_card_payload(card, brand_color=self._brand_color)
        await self._messenger.post_message(self._channel, payload, thread_ts=self._thread_ts)


def _raise_on_error(response: httpx.Response, call: str) -> None:
    # Slack reports failures as HTTP 200 with ok=false, so both layers need checking.
    response.raise_for_status()
    data = response.json()
    if not data.get('ok'):
        raise RuntimeError(f'Slack {call} failed: {data.get("error")}')
