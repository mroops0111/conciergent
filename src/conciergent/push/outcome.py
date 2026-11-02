import enum


# These live apart from the push executor, so the push command's control-socket client imports no agent code.


class UnknownPushError(LookupError):
    """No configured push has the name a run asked for."""


class PushOutcome(enum.StrEnum):
    """How a push ended, judged by whether anything reached the chat, since that decides if a retry is safe."""

    delivered = 'delivered'
    skipped = 'skipped'
    # Nothing but the failure message reached the chat, so running the push again cannot repeat a reply.
    failed = 'failed'
