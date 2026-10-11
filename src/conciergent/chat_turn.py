import abc
import dataclasses
import typing

from conciergent import i18n


if typing.TYPE_CHECKING:
    from conciergent.agent.runner import ChatRunner


_GROUP_CHAT_INSTRUCTIONS = (
    'This is a group chat shared by several people. '
    "Each user message starts with its speaker's name in square brackets, such as [Alice]. "
    'Answer the latest speaker, and tell people apart by those names. '
    'Never write the bracketed name prefix in your own reply.'
)


@dataclasses.dataclass(frozen=True)
class ChatTurn(abc.ABC):
    """The chat one turn runs in, a direct chat with one user or a group chat shared by several.

    The two differ in whose history and approvals the turn uses, whose credentials reach the MCP servers,
    and how the agent reads the message, so each kind answers those questions and the runtime never branches.
    """

    # The history scope, such as one user's direct chat, one Slack thread, or one group chat.
    conversation: str

    @abc.abstractmethod
    async def can_run(self, runner: 'ChatRunner') -> bool:
        """Whether the app can serve this chat right now."""

    @abc.abstractmethod
    def approval_owner(self, principal: str) -> str | None:
        """Who owns the approvals this turn parks and resumes, or None when the conversation has one user."""

    @abc.abstractmethod
    def drops_unowned_decision(self, user_input: str) -> bool:
        """Whether a confirm or cancel with no approval of the sender's to resolve is dropped instead of run."""

    @property
    @abc.abstractmethod
    def uses_user_credentials(self) -> bool:
        """Whether the MCP servers are reached with the user's own OAuth tokens."""

    @property
    @abc.abstractmethod
    def allows_sign_out(self) -> bool:
        """Whether the sign-out tool is offered, which clears the conversation's history."""

    @property
    @abc.abstractmethod
    def instructions(self) -> str:
        """Extra system instructions this kind of chat needs, empty when none."""

    @abc.abstractmethod
    def prompt(self, user_input: str) -> str:
        """The message as the agent reads it."""


@dataclasses.dataclass(frozen=True)
class DirectChatTurn(ChatTurn):
    """A turn in a direct chat, where one user owns the history, the approvals, and the credentials."""

    @typing.override
    async def can_run(self, runner: 'ChatRunner') -> bool:
        return True

    @typing.override
    def approval_owner(self, principal: str) -> str | None:
        return None

    @typing.override
    def drops_unowned_decision(self, user_input: str) -> bool:
        return False

    @property
    @typing.override
    def uses_user_credentials(self) -> bool:
        return True

    @property
    @typing.override
    def allows_sign_out(self) -> bool:
        return True

    @property
    @typing.override
    def instructions(self) -> str:
        return ''

    @typing.override
    def prompt(self, user_input: str) -> str:
        return user_input


@dataclasses.dataclass(frozen=True)
class GroupChatTurn(ChatTurn):
    """A turn in a group chat, whose members share the history while each one owns their approvals.

    It holds no one's authorization, so it runs only while every MCP server works without a user's token,
    and it names its speaker so the agent can tell the members apart.
    """

    # The name of the member who sent this message.
    speaker: str

    @typing.override
    async def can_run(self, runner: 'ChatRunner') -> bool:
        return await runner.supports_group_chats()

    @typing.override
    def approval_owner(self, principal: str) -> str | None:
        return principal

    @typing.override
    def drops_unowned_decision(self, user_input: str) -> bool:
        # A confirm or cancel with nothing of the speaker's to resolve is someone else's card or an expired one,
        # and running it as a message would only confuse the agent.
        return user_input in i18n.variants('approval.confirm') | i18n.variants('approval.cancel')

    @property
    @typing.override
    def uses_user_credentials(self) -> bool:
        return False

    @property
    @typing.override
    def allows_sign_out(self) -> bool:
        # A sign-out clears the conversation's history, which in a group belongs to everyone, not the speaker.
        return False

    @property
    @typing.override
    def instructions(self) -> str:
        return _GROUP_CHAT_INSTRUCTIONS

    @typing.override
    def prompt(self, user_input: str) -> str:
        return f'[{self.speaker}] {user_input}'
