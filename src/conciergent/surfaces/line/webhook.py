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
from conciergent.groups import GroupPolicy, without_spans
from conciergent.i18n.lang import Lang
from conciergent.identity import ChatSurface, make_principal
from conciergent.runtime import is_handoff_expiry
from conciergent.store.message import MessageStore
from conciergent.surfaces.line import render
from conciergent.surfaces.line.surface import (
    GroupChat,
    LineMessenger,
    LineOAuthBridge,
    LineReplySurface,
    ReplyTokenSlot,
)
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
    api_timeout_seconds: float = DEFAULTS.surface.line.api_timeout_seconds
    brand_color: str = render.BRAND_COLOR
    destructive_color: str = render.DESTRUCTIVE_COLOR
    groups: GroupPolicy = GroupPolicy()


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
    event_type = event.get('type')
    chat = _group_chat(source)
    if event_type in ('join', 'leave'):
        _log_membership(event_type, chat, allowed=chat is not None and settings.groups.admits(chat.chat_id))
        return
    user_id = source.get('userId')
    if not user_id:
        return
    if chat is None and source.get('type') != 'user':
        return
    if chat is not None and not (settings.groups.admits(chat.chat_id) and await runner.supports_groups()):
        return
    principal = make_principal(ChatSurface.line, user_id)
    async with LineMessenger(settings.channel_access_token, timeout_seconds=settings.api_timeout_seconds) as messenger:
        slot = ReplyTokenSlot(
            messenger,
            to=chat.chat_id if chat is not None else user_id,
            reply_token=event.get('replyToken'),
            show_loading=chat is None,
        )
        # Resolve the user's language once so the greeting, reply, approval card, and OAuth prompt all match it.
        lang = await messenger.get_lang(user_id)
        if event_type == 'follow' and chat is None:
            # The add-time prompt shows the welcome-flavored body; only a follow event reaches here.
            follow_bridge = LineOAuthBridge(
                message_store,
                slot,
                lang=lang,
                wait_timeout_seconds=settings.oauth_wait_timeout_seconds,
                brand_color=settings.brand_color,
                body_key='line.oauth.welcome_body',
            )
            await _greet_follower(runner=runner, principal=principal, bridge=follow_bridge, slot=slot, lang=lang)
            return
        user_text = _turn_input(event, addressed_only=chat is not None and settings.groups.reply_to == 'mention')
        if not user_text:
            return
        surface = LineReplySurface(
            slot,
            lang=lang,
            brand_color=settings.brand_color,
            destructive_color=settings.destructive_color,
        )
        if chat is not None:
            # A group turn holds no one's authorization, so it gets no OAuth bridge and names its speaker instead.
            conversation = make_principal(ChatSurface.line, chat.kind, chat.chat_id)
            bridge = None
            speaker = await messenger.get_display_name(user_id, chat=chat) or _anonymous_speaker(user_id)
        else:
            conversation = None
            bridge = LineOAuthBridge(
                message_store,
                slot,
                lang=lang,
                wait_timeout_seconds=settings.oauth_wait_timeout_seconds,
                brand_color=settings.brand_color,
            )
            speaker = None
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
                shared=chat is not None,
                speaker=speaker,
            )
        except Exception as error:
            # An unfinished authorization is an expected ending, anything else is a real failure.
            if not is_handoff_expiry(error):
                logger.exception('LINE turn failed for %s', principal)


def _group_chat(source: dict[str, typing.Any]) -> GroupChat | None:
    if source.get('type') == 'group' and source.get('groupId'):
        return GroupChat('group', source['groupId'])
    if source.get('type') == 'room' and source.get('roomId'):
        return GroupChat('room', source['roomId'])
    return None


def _log_membership(event_type: str, chat: GroupChat | None, *, allowed: bool) -> None:
    if chat is None:
        return
    if event_type == 'leave':
        logger.info('LINE bot left %s %s', chat.kind, chat.chat_id)
    elif allowed:
        logger.info('LINE bot joined allowed %s %s', chat.kind, chat.chat_id)
    else:
        # The operator copies this id into the allowlist, so it is logged even while groups are off.
        logger.info(
            'LINE bot joined %s %s, add it to surface.line.groups.allowed to answer there', chat.kind, chat.chat_id
        )


def _turn_input(event: dict[str, typing.Any], *, addressed_only: bool) -> str:
    """Return the text a message or suggestion tap feeds the agent, or empty when the event starts no turn.

    With ``addressed_only`` a typed message counts only when it mentions the bot, and that mention is removed.
    A suggestion tap is always addressed to the bot, so it never needs one.
    """
    if event.get('type') == 'postback':
        return render.parse_suggestion_postback((event.get('postback') or {}).get('data', '')) or ''
    if event.get('type') != 'message':
        return ''
    message = event.get('message') or {}
    text = message.get('text', '')
    if message.get('type') != 'text' or not text:
        return ''
    if not addressed_only:
        return text
    mentionees = (message.get('mention') or {}).get('mentionees') or []
    spans = [
        _code_point_span(text, mentionee.get('index', 0), mentionee.get('length', 0))
        for mentionee in mentionees
        if mentionee.get('isSelf')
    ]
    if not spans:
        return ''
    return without_spans(text, spans)


def _code_point_span(text: str, index: int, length: int) -> tuple[int, int]:
    # LINE counts mention offsets in UTF-16 code units, so an emoji before the mention shifts the Python index.
    return _code_point_offset(text, index), _code_point_offset(text, index + length)


def _code_point_offset(text: str, utf16_offset: int) -> int:
    units = 0
    for position, character in enumerate(text):
        if units >= utf16_offset:
            return position
        units += 2 if ord(character) > 0xFFFF else 1
    return len(text)


def _anonymous_speaker(user_id: str) -> str:
    # A member whose profile cannot be read still needs a stable label, so members stay distinct in the history.
    return f'user-{user_id[-4:]}'


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
    text = i18n.t('greeting.ready' if just_authorized else 'greeting.welcome_back', lang)
    await slot.send({'type': 'text', 'text': text})


def _signature_is_valid(channel_secret: str, body: bytes, *, signature: str | None) -> bool:
    if not signature:
        return False
    digest = hmac.new(channel_secret.encode(), body, hashlib.sha256).digest()
    return hmac.compare_digest(base64.b64encode(digest).decode(), signature)
