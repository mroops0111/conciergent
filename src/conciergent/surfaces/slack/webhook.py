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
from conciergent.group_chat import GroupChatPolicy
from conciergent.identity import ChatSurface
from conciergent.runtime import is_handoff_expiry
from conciergent.store.credential import CredentialStore
from conciergent.store.message import MessageStore
from conciergent.surfaces.slack import render
from conciergent.surfaces.slack.chat import SlackChat, slack_chat_from_event, slack_chat_from_interaction
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
    group_chat_policy: GroupChatPolicy = GroupChatPolicy()


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
        chat = slack_chat_from_event(payload.get('team_id', ''), event, settings.group_chat_policy)
        if chat is None or not chat.answers(event):
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
            chat=chat,
            user_text=chat.turn_input(event, bot_user_id=_bot_user_id(payload)),
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
        chat = slack_chat_from_interaction(payload, settings.group_chat_policy)
        if chat is None:
            return {}
        message = payload.get('message') or {}
        if await message_store.dedupe(
            _interaction_dedupe_key(payload, scope=scope, chat=chat, message=message), ttl_seconds=_DEDUPE_TTL_SECONDS
        ):
            return {}
        background.add_task(
            _dispatch_turn,
            settings=settings,
            message_store=message_store,
            credential_store=credential_store,
            runner=runner,
            compactor=compactor,
            chat=chat,
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
    chat: SlackChat,
    user_text: str,
    response_url: str | None = None,
    interacted_message: dict[str, typing.Any] | None = None,
    button_label: str = '',
) -> None:
    bot_token = await credential_store.resolve_bot_token(ChatSurface.slack, chat.team_id) or settings.fallback_bot_token
    if not bot_token or not user_text:
        return
    principal = chat.principal
    async with SlackMessenger(bot_token, timeout_seconds=settings.api_timeout_seconds) as messenger:
        # Resolve the user's language once so the reply, the approval card, and any OAuth prompt all match it.
        user = await messenger.get_user(chat.user_id)
        lang = user.lang
        surface = SlackReplySurface(
            messenger,
            channel=chat.channel,
            thread_ts=chat.thread_ts,
            response_url=response_url,
            interacted_message=interacted_message,
            button_label=button_label,
            lang=lang,
            brand_color=settings.brand_color,
            destructive_color=settings.destructive_color,
            reply_to_user=chat.reply_addressee(typed=interacted_message is None),
        )
        bridge = SlackOAuthBridge(
            message_store,
            messenger,
            channel=chat.channel,
            thread_ts=chat.thread_ts,
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
                conversation=chat.conversation,
                bridge=bridge,
                compactor=compactor,
                approval_ttl_seconds=settings.approval_ttl_seconds,
                history_ttl_seconds=settings.history_ttl_seconds,
                group=chat.group_chat_turn(user.display_name),
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


def _bot_user_id(payload: dict[str, typing.Any]) -> str | None:
    # Every event callback names the installation it was delivered to, whose user_id is the bot's own.
    authorizations = payload.get('authorizations') or [{}]
    return authorizations[0].get('user_id')


def _interaction_dedupe_key(
    payload: dict[str, typing.Any], *, scope: render.Scope, chat: SlackChat, message: dict[str, typing.Any]
) -> str:
    message_ts = message.get('ts')
    if not message_ts:
        return f'slack:interaction:{payload.get("trigger_id")}'
    if scope == 'exclusive':
        # An exclusive pick consumes the whole message, and the chat decides whose pick that is.
        return chat.exclusive_pick_key(message_ts)
    # An open button is dedup'd per action_id, so each distinct button dispatches at most once.
    action = (payload.get('actions') or [{}])[0]
    return f'slack:interaction:{chat.channel}:{message_ts}:{action.get("action_id", "")}'
