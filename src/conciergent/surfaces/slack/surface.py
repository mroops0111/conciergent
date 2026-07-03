import logging
import typing

import httpx
import typing_extensions

from ...reply import Card, Link, ReplySurface
from ...runtime import StatefulOAuthBridge
from ...stores.base import OAuthCodeStore
from . import render


logger = logging.getLogger(__name__)

_API_BASE_URL = 'https://slack.com/api'
_TIMEOUT_SECONDS = 30.0


class SlackMessenger:
    """A thin async client for the handful of Slack Web API calls the surface needs."""

    def __init__(self, bot_token: str) -> None:
        self._client = httpx.AsyncClient(
            base_url=_API_BASE_URL,
            timeout=_TIMEOUT_SECONDS,
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
        processing_text: str = 'Working on it...',
    ) -> None:
        self._messenger = messenger
        self._channel = channel
        self._thread_ts = thread_ts
        self._response_url = response_url
        self._interacted_message = interacted_message
        self._processing_text = processing_text

    @property
    @typing_extensions.override
    def text_formatting_instruction(self) -> str:
        return render.TEXT_FORMATTING_INSTRUCTION

    @typing_extensions.override
    async def send_text(self, text: str) -> None:
        await self._messenger.post_message(self._channel, {'text': text}, thread_ts=self._thread_ts)

    @typing_extensions.override
    async def send_card(self, card: Card, *, destructive: bool = False) -> None:
        payload = render.build_card_payload(card, destructive=destructive)
        await self._messenger.post_message(self._channel, payload, thread_ts=self._thread_ts)

    @typing_extensions.override
    async def send_carousel(self, cards: list[Card]) -> None:
        payload = render.build_carousel_payload(cards)
        await self._messenger.post_message(self._channel, payload, thread_ts=self._thread_ts)

    @typing_extensions.override
    async def show_processing(self) -> None:
        """Patch the interacted message to disable its buttons, a no-op on plain message events."""
        if self._response_url is None or self._interacted_message is None:
            return
        # The patch is cosmetic and must never abort the turn.
        try:
            patch = render.build_processing_patch(self._interacted_message, self._processing_text)
            await self._messenger.respond_via_response_url(self._response_url, patch)
        except Exception:
            logger.warning('Slack processing patch failed', exc_info=True)


class SlackOAuthBridge(StatefulOAuthBridge):
    """Push the authorize URL into the conversation as a card with a primary link button."""

    def __init__(
        self,
        store: OAuthCodeStore,
        messenger: SlackMessenger,
        *,
        channel: str,
        thread_ts: str | None = None,
        title: str = 'Authorization needed',
        link_label: str = 'Authorize',
    ) -> None:
        super().__init__(store)
        self._messenger = messenger
        self._channel = channel
        self._thread_ts = thread_ts
        self._title = title
        self._link_label = link_label

    @typing_extensions.override
    async def _render_authorization_ui(self, authorize_url: str) -> None:
        card = Card(title=self._title, links=[Link(text=self._link_label, url=authorize_url)])
        payload = render.build_card_payload(card)
        await self._messenger.post_message(self._channel, payload, thread_ts=self._thread_ts)


def _raise_on_error(response: httpx.Response, call: str) -> None:
    # Slack reports failures as HTTP 200 with ok=false, so both layers need checking.
    response.raise_for_status()
    data = response.json()
    if not data.get('ok'):
        raise RuntimeError(f'Slack {call} failed: {data.get("error")}')
