import abc
import dataclasses
import typing

from .reply import Card, Carousel, Reply, ReplySurface
from .stores.base import Store


@dataclasses.dataclass
class PendingApproval:
    """A request for the user to approve one or more sensitive actions before they run.

    The card renders the confirmation.
    The ``state`` is an opaque JSON-serializable dict that the store parks and hands back on resume.
    Only the agent adapter that produced it reads it back, so payload compatibility is handled in one place.
    """

    card: Card
    state: dict[str, typing.Any] = dataclasses.field(default_factory=dict)


@dataclasses.dataclass
class AgentResult:
    """The outcome of one agent run, carrying the reply to send and this turn's new messages to append."""

    output: Reply | PendingApproval
    history: list[typing.Any] = dataclasses.field(default_factory=list)


class OAuthBridge(abc.ABC):
    """Drive an OAuth authorization that happens inside the conversation."""

    @abc.abstractmethod
    async def request_authorization(self, authorize_url: str) -> str:
        """Show the user the authorize URL and return the code once they complete the flow."""
        ...


class HistoryCompactor(abc.ABC):
    """Shrink an oversized history before the agent runs, keeping the spine ignorant of its format."""

    @abc.abstractmethod
    async def compact(self, history: list[typing.Any]) -> list[typing.Any] | None:
        """Return the replacement history when compaction fired, or None to keep it as is."""
        ...


class ChatAgent(abc.ABC):
    """The minimal contract the runtime needs from an agent implementation."""

    @abc.abstractmethod
    async def run(
        self,
        user_input: str,
        *,
        principal: str,
        history: list[typing.Any],
        pending: dict[str, typing.Any] | None,
        bridge: OAuthBridge | None,
    ) -> AgentResult: ...


async def run_turn(
    user_input: str,
    *,
    principal: str,
    agent: ChatAgent,
    surface: ReplySurface,
    store: Store,
    bridge: OAuthBridge | None = None,
    compactor: HistoryCompactor | None = None,
    approval_ttl_seconds: int = 600,
    history_ttl_seconds: int = 604800,
) -> None:
    """Run one conversation turn end to end and dispatch the reply to ``surface``.

    This is side-effect only, the surface sends and the appended history turn.
    Tests observe it through the fake surface and store.
    """
    history = await store.load_history(principal)
    if compactor is not None and history:
        compacted = await compactor.compact(history)
        if compacted is not None:
            await store.replace_history(principal, compacted, ttl_seconds=history_ttl_seconds)
            history = compacted
    pending = await store.take_approval(principal)

    await surface.show_processing()
    result = await agent.run(user_input, principal=principal, history=history, pending=pending, bridge=bridge)

    output = result.output
    if isinstance(output, PendingApproval):
        # The turn is only paused, not finished.
        # The in-flight messages ride on ``output.state`` and are replayed via ``pending`` on resume,
        # so committing ``result.history`` here would either wipe the conversation with the empty default,
        # or orphan the tool-call turn from its later result.
        await store.park_approval(principal, output.state, ttl_seconds=approval_ttl_seconds)
        await surface.send_card(output.card, destructive=True)
        return

    if isinstance(output, Carousel):
        await surface.send_carousel([*output.options, output.fallback])
    elif isinstance(output, Card):
        await surface.send_card(output)
    else:
        await surface.send_text(output)

    await store.append_history(principal, result.history, ttl_seconds=history_ttl_seconds)
