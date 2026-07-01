import abc
import dataclasses
import typing

from .reply import Card, Carousel, Reply, ReplySurface
from .stores.base import Store


@dataclasses.dataclass
class PendingApproval:
    """A request for the user to approve one or more sensitive actions before they run.

    This is framework-neutral on purpose, so the agent library's own suspension types are not exposed here.
    An agent that cannot pause simply never returns one.
    """

    card: Card
    state: dict[str, typing.Any] = dataclasses.field(default_factory=dict)


@dataclasses.dataclass
class AgentResult:
    """The outcome of one agent run, carrying the reply to send and the message history to persist."""

    output: Reply | PendingApproval
    history: list[typing.Any] = dataclasses.field(default_factory=list)


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
    ) -> AgentResult: ...


class OAuthBridge(abc.ABC):
    """Drive an OAuth authorization that happens inside the conversation."""

    @abc.abstractmethod
    async def request_authorization(self, authorize_url: str) -> str:
        """Show the user the authorize URL and return the code once they complete the flow."""
        ...


async def run_turn(
    user_input: str,
    *,
    principal: str,
    agent: ChatAgent,
    surface: ReplySurface,
    store: Store,
    approval_ttl_seconds: int = 3600,
) -> None:
    """Run one conversation turn end to end and dispatch the reply to ``surface``.

    This is side-effect only, the surface sends and the persisted history.
    Tests observe it through the fake surface and store.
    """
    history = await store.load_history(principal)
    pending = await store.take_approval(principal)

    await surface.show_processing()
    result = await agent.run(user_input, principal=principal, history=history, pending=pending)

    output = result.output
    if isinstance(output, PendingApproval):
        # The turn is only paused, not finished. The in-flight messages ride on ``output.state`` and are
        # replayed via ``pending`` on resume, so committing ``result.history`` here would either wipe the
        # conversation (empty default) or orphan the tool-call turn from its later result.
        await store.park_approval(principal, output.state, ttl_seconds=approval_ttl_seconds)
        await surface.send_card(output.card, destructive=True)
        return

    if isinstance(output, Carousel):
        await surface.send_carousel(output.cards)
    elif isinstance(output, Card):
        await surface.send_card(output)
    else:
        await surface.send_text(output)

    await store.save_history(principal, result.history)
