class OAuthHandoffExpiredError(Exception):
    """The user received the authorization link but never completed the flow in time."""


def is_handoff_expiry(error: BaseException) -> bool:
    """Report whether ``error`` is a handoff expiry, unwrapping the groups task runners nest it in."""
    if isinstance(error, OAuthHandoffExpiredError):
        return True
    if isinstance(error, BaseExceptionGroup):
        return bool(error.exceptions) and all(is_handoff_expiry(inner) for inner in error.exceptions)
    return False
