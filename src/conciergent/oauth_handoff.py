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
