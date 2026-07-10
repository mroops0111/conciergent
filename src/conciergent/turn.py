from conciergent.agent.compactor import HistorySummarizer
from conciergent.agent.runner import ChatRunner
from conciergent.defaults import DEFAULTS
from conciergent.reply import Card, Carousel, ReplySurface
from conciergent.runtime import OAuthBridge, PendingApproval
from conciergent.store.message import MessageStore


async def run_turn(
    user_input: str,
    *,
    principal: str,
    runner: ChatRunner,
    surface: ReplySurface,
    message_store: MessageStore,
    conversation: str | None = None,
    bridge: OAuthBridge | None = None,
    compactor: HistorySummarizer | None = None,
    approval_ttl_seconds: int = DEFAULTS.conversation.approval_ttl_seconds,
    history_ttl_seconds: int = DEFAULTS.conversation.history_ttl_seconds,
) -> None:
    """Run one conversation turn end to end and dispatch the reply to ``surface``.

    The ``principal`` is the user's identity and keys credentials,
    while ``conversation`` scopes history and pending approvals, for example one Slack thread.
    Surfaces without threads leave it unset and the whole dialog with a user is one conversation.
    This is side-effect only, the surface sends and the appended history turn.
    """
    conversation = conversation or principal
    history = await message_store.load_history(conversation)
    if compactor is not None and history:
        compacted = await compactor.compact_if_needed(history)
        if compacted is not None:
            await message_store.replace_history(conversation, compacted, ttl_seconds=history_ttl_seconds)
            history = compacted
    pending_approval = await message_store.take_approval(conversation)

    await surface.show_processing()
    result = await runner.run(
        user_input,
        principal=principal,
        history=history,
        pending_approval=pending_approval,
        bridge=bridge,
        surface=surface,
    )

    output = result.output
    if isinstance(output, PendingApproval):
        # The turn is only paused, not finished.
        # The in-flight messages ride on ``output.state`` and are replayed via ``pending_approval`` on resume,
        # so committing ``result.history`` here would either wipe the conversation with the empty default,
        # or orphan the tool-call turn from its later result.
        await message_store.park_approval(conversation, output.state, ttl_seconds=approval_ttl_seconds)
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
