import base64
import hashlib
import hmac
import json
import logging
import typing

import fastapi

from ...identity import ChatSurface, make_principal
from ...oauth import OAuthHandoffExpiredError
from ...runtime import ChatAgent, HistoryCompactor, run_turn
from ...stores.base import Store
from .surface import LineMessenger, LineOAuthBridge, LineReplySurface, ReplyTokenSlot


logger = logging.getLogger(__name__)

_DEDUPE_TTL_SECONDS = 86400


class LineWebhookSettings(typing.NamedTuple):
    """The LINE channel credentials the webhook route needs."""

    channel_secret: str
    channel_access_token: str
    welcome_text: str = 'Hi! Send me a message to get started.'


def build_router(
    *,
    settings: LineWebhookSettings,
    store: Store,
    agent: ChatAgent,
    compactor: HistoryCompactor | None = None,
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
            if event_id and await store.dedupe(f'line:event:{event_id}', ttl_seconds=_DEDUPE_TTL_SECONDS):
                continue
            background.add_task(
                _dispatch_event, settings=settings, store=store, agent=agent, compactor=compactor, event=event
            )
        return {}

    return router


async def _dispatch_event(
    *,
    settings: LineWebhookSettings,
    store: Store,
    agent: ChatAgent,
    compactor: HistoryCompactor | None,
    event: dict[str, typing.Any],
) -> None:
    source = event.get('source') or {}
    user_id = source.get('userId')
    if source.get('type') != 'user' or not user_id:
        return
    async with LineMessenger(settings.channel_access_token) as messenger:
        slot = ReplyTokenSlot(messenger, user_id=user_id, reply_token=event.get('replyToken'))
        if event.get('type') == 'follow':
            await slot.send({'type': 'text', 'text': settings.welcome_text})
            return
        message = event.get('message') or {}
        user_text = message.get('text', '')
        if event.get('type') != 'message' or message.get('type') != 'text' or not user_text:
            return
        principal = make_principal(ChatSurface.line, user_id)
        surface = LineReplySurface(slot)
        bridge = LineOAuthBridge(store, slot)
        try:
            await run_turn(
                user_text,
                principal=principal,
                agent=agent,
                surface=surface,
                store=store,
                bridge=bridge,
                compactor=compactor,
            )
        except OAuthHandoffExpiredError:
            return
        except Exception:
            logger.exception('LINE turn failed for %s', principal)


def _signature_is_valid(channel_secret: str, body: bytes, *, signature: str | None) -> bool:
    if not signature:
        return False
    digest = hmac.new(channel_secret.encode(), body, hashlib.sha256).digest()
    return hmac.compare_digest(base64.b64encode(digest).decode(), signature)
