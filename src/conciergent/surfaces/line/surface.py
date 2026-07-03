import logging
import typing

import httpx

from ...reply import Card, Link, ReplySurface
from ...runtime import StatefulOAuthBridge
from ...stores.base import OAuthCodeStore
from . import render


logger = logging.getLogger(__name__)

_API_BASE_URL = 'https://api.line.me'
_TIMEOUT_SECONDS = 30.0
_LOADING_SECONDS = 30
_TEXT_MAX_CHARS = 5000

TEXT_FORMATTING_INSTRUCTION = (
    'LINE renders plain text only. Never use markdown of any kind, no asterisks, backticks, or [text](url). '
    'Write short lines and use blank lines to separate ideas.'
)


class LineMessenger:
    """A thin async client for the handful of LINE Messaging API calls the surface needs."""

    def __init__(self, channel_access_token: str, *, timeout_seconds: float = _TIMEOUT_SECONDS) -> None:
        self._client = httpx.AsyncClient(
            base_url=_API_BASE_URL,
            timeout=timeout_seconds,
            headers={'Authorization': f'Bearer {channel_access_token}', 'Content-Type': 'application/json'},
        )

    async def __aenter__(self) -> 'LineMessenger':
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self._client.aclose()

    async def reply(self, reply_token: str, message: dict[str, typing.Any]) -> None:
        response = await self._client.post(
            '/v2/bot/message/reply', json={'replyToken': reply_token, 'messages': [message]}
        )
        response.raise_for_status()

    async def push(self, user_id: str, message: dict[str, typing.Any]) -> None:
        response = await self._client.post('/v2/bot/message/push', json={'to': user_id, 'messages': [message]})
        response.raise_for_status()

    async def start_loading(self, user_id: str) -> None:
        response = await self._client.post(
            '/v2/bot/chat/loading/start', json={'chatId': user_id, 'loadingSeconds': _LOADING_SECONDS}
        )
        response.raise_for_status()


class ReplyTokenSlot:
    """Spend the free single-use reply token on the first send, then fall back to the push API.

    A turn may emit several messages, an OAuth prompt and then the agent reply,
    but the reply token is single-use and expires within a minute.
    A failed reply attempt also falls through to push, so a message is never lost to a stale token.
    """

    def __init__(self, messenger: LineMessenger, *, user_id: str, reply_token: str | None) -> None:
        self._messenger = messenger
        self._user_id = user_id
        self._reply_token = reply_token

    async def send(self, message: dict[str, typing.Any]) -> None:
        token, self._reply_token = self._reply_token, None
        if token is not None:
            try:
                await self._messenger.reply(token, message)
                return
            except httpx.HTTPStatusError:
                logger.warning('LINE reply token failed, falling back to push')
        await self._messenger.push(self._user_id, message)

    async def start_loading(self) -> None:
        await self._messenger.start_loading(self._user_id)


class LineReplySurface(ReplySurface):
    """Render replies as LINE Flex messages through the reply-token slot."""

    def __init__(self, slot: ReplyTokenSlot, *, text_formatting_instruction: str = TEXT_FORMATTING_INSTRUCTION) -> None:
        self._slot = slot
        self._text_formatting_instruction = text_formatting_instruction

    @property
    @typing.override
    def text_formatting_instruction(self) -> str:
        return self._text_formatting_instruction

    @typing.override
    async def send_text(self, text: str) -> None:
        # LINE rejects text messages over 5000 characters, so longer replies go out in slices.
        for start in range(0, len(text), _TEXT_MAX_CHARS):
            await self._slot.send({'type': 'text', 'text': text[start : start + _TEXT_MAX_CHARS]})

    @typing.override
    async def send_card(self, card: Card, *, destructive: bool = False) -> None:
        placement: render.SuggestionPlacement = 'destructive_button' if destructive else 'chip'
        message: dict[str, typing.Any] = {
            'type': 'flex',
            'altText': render.alt_text(card),
            'contents': render.build_card_bubble(card, suggestion_placement=placement),
        }
        if not destructive:
            quick_reply = render.build_quick_reply(card.suggestions)
            if quick_reply is not None:
                message['quickReply'] = quick_reply
        await self._slot.send(message)

    @typing.override
    async def send_carousel(self, cards: list[Card]) -> None:
        message = {
            'type': 'flex',
            'altText': render.alt_text(cards[0]) if cards else 'Options',
            'contents': render.build_carousel(cards),
        }
        await self._slot.send(message)

    @typing.override
    async def show_processing(self) -> None:
        # The typing indicator is a nice-to-have and must never abort the turn.
        try:
            await self._slot.start_loading()
        except Exception:
            logger.warning('LINE loading indicator failed', exc_info=True)


class LineOAuthBridge(StatefulOAuthBridge):
    """Push the authorize URL into the conversation as a Flex card with a primary link button."""

    def __init__(
        self,
        store: OAuthCodeStore,
        slot: ReplyTokenSlot,
        *,
        title: str = 'Authorization needed',
        link_label: str = 'Authorize',
        wait_timeout_seconds: float | None = None,
    ) -> None:
        if wait_timeout_seconds is not None:
            super().__init__(store, wait_timeout_seconds=wait_timeout_seconds)
        else:
            super().__init__(store)
        self._slot = slot
        self._title = title
        self._link_label = link_label

    @typing.override
    async def _render_authorization_ui(self, authorize_url: str) -> None:
        card = Card(title=self._title, links=[Link(text=self._link_label, url=authorize_url)])
        message = {
            'type': 'flex',
            'altText': render.alt_text(card),
            'contents': render.build_card_bubble(card, suggestion_placement='button'),
        }
        await self._slot.send(message)
