"""The per-turn orchestrator and the interfaces it depends on.

``run_turn`` is agnostic to the concrete surface, agent and store. It loads
conversation state, runs the agent, dispatches the reply by its shape, and
persists the updated state. All surface- and agent-specific behaviour lives
behind the interfaces defined here.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Any

from .reply import Card, Carousel, Reply, ReplySurface
from .stores.base import Store


@dataclass
class PendingApproval:
    """A framework-neutral request for user approval before running one or more
    sensitive actions.

    The agent library's own suspension types are deliberately not exposed here, so
    an agent that cannot suspend simply never returns this.
    """

    card: Card
    """The confirm/cancel UI to show the user."""
    state: dict[str, Any] = field(default_factory=dict)
    """Opaque data needed to resume the run once the user decides."""


@dataclass
class AgentResult:
    """The outcome of a single agent run."""

    output: Reply | PendingApproval
    history: list[Any] = field(default_factory=list)
    """The updated message history to persist for the next turn."""


class ChatAgent(abc.ABC):
    """The minimal contract the runtime needs from an agent implementation."""

    @abc.abstractmethod
    async def run(
        self,
        user_input: str,
        *,
        principal: str,
        history: list[Any],
        pending: dict[str, Any] | None,
    ) -> AgentResult: ...

    async def bootstrap(self, *, principal: str) -> None:
        """Optional per-user setup before the first turn (for example, priming credentials)."""
        return None

    @property
    def input_token_limit(self) -> int | None:
        """The model's input token limit, if known, used to trigger history compaction."""
        return None

    @property
    def history_invalidating_tools(self) -> set[str]:
        """Tools whose use makes prior history stale and should trigger a reset."""
        return set()


class OAuthBridge(abc.ABC):
    """Drives an OAuth authorization that happens inside the conversation: show the
    user an authorize URL and return the authorization code once they complete it."""

    @abc.abstractmethod
    async def request_authorization(self, authorize_url: str) -> str: ...


async def run_turn(
    user_input: str,
    *,
    principal: str,
    agent: ChatAgent,
    surface: ReplySurface,
    store: Store,
    approval_ttl_seconds: int = 3600,
) -> Reply | PendingApproval:
    """Run one conversation turn end-to-end and dispatch the reply to ``surface``.

    Returns the agent's output (useful for tests); the side effects are the
    surface sends and the persisted history.
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
