import hashlib
import hmac
import json
import logging
import time
import typing
import urllib.parse

import fastapi

from conciergent.agent.compactor import HistorySummarizer
from conciergent.agent.runner import ChatRunner
from conciergent.defaults import DEFAULTS
from conciergent.identity import ChatSurface, make_principal
from conciergent.runtime import is_handoff_expiry
from conciergent.store.credential import CredentialStore
from conciergent.store.message import MessageStore
from conciergent.surfaces.slack import render
from conciergent.surfaces.slack.surface import SlackMessenger, SlackOAuthBridge, SlackReplySurface
from conciergent.turn import run_turn


logger = logging.getLogger(__name__)

_SIGNATURE_MAX_SKEW_SECONDS = 300
_DEDUPE_TTL_SECONDS = 86400


class SlackWebhookSettings(typing.NamedTuple):
    """Everything the webhook routes need beyond the store and the agent.

    The ``fallback_bot_token`` serves single-workspace apps that skip the install flow,
    it answers any team the store has no installed token for.
    """

    signing_secret: str
    fallback_bot_token: str = ''
    approval_ttl_seconds: int = DEFAULTS.conversation.approval_ttl_seconds
    history_ttl_seconds: int = DEFAULTS.conversation.history_ttl_seconds
    oauth_wait_timeout_seconds: float = DEFAULTS.conversation.oauth_wait_timeout_seconds
    api_timeout_seconds: float = DEFAULTS.surface.slack.api_timeout_seconds
    brand_color: str = render.BRAND_COLOR
    destructive_color: str = render.DESTRUCTIVE_COLOR


def build_router(
    *,
    settings: SlackWebhookSettings,
    message_store: MessageStore,
    credential_store: CredentialStore,
    runner: ChatRunner,
    compactor: HistorySummarizer | None = None,
) -> fastapi.APIRouter:
    """Build the Slack webhook routes, acknowledging within Slack's deadline and replying in the background."""
    router = fastapi.APIRouter()

    async def verified_body(request: fastapi.Request) -> bytes:
        body = await request.body()
        timestamp = request.headers.get('X-Slack-Request-Timestamp')
        signature = request.headers.get('X-Slack-Signature')
        if not _signature_is_valid(settings.signing_secret, body, timestamp=timestamp, signature=signature):
            raise fastapi.HTTPException(status_code=401, detail='invalid Slack signature')
        return body

    @router.post('/slack/events')
    async def events(
        background: fastapi.BackgroundTasks, body: bytes = fastapi.Depends(verified_body)
    ) -> dict[str, typing.Any]:
        payload = json.loads(body)
        if payload.get('type') == 'url_verification':
            return {'challenge': payload.get('challenge')}
        if payload.get('type') != 'event_callback':
            return {}
        event = payload.get('event') or {}
        if not _is_direct_user_message(event):
            return {}
        if await message_store.dedupe(f'slack:event:{payload.get("event_id")}', ttl_seconds=_DEDUPE_TTL_SECONDS):
            return {}
        background.add_task(
            _dispatch_turn,
            settings=settings,
            message_store=message_store,
            credential_store=credential_store,
            runner=runner,
            compactor=compactor,
            team_id=payload.get('team_id', ''),
            user_id=event['user'],
            channel=event['channel'],
            thread_ts=event.get('thread_ts') or event.get('ts'),
            user_text=event.get('text', ''),
        )
        return {}

    @router.post('/slack/interactions')
    async def interactions(
        background: fastapi.BackgroundTasks, body: bytes = fastapi.Depends(verified_body)
    ) -> dict[str, typing.Any]:
        form = urllib.parse.parse_qs(body.decode())
        payload = json.loads(form.get('payload', ['{}'])[0])
        if payload.get('type') != 'block_actions':
            return {}
        action = (payload.get('actions') or [{}])[0]
        scope = render.parse_suggestion_scope(action.get('action_id', ''))
        if scope is None:
            return {}
        message = payload.get('message') or {}
        channel = (payload.get('channel') or {}).get('id', '')
        dedupe_key = _interaction_dedupe_key(payload, scope=scope, channel=channel, message=message)
        if await message_store.dedupe(dedupe_key, ttl_seconds=_DEDUPE_TTL_SECONDS):
            return {}
        background.add_task(
            _dispatch_turn,
            settings=settings,
            message_store=message_store,
            credential_store=credential_store,
            runner=runner,
            compactor=compactor,
            team_id=(payload.get('team') or {}).get('id', ''),
            user_id=(payload.get('user') or {}).get('id', ''),
            channel=channel,
            thread_ts=message.get('thread_ts') or message.get('ts'),
            user_text=action.get('value', ''),
            response_url=payload.get('response_url'),
            interacted_message=message,
            button_label=(action.get('text') or {}).get('text') or action.get('value', ''),
        )
        return {}

    return router


