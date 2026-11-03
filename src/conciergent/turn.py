import logging
import typing

from opentelemetry import trace

from conciergent.agent.compactor import HistorySummarizer
from conciergent.agent.runner import ChatRunner
from conciergent.chat_context import ChatContext
from conciergent.defaults import DEFAULTS
from conciergent.reply import Card, Carousel, ReplySurface
from conciergent.runtime import OAuthBridge, PendingApproval, SkippedReply
from conciergent.store.message import MessageStore
from conciergent.telemetry import tracer


logger = logging.getLogger(__name__)


@tracer.start_as_current_span('turn')
async def run_turn(
    user_input: str,
    *,
    principal: str,
    chat_context: ChatContext,
    runner: ChatRunner,
    surface: ReplySurface,
    message_store: MessageStore,
    bridge: OAuthBridge | None = None,
    compactor: HistorySummarizer | None = None,
    approval_ttl_seconds: int = DEFAULTS.conversation.approval_ttl_seconds,
    history_ttl_seconds: int = DEFAULTS.conversation.history_ttl_seconds,
) -> None:
    """Run one conversation turn end to end and dispatch the reply to ``surface``.

    The ``principal`` is the user's identity and keys credentials,
    while ``chat_context`` names the chat the turn runs in, such as a direct chat, a group chat, or a push's,
    with the conversation that scopes history and pending approvals.
    This is side-effect only, the surface sends and the appended history turn.
    """
    span = trace.get_current_span()
    span.set_attributes({'conciergent.conversation': chat_context.conversation, 'conciergent.chat': chat_context.kind})
    if not await chat_context.can_run(runner):
        # The app cannot serve this chat right now, and the kind of chat decides how that ends.
        await chat_context.decline(surface)
        span.set_attribute('conciergent.turn.outcome', 'declined')
        return
    conversation = chat_context.conversation
    history = await message_store.load_history(conversation)
    if compactor is not None and history:
        compacted = await compactor.compact_if_needed(history)
        if compacted is not None:
            await message_store.replace_history(conversation, compacted, ttl_seconds=history_ttl_seconds)
            history = compacted
    owner = chat_context.approval_owner(principal)
    pending_approval = await message_store.take_approval(conversation, owner=owner)
    if pending_approval is not None:
        span.add_event('approval resumed')
    if pending_approval is None and chat_context.drops_unowned_decision(user_input):
        # Not every surface can tell one member privately, so the tap is dropped quietly on all of them.
        await surface.acknowledge_silently()
        span.set_attribute('conciergent.turn.outcome', 'dropped')
        return

    await surface.show_processing()
    result = await runner.run(
        user_input,
        principal=principal,
        history=history,
        pending_approval=pending_approval,
        bridge=bridge,
        surface=surface,
        chat_context=chat_context,
    )

    # The outcomes are a closed set, and assert_never makes the type checker flag one left unhandled here.
    match result.output:
        case PendingApproval(card=card, state=state):
            # The turn is only paused, not finished.
            # The in-flight messages ride on ``state`` and are replayed via ``pending_approval`` on resume,
            # so committing ``result.history`` here would either wipe the conversation with the empty default,
            # or orphan the tool-call turn from its later result.
            await message_store.park_approval(conversation, state, ttl_seconds=approval_ttl_seconds, owner=owner)
            await surface.send_card(card, destructive=True)
            span.set_attribute('conciergent.turn.outcome', 'approval_requested')
            return
        case SkippedReply(reason=reason):
            # Nothing reached the chat, so nothing joins the history the members see.
            logger.info('Turn in %s ended without a reply: %s', conversation, reason)
            span.set_attribute('conciergent.turn.outcome', 'skipped')
            return
        case Carousel(options=options, fallback=fallback):
            await surface.send_carousel([*options, fallback])
            span.set_attribute('conciergent.turn.outcome', 'carousel')
        case Card() as card:
            await surface.send_card(card)
            span.set_attribute('conciergent.turn.outcome', 'card')
        case str() as text:
            await surface.send_text(text)
            span.set_attribute('conciergent.turn.outcome', 'text')
        case unhandled:
            typing.assert_never(unhandled)

    if result.invalidate_history:
        # A tool made earlier turns stale, e.g. a sign-out, so drop them instead of appending this turn.
        await message_store.clear_history(conversation)
    else:
        await message_store.append_history(conversation, result.history, ttl_seconds=history_ttl_seconds)
