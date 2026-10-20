import abc
import contextlib
import typing

import fastapi

from conciergent.agent.compactor import HistorySummarizer
from conciergent.agent.runner import ChatRunner
from conciergent.defaults import DEFAULTS
from conciergent.group_chat import GroupChatPolicy
from conciergent.identity import ChatSurface
from conciergent.reply import ReplySurface
from conciergent.store.credential import CredentialStore
from conciergent.store.message import MessageStore


class SurfaceContext(typing.NamedTuple):
    """Everything the application hands a surface when it mounts."""

    message_store: MessageStore
    credential_store: CredentialStore
    runner: ChatRunner
    compactor: HistorySummarizer | None
    base_url: str
    approval_ttl_seconds: int = DEFAULTS.conversation.approval_ttl_seconds
    history_ttl_seconds: int = DEFAULTS.conversation.history_ttl_seconds
    oauth_wait_timeout_seconds: float = DEFAULTS.conversation.oauth_wait_timeout_seconds


class PushTarget(typing.NamedTuple):
    """Where an unattended turn's reply goes, a surface that pushes it and the conversation its history joins."""

    conversation: str
    surface: ReplySurface


class Surface(abc.ABC):
    """One chat platform's contribution to the application.

    The application stays ignorant of concrete platforms,
    adding one means implementing this and passing an instance to ``App``, never editing the assembly.
    """

    # The platform's name, which a schedule's target names.
    name: typing.ClassVar[ChatSurface]

    def __init__(self, *, group_chat_policy: GroupChatPolicy = GroupChatPolicy()) -> None:
        # Which group chats this platform answers in, beyond direct messages.
        # None unless configured.
        self.group_chat_policy = group_chat_policy

    @abc.abstractmethod
    def build_routers(self, context: SurfaceContext) -> list[fastapi.APIRouter]:
        """Return the webhook and auxiliary routes this platform needs, empty for a connection surface."""
        ...

    async def run_connection(self, context: SurfaceContext) -> None:
        """Serve a platform that streams its events over a persistent connection, until the app stops.

        The default does nothing, which fits a webhook surface, whose events arrive as requests.
        """
        return None

    def push_target(self, to: str) -> contextlib.AbstractAsyncContextManager[PushTarget]:
        """Open a push to a user or chat id, for a reply nobody asked for, such as a scheduled one.

        The default refuses, which fits a platform that cannot push yet.
        """
        raise NotImplementedError(f'{self.name} cannot push a scheduled reply yet')
