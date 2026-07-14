import asyncio
import enum
import json
import logging
import typing

from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed

from conciergent.agent.compactor import HistorySummarizer
from conciergent.agent.runner import ChatRunner
from conciergent.defaults import DEFAULTS
from conciergent.i18n.lang import Lang
from conciergent.identity import ChatSurface, make_principal
from conciergent.runtime import is_handoff_expiry
from conciergent.store.credential import CredentialStore
from conciergent.store.message import MessageStore
from conciergent.surfaces.discord import render
from conciergent.surfaces.discord.surface import (
    DiscordMessenger,
    DiscordOAuthBridge,
    DiscordReplySurface,
    Interaction,
)
from conciergent.turn import run_turn


logger = logging.getLogger(__name__)

_GATEWAY_URL = 'wss://gateway.discord.gg/?v=10&encoding=json'
# The one non-privileged intent a direct-message bot needs; DM content is exempt from the message-content intent.
_INTENT_DIRECT_MESSAGES = 1 << 12
_DEDUPE_TTL_SECONDS = 86400
_MAX_BACKOFF_SECONDS = 60

# The interaction type Discord sends for a message-component click.
_MESSAGE_COMPONENT = 3

# Close codes Discord marks non-recoverable, so a reconnect would only reproduce the same rejection.
# They cover a bad bot token, an unsupported API version, and invalid or disallowed intents.
_FATAL_CLOSE_CODES = frozenset({4004, 4010, 4011, 4012, 4013, 4014})


class _Op(enum.IntEnum):
    """Discord gateway opcodes, the ``op`` field on every gateway payload.

    These are Discord's application-level protocol, distinct from the WebSocket frame opcodes the transport handles.
    See https://discord.com/developers/docs/topics/opcodes-and-status-codes#gateway-gateway-opcodes.
    """

    DISPATCH = 0
    HEARTBEAT = 1
    IDENTIFY = 2
    RESUME = 6
    RECONNECT = 7
    INVALID_SESSION = 9
    HEARTBEAT_ACK = 11


class DiscordGatewaySettings(typing.NamedTuple):
    """The Discord bot credentials the gateway connection needs."""

    bot_token: str
    approval_ttl_seconds: int = DEFAULTS.conversation.approval_ttl_seconds
    history_ttl_seconds: int = DEFAULTS.conversation.history_ttl_seconds
    oauth_wait_timeout_seconds: float = DEFAULTS.conversation.oauth_wait_timeout_seconds
    api_timeout_seconds: float = DEFAULTS.surface.discord.api_timeout_seconds
    brand_color: str = render.BRAND_COLOR
    destructive_color: str = render.DESTRUCTIVE_COLOR


