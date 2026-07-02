import base64
import hashlib
import hmac
import json
import logging
import typing

import fastapi

from ...identity import ChatSurface, make_principal
from ...oauth import is_handoff_expiry
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
    ready_text: str = 'You are all set. Send me a message to get started.'


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
    principal = make_principal(ChatSurface.line, user_id)
    async with LineMessenger(settings.channel_access_token) as messenger:
        slot = ReplyTokenSlot(messenger, user_id=user_id, reply_token=event.get('replyToken'))
        bridge = LineOAuthBridge(store, slot)
        if event.get('type') == 'follow':
            await _greet_follower(settings=settings, agent=agent, principal=principal, bridge=bridge, slot=slot)
            return
        message = event.get('message') or {}
        user_text = message.get('text', '')
        if event.get('type') != 'message' or message.get('type') != 'text' or not user_text:
            return
        surface = LineReplySurface(slot)
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
        except Exception as error:
            # An unfinished authorization is an expected ending, anything else is a real failure.
            if not is_handoff_expiry(error):
                logger.exception('LINE turn failed for %s', principal)


async def _greet_follower(
    *,
    settings: LineWebhookSettings,
    agent: ChatAgent,
    principal: str,
    bridge: LineOAuthBridge,
    slot: ReplyTokenSlot,
) -> None:
    """Fire any pending OAuth at add time and greet according to what happened."""
    try:
        just_authorized = await agent.bootstrap(principal, bridge=bridge)
    except Exception as error:
        # The user got the authorization link but walked away, greeting can wait for their message.
        if not is_handoff_expiry(error):
            logger.exception('LINE follow bootstrap failed for %s', principal)
        return
    text = settings.ready_text if just_authorized else settings.welcome_text
    await slot.send({'type': 'text', 'text': text})


def _signature_is_valid(channel_secret: str, body: bytes, *, signature: str | None) -> bool:
    if not signature:
        return False
    digest = hmac.new(channel_secret.encode(), body, hashlib.sha256).digest()
    return hmac.compare_digest(base64.b64encode(digest).decode(), signature)
