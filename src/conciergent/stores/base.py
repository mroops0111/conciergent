import abc
import collections.abc
import typing

from conciergent.defaults import DEFAULTS


# Re-exported so the backends default to it without each reaching into the defaults tree.
DEFAULT_MAX_TURNS = DEFAULTS.store.max_turns


class HistoryStore(abc.ABC):
    """Conversation history, one expiring turn at a time."""

    @abc.abstractmethod
    async def load_history(self, conversation: str) -> list[typing.Any]:
        """Return the still-live turns of one conversation, flattened into one message list."""
        ...

    @abc.abstractmethod
    async def append_history(self, conversation: str, messages: list[typing.Any], *, ttl_seconds: int) -> None:
        """Append one turn of messages, which ages out on its own after ``ttl_seconds``.

        Backends also keep only a bounded number of recent turns,
        so history behaves identically on the in-memory default and on networked implementations.
        """
        ...

    @abc.abstractmethod
    async def replace_history(self, conversation: str, messages: list[typing.Any], *, ttl_seconds: int) -> None:
        """Replace every stored turn with ``messages`` as one fresh turn, used by history compaction.

        Callers must serialize turns per conversation, a concurrent append between load and replace is lost.
        """
        ...


class ApprovalStore(abc.ABC):
    """The human-in-the-loop parking lot, one pending approval per conversation."""

    @abc.abstractmethod
    async def park_approval(
        self, conversation: str, state: collections.abc.Mapping[str, typing.Any], *, ttl_seconds: int
    ) -> None: ...

    @abc.abstractmethod
    async def take_approval(self, conversation: str) -> dict[str, typing.Any] | None:
        """Return and clear any approval state parked on one conversation."""
        ...


class DedupeStore(abc.ABC):
    """Idempotency for redelivered webhooks."""

    @abc.abstractmethod
    async def dedupe(self, key: str, *, ttl_seconds: int) -> bool:
        """Record ``key`` and report whether it had already been recorded within the ttl window.

        Returns True when ``key`` is a repeat and False when it is new, which lets a caller drop
        duplicate work such as a redelivered webhook. Recording the key is a side effect of the check.
        """
        ...


class OAuthCodeStore(abc.ABC):
    """The in-chat OAuth handoff, carrying the authorization code from the callback to the waiting turn."""

    @abc.abstractmethod
    async def deliver_oauth_code(self, state: str, code: str) -> None:
        """Hand an OAuth authorization code to whoever awaits ``state``, called by the callback route."""
        ...

    @abc.abstractmethod
    async def await_oauth_code(self, state: str, *, timeout_seconds: float) -> str | None:
        """Block until the code for ``state`` arrives and return it, or None when the wait times out.

        The in-memory default only bridges coroutines inside one process,
        multi-process deployments need a networked backend for this handoff.
        """
        ...


class CredentialStore(abc.ABC):
    """Long-lived credentials, MCP OAuth tokens and clients plus per-tenant bot tokens."""

    @abc.abstractmethod
    async def get_mcp_token(self, server: str, principal: str) -> dict[str, typing.Any] | None:
        """Return the stored OAuth token for one user of one MCP server, as an opaque dict."""
        ...

    @abc.abstractmethod
    async def set_mcp_token(
        self, server: str, principal: str, token: collections.abc.Mapping[str, typing.Any]
    ) -> None: ...

    @abc.abstractmethod
    async def get_mcp_client(self, server: str) -> dict[str, typing.Any] | None:
        """Return the dynamically registered OAuth client for one MCP server, shared across users."""
        ...

    @abc.abstractmethod
    async def set_mcp_client(self, server: str, client: collections.abc.Mapping[str, typing.Any]) -> None: ...

    @abc.abstractmethod
    async def resolve_bot_token(self, surface: str, tenant: str) -> str | None:
        """Return the bot credential installed for one tenant of one surface, for example a Slack team."""
        ...

    @abc.abstractmethod
    async def set_bot_token(self, surface: str, tenant: str, token: str) -> None: ...


class Store(HistoryStore, ApprovalStore, DedupeStore, OAuthCodeStore, CredentialStore, abc.ABC):
    """Everything a full backend persists, the union of the per-concern interfaces above.

    Backends implement this whole class, while single-purpose consumers depend on the narrow
    interface they actually use, for example the OAuth bridge only sees ``OAuthCodeStore``.
    The in-memory default needs no infrastructure.
    """

    async def prepare(self) -> None:
        """Set the backend up before serving, for example creating tables; a no-op by default."""
        return None
