from conciergent.agent.compactor import HistorySummarizer
from conciergent.agent.runner import ChatRunner
from conciergent.chat_turn import ChatTurn
from conciergent.defaults import DEFAULTS
from conciergent.reply import Card, Carousel, ReplySurface
from conciergent.runtime import OAuthBridge, PendingApproval
from conciergent.store.message import MessageStore


async def run_turn(
    user_input: str,
    *,
    principal: str,
    turn: ChatTurn,
    runner: ChatRunner,
    surface: ReplySurface,
    message_store: MessageStore,
    bridge: OAuthBridge | None = None,
    compactor: HistorySummarizer | None = None,
    approval_ttl_seconds: int = DEFAULTS.conversation.approval_ttl_seconds,
    history_ttl_seconds: int = DEFAULTS.conversation.history_ttl_seconds,
) -> None:
    """Run one conversation turn end to end and dispatch the reply to ``surface``.

    The ``principal`` is the user's identity and keys credentials, while ``turn`` names the chat the turn runs in,
    a direct chat or a group chat, with the conversation that scopes history and pending approvals.
    This is side-effect only, the surface sends and the appended history turn.
    """
    if not await turn.can_run(runner):
        # The app cannot serve this chat right now, so the message is dropped without a reply.
        await surface.acknowledge_silently()
        return
    conversation = turn.conversation
    history = await message_store.load_history(conversation)
    if compactor is not None and history:
        compacted = await compactor.compact_if_needed(history)
        if compacted is not None:
            await message_store.replace_history(conversation, compacted, ttl_seconds=history_ttl_seconds)
            history = compacted
    owner = turn.approval_owner(principal)
    pending_approval = await message_store.take_approval(conversation, owner=owner)
    if pending_approval is None and turn.drops_unowned_decision(user_input):
        # Not every surface can tell one member privately, so the tap is dropped quietly on all of them.
        await surface.acknowledge_silently()
        return

    await surface.show_processing()
    result = await runner.run(
        user_input,
        principal=principal,
        history=history,
        pending_approval=pending_approval,
        bridge=bridge,
        surface=surface,
        turn=turn,
    )

    output = result.output
    if isinstance(output, PendingApproval):
        # The turn is only paused, not finished.
        # The in-flight messages ride on ``output.state`` and are replayed via ``pending_approval`` on resume,
        # so committing ``result.history`` here would either wipe the conversation with the empty default,
        # or orphan the tool-call turn from its later result.
        await message_store.park_approval(conversation, output.state, ttl_seconds=approval_ttl_seconds, owner=owner)
        await surface.send_card(output.card, destructive=True)
        return

    if isinstance(output, Carousel):
        await surface.send_carousel([*output.options, output.fallback])
    elif isinstance(output, Card):
        await surface.send_card(output)
    else:
        await surface.send_text(output)

    if result.invalidate_history:
        # A tool made earlier turns stale, e.g. a sign-out, so drop them instead of appending this turn.
        await message_store.clear_history(conversation)
    else:
        await message_store.append_history(conversation, result.history, ttl_seconds=history_ttl_seconds)