class DiscordGateway:
    """A hand-rolled Discord gateway client that turns direct messages and button clicks into turns.

    It owns the WebSocket lifecycle, identify, heartbeat, resume, and reconnect, so the rest of the surface
    stays a plain REST client. Only direct messages and component interactions are acted on; everything else
    is ignored. The dispatch entry point is separated from the socket so it can be driven directly in tests.
    """

    def __init__(
        self,
        *,
        settings: DiscordGatewaySettings,
        message_store: MessageStore,
        runner: ChatRunner,
        compactor: HistorySummarizer | None = None,
        credential_store: CredentialStore | None = None,
    ) -> None:
        self._settings = settings
        self._message_store = message_store
        self._runner = runner
        self._compactor = compactor
        self._credential_store = credential_store
        self._sequence: int | None = None
        self._session_id: str | None = None
        self._resume_gateway_url: str | None = None
        self._heartbeat_acked = True

    async def run(self) -> None:
        """Hold the gateway connection for the app's lifetime, reconnecting with backoff until cancelled."""
        backoff = 1.0
        while True:
            try:
                await self._connect_once()
                backoff = 1.0
            except asyncio.CancelledError:
                raise
            except Exception as error:
                if _received_close_code(error) in _FATAL_CLOSE_CODES:
                    # A misconfigured token or intent fails identically on every retry, so stop rather than loop.
                    # Only this surface's task ends, because the app runs each enabled surface on its own task.
                    logger.error('Discord gateway closed with a fatal code, not reconnecting', exc_info=True)
                    raise
                logger.warning('Discord gateway connection dropped, reconnecting', exc_info=True)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, _MAX_BACKOFF_SECONDS)

    async def _connect_once(self) -> None:
        url = self._resume_gateway_url or _GATEWAY_URL
        async with connect(url, max_size=None) as socket:
            hello = json.loads(await socket.recv())
            interval_seconds = hello['d']['heartbeat_interval'] / 1000
            self._heartbeat_acked = True
            heartbeat = asyncio.create_task(self._heartbeat_loop(socket, interval_seconds))
            try:
                await socket.send(
                    json.dumps(self._resume_payload() if self._can_resume() else self._identify_payload())
                )
                async for raw in socket:
                    await self._handle_gateway_message(socket, json.loads(raw))
            finally:
                heartbeat.cancel()

    async def _heartbeat_loop(self, socket: ClientConnection, interval_seconds: float) -> None:
        # Beat once per interval; an unanswered previous beat means a dead link, so close and let run() reconnect.
        while True:
            await asyncio.sleep(interval_seconds)
            if not self._heartbeat_acked:
                await socket.close(code=4000)
                return
            self._heartbeat_acked = False
            await socket.send(json.dumps({'op': _Op.HEARTBEAT, 'd': self._sequence}))

    async def _handle_gateway_message(self, socket: ClientConnection, message: dict[str, typing.Any]) -> None:
        op = message.get('op')
        if op == _Op.DISPATCH:
            self._sequence = message.get('s') or self._sequence
            await self._handle_dispatch(message.get('t') or '', message.get('d') or {})
        elif op == _Op.HEARTBEAT:
            await socket.send(json.dumps({'op': _Op.HEARTBEAT, 'd': self._sequence}))
        elif op == _Op.HEARTBEAT_ACK:
            self._heartbeat_acked = True
        elif op == _Op.RECONNECT:
            # A resumable disconnect; close and let run() reconnect against the resume URL.
            await socket.close(code=4000)
        elif op == _Op.INVALID_SESSION:
            if not message.get('d'):
                # The session cannot be resumed, so drop it and identify fresh on the next connect.
                self._session_id = None
                self._sequence = None
            await socket.close(code=4000)

    async def _handle_dispatch(self, event_type: str, data: dict[str, typing.Any]) -> None:
        if event_type == 'READY':
            self._session_id = data.get('session_id')
            self._resume_gateway_url = data.get('resume_gateway_url')
        elif event_type == 'MESSAGE_CREATE':
            await self._maybe_dispatch_message(data)
        elif event_type == 'INTERACTION_CREATE':
            await self._maybe_dispatch_interaction(data)

    async def _maybe_dispatch_message(self, data: dict[str, typing.Any]) -> None:
        # Only a fresh direct message from a human starts a turn; guild messages and bot echoes are dropped.
        author = data.get('author') or {}
        user_id = author.get('id')
        content = data.get('content') or ''
        channel_id = data.get('channel_id')
        if data.get('guild_id') or author.get('bot') or not user_id or not channel_id or not content:
            return
        message_id = data.get('id')
        if message_id and await self._message_store.dedupe(
            f'discord:message:{message_id}', ttl_seconds=_DEDUPE_TTL_SECONDS
        ):
            return
        await self._dispatch_turn(user_id=user_id, channel_id=channel_id, user_text=content, locale=None)

    async def _maybe_dispatch_interaction(self, data: dict[str, typing.Any]) -> None:
        if data.get('type') != _MESSAGE_COMPONENT:
            return
        parsed = render.parse_suggestion((data.get('data') or {}).get('custom_id', ''))
        if parsed is None:
            return
        user = data.get('user') or (data.get('member') or {}).get('user') or {}
        user_id = user.get('id')
        channel_id = data.get('channel_id')
        interaction_id = data.get('id')
        token = data.get('token')
        if not user_id or not channel_id or not interaction_id or not token:
            return
        if await self._message_store.dedupe(f'discord:interaction:{interaction_id}', ttl_seconds=_DEDUPE_TTL_SECONDS):
            return
        await self._dispatch_turn(
            user_id=user_id,
            channel_id=channel_id,
            user_text=parsed[1],
            locale=data.get('locale'),
            interaction=Interaction(interaction_id=interaction_id, token=token),
        )

    async def _dispatch_turn(
        self,
        *,
        user_id: str,
        channel_id: str,
        user_text: str,
        locale: str | None,
        interaction: Interaction | None = None,
    ) -> None:
        principal = make_principal(ChatSurface.discord, user_id)
        lang = await self._resolve_lang(principal, locale)
        # A direct message has no threads, so the whole dialog with a user is one conversation.
        async with DiscordMessenger(
            self._settings.bot_token, timeout_seconds=self._settings.api_timeout_seconds
        ) as messenger:
            surface = DiscordReplySurface(
                messenger,
                channel_id=channel_id,
                interaction=interaction,
                lang=lang,
                brand_color=self._settings.brand_color,
                destructive_color=self._settings.destructive_color,
            )
            bridge = DiscordOAuthBridge(
                self._message_store,
                messenger,
                channel_id=channel_id,
                lang=lang,
                wait_timeout_seconds=self._settings.oauth_wait_timeout_seconds,
                brand_color=self._settings.brand_color,
            )
            try:
                await run_turn(
                    user_text,
                    principal=principal,
                    runner=self._runner,
                    surface=surface,
                    message_store=self._message_store,
                    bridge=bridge,
                    compactor=self._compactor,
                    approval_ttl_seconds=self._settings.approval_ttl_seconds,
                    history_ttl_seconds=self._settings.history_ttl_seconds,
                )
            except Exception as error:
                # An unfinished authorization is an expected ending, anything else is a real failure.
                if not is_handoff_expiry(error):
                    logger.exception('Discord turn failed for %s', principal)

    async def _resolve_lang(self, principal: str, locale: str | None) -> Lang | None:
        # An interaction carries the user's locale but a typed message carries none, so persist it on arrival,
        # then reuse it on later messages and resolve to None until one arrives, letting the reply mirror the message.
        store = self._credential_store
        if locale is not None:
            if store is not None:
                await store.set_locale(principal, locale)
            return _parse_lang(locale)
        stored = await store.get_locale(principal) if store is not None else None
        return _parse_lang(stored)

    def _can_resume(self) -> bool:
        return bool(self._session_id) and self._sequence is not None

    def _identify_payload(self) -> dict[str, typing.Any]:
        return {
            'op': _Op.IDENTIFY,
            'd': {
                'token': self._settings.bot_token,
                'intents': _INTENT_DIRECT_MESSAGES,
                'properties': {'os': 'linux', 'browser': 'conciergent', 'device': 'conciergent'},
            },
        }

    def _resume_payload(self) -> dict[str, typing.Any]:
        return {
            'op': _Op.RESUME,
            'd': {'token': self._settings.bot_token, 'session_id': self._session_id, 'seq': self._sequence},
        }


def _received_close_code(error: Exception) -> int | None:
    # The code from the server's close frame, or None when the drop carried no frame or was not a close at all.
    if isinstance(error, ConnectionClosed) and error.rcvd is not None:
        return error.rcvd.code
    return None


def _parse_lang(locale: str | None) -> Lang | None:
    if not locale:
        return None
    try:
        return Lang(locale)
    except ValueError:
        return None
