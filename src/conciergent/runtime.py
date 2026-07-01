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
    """The outcome of one agent run: what to reply, and the message history to persist."""

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

    async def bootstrap(self, *, principal: str) -> None:
        """Run optional per-user setup before the first turn, for example priming credentials."""
        return None

    @property
    def input_token_limit(self) -> int | None:
        """Return the model's input token limit when known, used to trigger history compaction."""
        return None

    @property
    def history_invalidating_tools(self) -> set[str]:
        """Return the tools whose use makes prior history stale and should trigger a reset."""
        return set()


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
) -> Reply | PendingApproval:
    """Run one conversation turn end to end and dispatch the reply to ``surface``.

    The reply is also returned, which is convenient for tests.
    The side effects are the surface sends and the persisted history.
    """
    history = await store.load_history(principal)
    pending = await store.take_approval(principal)

    await surface.show_processing()
    result = await agent.run(user_input, principal=principal, history=history, pending=pending)

    output = result.output
    if isinstance(output, PendingApproval):
        await store.park_approval(principal, output.state, ttl_seconds=approval_ttl_seconds)
        await surface.send_card(output.card, destructive=True)
    elif isinstance(output, Carousel):
        await surface.send_carousel(output.cards)
    elif isinstance(output, Card):
        await surface.send_card(output)
    else:
        await surface.send_text(output)

    await store.save_history(principal, result.history)
    return output
