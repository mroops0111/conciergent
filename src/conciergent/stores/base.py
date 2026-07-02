import abc
import collections.abc
import typing


class Store(abc.ABC):
    """Persist the small amount of state the runtime needs across turns and requests.

    The in-memory default needs no infrastructure, and networked backends implement the same interface.
    The interface grows as surfaces are added, so this defines only the parts the core runtime depends on.
    """

    @abc.abstractmethod
    async def load_history(self, principal: str) -> list[typing.Any]:
        """Return the still-live turns for ``principal``, flattened into one message list."""
        ...

    @abc.abstractmethod
    async def append_history(self, principal: str, messages: list[typing.Any], *, ttl_seconds: int) -> None:
        """Append one turn of messages, which ages out on its own after ``ttl_seconds``.

        Backends also keep only a bounded number of recent turns,
        so history behaves identically on the in-memory default and on networked implementations.
        """
        ...

    @abc.abstractmethod
    async def replace_history(self, principal: str, messages: list[typing.Any], *, ttl_seconds: int) -> None:
        """Replace every stored turn with ``messages`` as one fresh turn, used by history compaction.

        Callers must serialize turns per principal, a concurrent append between load and replace is lost.
        """
        ...

    @abc.abstractmethod
    async def dedupe(self, key: str, *, ttl_seconds: int) -> bool:
        """Record ``key`` and report whether it had already been recorded within the ttl window.

        Returns True when ``key`` is a repeat and False when it is new, which lets a caller drop
        duplicate work such as a redelivered webhook. Recording the key is a side effect of the check.
        """
        ...

    @abc.abstractmethod
    async def park_approval(
        self, principal: str, state: collections.abc.Mapping[str, typing.Any], *, ttl_seconds: int
    ) -> None: ...

    @abc.abstractmethod
    async def take_approval(self, principal: str) -> dict[str, typing.Any] | None:
        """Return and clear any parked approval state for ``principal``."""
        ...

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
