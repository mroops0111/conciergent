import abc
import dataclasses
import typing
import urllib.parse

from .oauth_handoff import WAIT_TIMEOUT_SECONDS, OAuthHandoffExpiredError
from .reply import Card, Carousel, Reply, ReplySurface
from .stores.base import OAuthCodeStore, Store


@dataclasses.dataclass
class PendingApproval:
    """A request for the user to approve one or more sensitive actions before they run.

    The card renders the confirmation.
    The ``state`` is an opaque JSON-serializable dict that the store parks and hands back on resume,
    only the agent that produced it reads it back.
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


class StatefulOAuthBridge(OAuthBridge):
    """Complete an in-chat OAuth authorization by round-tripping the ``state`` through the store.

    ``request_authorization`` extracts the state from the authorize URL, lets the surface render the
    link to the user, then blocks until the callback route delivers the code for that state.
    Subclasses implement only the rendering.
    """

    def __init__(self, store: OAuthCodeStore, *, wait_timeout_seconds: float = WAIT_TIMEOUT_SECONDS) -> None:
        self._store = store
        self._wait_timeout_seconds = wait_timeout_seconds

    @typing.override
    async def request_authorization(self, authorize_url: str) -> str:
        query = urllib.parse.parse_qs(urllib.parse.urlparse(authorize_url).query)
        states = query.get('state')
        if not states:
            raise ValueError('the authorization URL carries no state parameter')
        await self._render_authorization_ui(authorize_url)
        code = await self._store.await_oauth_code(states[0], timeout_seconds=self._wait_timeout_seconds)
        if code is None:
            raise OAuthHandoffExpiredError
        return code

    @abc.abstractmethod
    async def _render_authorization_ui(self, authorize_url: str) -> None:
        """Show the authorize URL to the user, for example as a button in the conversation."""
        ...


class HistoryCompactor(abc.ABC):
    """Shrink an oversized history before the agent runs, keeping the spine ignorant of its format."""

    @abc.abstractmethod
    async def compact_if_needed(self, history: list[typing.Any]) -> list[typing.Any] | None:
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
        surface: ReplySurface | None,
    ) -> AgentResult: ...

    async def bootstrap(self, principal: str, *, bridge: OAuthBridge | None = None) -> bool:
        """Open the agent's backing context without running it, and report whether the user just authorized.

        Surface lifecycle hooks call this, for example when a user adds the bot,
        so a pending OAuth flow fires at add time instead of on the first message.
        Returns True only when an authorization completed during this call; the default has nothing to open.
        """
        return False


async def run_turn(
    user_input: str,
    *,
    principal: str,
    agent: ChatAgent,
    surface: ReplySurface,
    store: Store,
    conversation: str | None = None,
    bridge: OAuthBridge | None = None,
    compactor: HistoryCompactor | None = None,
    approval_ttl_seconds: int = 600,
    history_ttl_seconds: int = 604800,
) -> None:
    """Run one conversation turn end to end and dispatch the reply to ``surface``.

    The ``principal`` is the user's identity and keys credentials,
    while ``conversation`` scopes history and pending approvals, for example one Slack thread.
    Surfaces without threads leave it unset and the whole dialog with a user is one conversation.
    This is side-effect only, the surface sends and the appended history turn.
    """
    conversation = conversation or principal
    history = await store.load_history(conversation)
    if compactor is not None and history:
        compacted = await compactor.compact_if_needed(history)
        if compacted is not None:
            await store.replace_history(conversation, compacted, ttl_seconds=history_ttl_seconds)
            history = compacted
    pending = await store.take_approval(conversation)

    await surface.show_processing()
    result = await agent.run(
        user_input, principal=principal, history=history, pending=pending, bridge=bridge, surface=surface
    )

    output = result.output
    if isinstance(output, PendingApproval):
        # The turn is only paused, not finished.
        # The in-flight messages ride on ``output.state`` and are replayed via ``pending`` on resume,
        # so committing ``result.history`` here would either wipe the conversation with the empty default,
        # or orphan the tool-call turn from its later result.
        await store.park_approval(conversation, output.state, ttl_seconds=approval_ttl_seconds)
        await surface.send_card(output.card, destructive=True)
        return

    if isinstance(output, Carousel):
        await surface.send_carousel([*output.options, output.fallback])
    elif isinstance(output, Card):
        await surface.send_card(output)
    else:
        await surface.send_text(output)

    await store.append_history(conversation, result.history, ttl_seconds=history_ttl_seconds)
