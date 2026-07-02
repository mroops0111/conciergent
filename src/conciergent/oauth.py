import abc
import urllib.parse

from .runtime import OAuthBridge
from .stores.base import Store


# OAuth authorization codes expire within minutes on most servers.
# Keep the wait below any outer MCP initialization timeout so the timeout surfaces here,
# with a clear expiry, instead of being swallowed by an outer cancellation.
WAIT_TIMEOUT_SECONDS = 240.0


class OAuthHandoffExpiredError(Exception):
    """The user received the authorization link but never completed the flow in time."""


def is_handoff_expiry(error: BaseException) -> bool:
    """Report whether ``error`` is a handoff expiry, unwrapping the groups task runners nest it in."""
    if isinstance(error, OAuthHandoffExpiredError):
        return True
    if isinstance(error, BaseExceptionGroup):
        return bool(error.exceptions) and all(is_handoff_expiry(inner) for inner in error.exceptions)
    return False


class StatefulOAuthBridge(OAuthBridge):
    """Complete an in-chat OAuth authorization by round-tripping the ``state`` through the store.

    ``request_authorization`` extracts the state from the authorize URL, lets the surface render the
    link to the user, then blocks until the callback route delivers the code for that state.
    Subclasses implement only the rendering.
    """

    def __init__(self, store: Store, *, wait_timeout_seconds: float = WAIT_TIMEOUT_SECONDS) -> None:
        self._store = store
        self._wait_timeout_seconds = wait_timeout_seconds

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
