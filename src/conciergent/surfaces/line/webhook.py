import base64
import hashlib
import hmac
import json
import logging
import typing

import fastapi

from conciergent import i18n
from conciergent.agent.compactor import HistorySummarizer
from conciergent.agent.runner import ChatRunner
from conciergent.defaults import DEFAULTS
from conciergent.i18n.lang import Lang
from conciergent.identity import ChatSurface, make_principal
from conciergent.runtime import is_handoff_expiry
from conciergent.store.message import MessageStore
from conciergent.surfaces.line import render
from conciergent.surfaces.line.surface import LineMessenger, LineOAuthBridge, LineReplySurface, ReplyTokenSlot
from conciergent.turn import run_turn


logger = logging.getLogger(__name__)

_DEDUPE_TTL_SECONDS = 86400


class LineWebhookSettings(typing.NamedTuple):
    """The LINE channel credentials the webhook route needs."""

    channel_secret: str
    channel_access_token: str
    approval_ttl_seconds: int = DEFAULTS.conversation.approval_ttl_seconds
    history_ttl_seconds: int = DEFAULTS.conversation.history_ttl_seconds
    oauth_wait_timeout_seconds: float = DEFAULTS.conversation.oauth_wait_timeout_seconds
    api_timeout_seconds: float = DEFAULTS.surface.api_timeout_seconds
    brand_color: str = render.BRAND_COLOR
    destructive_color: str = render.DESTRUCTIVE_COLOR


def build_router(
    *,
    settings: LineWebhookSettings,
    message_store: MessageStore,
    runner: ChatRunner,
    compactor: HistorySummarizer | None = None,
) -> fastapi.APIRouter:
    """Build the LINE webhook route, acknowledging immediately and replying in the background."""
    router = fastapi.APIRouter()

    async def verified_body(request: fastapi.Request) -> bytes:
        body = await request.body()
        signature = request.headers.get('X-Line-Signature')
        if not _signature_is_valid(settings.channel_secret, body, signature=signature):
            raise fastapi.HTTPException(status_code=401, detail='invalid LINE signature')
        return body

    @router.post('/line/events')
    async def events(
        background: fastapi.BackgroundTasks, body: bytes = fastapi.Depends(verified_body)
    ) -> dict[str, typing.Any]:
        payload = json.loads(body)
        for event in payload.get('events') or []:
            event_id = event.get('webhookEventId')
            # Without an id there is nothing to deduplicate on, and a shared placeholder key
            # would swallow every later id-less event for a day.
            if event_id and await message_store.dedupe(f'line:event:{event_id}', ttl_seconds=_DEDUPE_TTL_SECONDS):
                continue
            background.add_task(
                _dispatch_event,
                settings=settings,
                message_store=message_store,
                runner=runner,
                compactor=compactor,
                event=event,
            )
        return {}

    return router


async def _dispatch_event(
    *,
    settings: LineWebhookSettings,
    message_store: MessageStore,
    runner: ChatRunner,
    compactor: HistorySummarizer | None,
    event: dict[str, typing.Any],
) -> None:
    source = event.get('source') or {}
    user_id = source.get('userId')
    if source.get('type') != 'user' or not user_id:
        return
    principal = make_principal(ChatSurface.line, user_id)
    async with LineMessenger(settings.channel_access_token, timeout_seconds=settings.api_timeout_seconds) as messenger:
        slot = ReplyTokenSlot(messenger, user_id=user_id, reply_token=event.get('replyToken'))
        # Resolve the user's language once so the greeting, reply, approval card, and OAuth prompt all match it.
        lang = await messenger.get_lang(user_id)
        bridge = LineOAuthBridge(
            message_store,
            slot,
            lang=lang,
            wait_timeout_seconds=settings.oauth_wait_timeout_seconds,
            brand_color=settings.brand_color,
        )
        if event.get('type') == 'follow':
            await _greet_follower(runner=runner, principal=principal, bridge=bridge, slot=slot, lang=lang)
            return
        message = event.get('message') or {}
        user_text = message.get('text', '')
        if event.get('type') != 'message' or message.get('type') != 'text' or not user_text:
            return
        surface = LineReplySurface(
            slot,
            lang=lang,
            brand_color=settings.brand_color,
            destructive_color=settings.destructive_color,
        )
        try:
            await run_turn(
                user_text,
                principal=principal,
                runner=runner,
                surface=surface,
                message_store=message_store,
                bridge=bridge,
                compactor=compactor,
                approval_ttl_seconds=settings.approval_ttl_seconds,
                history_ttl_seconds=settings.history_ttl_seconds,
            )
        except Exception as error:
            # An unfinished authorization is an expected ending, anything else is a real failure.
            if not is_handoff_expiry(error):
                logger.exception('LINE turn failed for %s', principal)


async def _greet_follower(
    *,
    runner: ChatRunner,
    principal: str,
    bridge: LineOAuthBridge,
    slot: ReplyTokenSlot,
    lang: Lang | None,
) -> None:
    """Fire any pending OAuth at add time and greet according to what happened."""
    try:
        just_authorized = await runner.bootstrap(principal, bridge=bridge)
    except Exception as error:
        # The user got the authorization link but walked away, greeting can wait for their message.
        if not is_handoff_expiry(error):
            logger.exception('LINE follow bootstrap failed for %s', principal)
        return
    text = i18n.t('line.ready' if just_authorized else 'line.welcome', lang)
    await slot.send({'type': 'text', 'text': text})


def _signature_is_valid(channel_secret: str, body: bytes, *, signature: str | None) -> bool:
    if not signature:
        return False
    digest = hmac.new(channel_secret.encode(), body, hashlib.sha256).digest()
    return hmac.compare_digest(base64.b64encode(digest).decode(), signature)