async def _dispatch_turn(
    *,
    settings: SlackWebhookSettings,
    message_store: MessageStore,
    credential_store: CredentialStore,
    runner: ChatRunner,
    compactor: HistorySummarizer | None,
    team_id: str,
    user_id: str,
    channel: str,
    thread_ts: str | None,
    user_text: str,
    response_url: str | None = None,
    interacted_message: dict[str, typing.Any] | None = None,
    button_label: str = '',
) -> None:
    bot_token = await credential_store.resolve_bot_token(ChatSurface.slack, team_id) or settings.fallback_bot_token
    if not bot_token or not user_text:
        return
    principal = make_principal(ChatSurface.slack, team_id, user_id)
    # One Slack thread is one conversation, the surface replies in-thread so follow-ups stay scoped.
    conversation = f'{principal}:{thread_ts}' if thread_ts else principal
    async with SlackMessenger(bot_token, timeout_seconds=settings.api_timeout_seconds) as messenger:
        # Resolve the user's language once so the reply, the approval card, and any OAuth prompt all match it.
        lang = await messenger.get_lang(user_id)
        surface = SlackReplySurface(
            messenger,
            channel=channel,
            thread_ts=thread_ts,
            response_url=response_url,
            interacted_message=interacted_message,
            button_label=button_label,
            lang=lang,
            brand_color=settings.brand_color,
            destructive_color=settings.destructive_color,
        )
        bridge = SlackOAuthBridge(
            message_store,
            messenger,
            channel=channel,
            thread_ts=thread_ts,
            lang=lang,
            wait_timeout_seconds=settings.oauth_wait_timeout_seconds,
            brand_color=settings.brand_color,
        )
        try:
            await run_turn(
                user_text,
                principal=principal,
                runner=runner,
                surface=surface,
                message_store=message_store,
                conversation=conversation,
                bridge=bridge,
                compactor=compactor,
                approval_ttl_seconds=settings.approval_ttl_seconds,
                history_ttl_seconds=settings.history_ttl_seconds,
            )
        except Exception as error:
            # An unfinished authorization is an expected ending, anything else is a real failure.
            if not is_handoff_expiry(error):
                logger.exception('Slack turn failed for %s', principal)


def _signature_is_valid(secret: str, body: bytes, *, timestamp: str | None, signature: str | None) -> bool:
    if not timestamp or not signature:
        return False
    try:
        skew = abs(time.time() - float(timestamp))
    except ValueError:
        return False
    if skew > _SIGNATURE_MAX_SKEW_SECONDS:
        return False
    digest = hmac.new(secret.encode(), f'v0:{timestamp}:'.encode() + body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(f'v0={digest}', signature)


def _is_direct_user_message(event: dict[str, typing.Any]) -> bool:
    # Only fresh direct messages from humans start a turn, bot echoes and edits are dropped.
    return (
        event.get('type') == 'message'
        and event.get('channel_type') == 'im'
        and not event.get('bot_id')
        and not event.get('subtype')
        and bool(event.get('user'))
    )


def _interaction_dedupe_key(
    payload: dict[str, typing.Any], *, scope: render.Scope, channel: str, message: dict[str, typing.Any]
) -> str:
    message_ts = message.get('ts')
    if not message_ts:
        return f'slack:interaction:{payload.get("trigger_id")}'
    if scope == 'exclusive':
        # An exclusive pick consumes the whole message, so every button shares one key.
        return f'slack:interaction:{channel}:{message_ts}'
    # An open button is dedup'd per action_id, so each distinct button dispatches at most once.
    action = (payload.get('actions') or [{}])[0]
    return f'slack:interaction:{channel}:{message_ts}:{action.get("action_id", "")}'
