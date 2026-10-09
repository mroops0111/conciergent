from conciergent import i18n
from conciergent.agent.compactor import HistorySummarizer
from conciergent.agent.runner import ChatRunner
from conciergent.defaults import DEFAULTS
from conciergent.groups import GroupTurn
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
    group: GroupTurn | None = None,
) -> None:
    """Run one conversation turn end to end and dispatch the reply to ``surface``.

    The ``principal`` is the user's identity and keys credentials,
    while ``conversation`` scopes history and pending approvals, for example one Slack thread.
    Surfaces without threads leave it unset and the whole dialog with a user is one conversation.
    A ``group`` makes this a group-chat turn in its shared conversation, served only while the app can serve groups.
    Its members share the history while each one owns the approvals their own turns parked.
    This is side-effect only, the surface sends and the appended history turn.
    """
    if group is not None and not await runner.supports_groups():
        # The app cannot serve groups right now, so the message is dropped without a reply.
        await surface.acknowledge_silently()
        return
    conversation = group.conversation if group is not None else conversation or principal
    history = await message_store.load_history(conversation)
    if compactor is not None and history:
        compacted = await compactor.compact_if_needed(history)
        if compacted is not None:
            await message_store.replace_history(conversation, compacted, ttl_seconds=history_ttl_seconds)
            history = compacted
    # In a group each member owns the approvals their own turns parked, so another member's message never takes one.
    owner = principal if group is not None else None
    pending_approval = await message_store.take_approval(conversation, owner=owner)
    if group is not None and pending_approval is None and _is_approval_decision(user_input):
        # A confirm or cancel with nothing of the speaker's to resolve is someone else's card or an expired one.
        # Running it as a message would only confuse the agent, and not every surface can tell one member privately,
        # so the tap is dropped quietly on all of them.
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
        group=group,
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


def _is_approval_decision(user_input: str) -> bool:
    return user_input in i18n.variants('approval.confirm') | i18n.variants('approval.cancel')
