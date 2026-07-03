import abc
import dataclasses
import typing
import urllib.parse

from conciergent.defaults import DEFAULTS
from conciergent.oauth_handoff import OAuthHandoffExpiredError
from conciergent.reply import Card, Reply
from conciergent.stores.base import OAuthCodeStore


@dataclasses.dataclass
class PendingApproval:
    """A request for the user to approve one or more sensitive actions before they run.

    The card renders the confirmation.
    The ``state`` is an opaque JSON-serializable dict that the store parks and hands back on resume,
    only the runner that produced it reads it back.
    """

    card: Card
    state: dict[str, typing.Any] = dataclasses.field(default_factory=dict)


@dataclasses.dataclass
class TurnResult:
    """The outcome of one turn, carrying the reply to send and this turn's new messages to append."""

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

    ``request_authorization`` extracts the state from the authorize URL, lets the surface render the link to the user,
    then blocks until the callback route delivers the code for that state.
    """

    def __init__(
        self, store: OAuthCodeStore, *, wait_timeout_seconds: float = DEFAULTS.conversation.oauth_wait_timeout_seconds
    ) -> None:
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


class AuthorizationProbe(OAuthBridge):
    """Wrap a bridge and record whether an authorization actually ran through it.

    A bridge is only called when a real authorization is needed,
    so a completed delegation is exactly the just-authorized signal that bootstrap reports.
    """

    def __init__(self, inner: OAuthBridge) -> None:
        self._inner = inner
        self.authorized = False

    @typing.override
    async def request_authorization(self, authorize_url: str) -> str:
        code = await self._inner.request_authorization(authorize_url)
        self.authorized = True
        return code
