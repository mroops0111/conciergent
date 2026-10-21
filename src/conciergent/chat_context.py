import abc
import dataclasses
import datetime
import typing

from conciergent import i18n
from conciergent.agent.mcp.tool_policy import (
    ConfirmDestructiveToolPolicy,
    DestructiveToolPolicy,
    HideDestructiveToolPolicy,
    RunDestructiveToolPolicy,
)
from conciergent.reply import ReplySurface


if typing.TYPE_CHECKING:
    from conciergent.agent.runner import ChatRunner


_GROUP_CHAT_INSTRUCTIONS = (
    'This is a group chat shared by several people. '
    "Each user message starts with its speaker's name in square brackets, such as [Alice]. "
    'Answer the latest speaker, and tell people apart by those names. '
    'Never write the bracketed name prefix in your own reply.'
)
_SCHEDULED_INSTRUCTIONS = (
    'This turn was started by a schedule, not by a person, and its message starts with the schedule name and time. '
    'Nobody can answer a question or confirm an action before your reply is sent, so write the finished message the prompt asks for. '
    'Never write the bracketed schedule prefix in your own reply.'
)
_SKIP_INSTRUCTIONS = 'When the prompt finds nothing worth sending, end the turn with skip_reply instead of a reply.'
# Spelled out here rather than with strftime, whose day names follow the process locale.
_WEEKDAYS = ('Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday')
# Policies are stateless, so every chat shares one of each.
_CONFIRM_DESTRUCTIVE_TOOL_POLICY = ConfirmDestructiveToolPolicy()
_HIDE_DESTRUCTIVE_TOOL_POLICY = HideDestructiveToolPolicy()
_RUN_DESTRUCTIVE_TOOL_POLICY = RunDestructiveToolPolicy()


class TurnDeclinedError(Exception):
    """A turn nobody is waiting on cannot run, such as when an MCP server is unreachable."""


@dataclasses.dataclass(frozen=True)
class ChatContext(abc.ABC):
    """The chat one turn runs in, a direct chat with one user or a group chat shared by several.

    The kinds differ in whose history and approvals the turn uses, whose credentials reach the MCP servers,
    and how the agent reads the message, so each kind answers those questions and the runtime never branches.
    The abstract members are what every kind must decide.
    The concrete ones are what an interactive chat does, which a new kind overrides only where it differs.
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

    async def decline(self, surface: ReplySurface) -> None:
        """Settle a turn the app cannot serve right now, see ``can_run``.

        Someone in a chat sent the message, so by default it is dropped quietly, as the platform needs.
        """
        await surface.acknowledge_silently()

    @property
    def allows_skip(self) -> bool:
        """Whether the turn may end with no reply at all, which only a turn nobody asked for needs."""
        return False

    @property
    def destructive_tool_policy(self) -> DestructiveToolPolicy:
        """How the turn treats a destructive tool, which by default someone in the chat confirms first."""
        return _CONFIRM_DESTRUCTIVE_TOOL_POLICY


@dataclasses.dataclass(frozen=True)
class DirectChatContext(ChatContext):
    """A direct chat, where one user owns the history, the approvals, and the credentials."""

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
class GroupChatContext(ChatContext):
    """A group chat, whose members share the history while each one owns their approvals.

    It holds no one's authorization, so it runs only while every MCP server works without a user's token,
    and it names its speaker so the agent can tell the members apart.
    """

    # The name of the member who sent this message.
    speaker: str

    @typing.override
    async def can_run(self, runner: 'ChatRunner') -> bool:
        return await runner.works_without_user_credentials()

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


@dataclasses.dataclass(frozen=True)
class ScheduledChatContext(ChatContext):
    """An unattended turn a schedule starts, whose reply is pushed into a chat's conversation.

    Nobody is present to authorize, confirm, or sign out,
    so like a group chat the turn holds no one's authorization, and it never asks about a destructive tool.
    """

    # The schedule's name and the time it fired, which head the prompt so the agent knows what started it and when.
    schedule_name: str
    fired_at: datetime.datetime
    # Stands in for the base class property of the same name.
    allows_skip: bool = False
    # Nobody can confirm a destructive tool, so it is left out unless the schedule opts into running it unasked.
    runs_destructive_tools: bool = False

    @typing.override
    async def can_run(self, runner: 'ChatRunner') -> bool:
        return await runner.works_without_user_credentials()

    @typing.override
    async def decline(self, surface: ReplySurface) -> None:
        # Nobody sent a message to drop, so a quiet ending would hide an outage behind silence.
        raise TurnDeclinedError('an MCP server is unreachable or needs a per-user authorization')

    @typing.override
    def approval_owner(self, principal: str) -> str | None:
        # The conversation may be a user's direct chat, whose own approval sits under no owner,
        # so a schedule keys its own and never takes theirs.
        return f'schedule:{self.schedule_name}'

    @typing.override
    def drops_unowned_decision(self, user_input: str) -> bool:
        return False

    @property
    @typing.override
    def uses_user_credentials(self) -> bool:
        return False

    @property
    @typing.override
    def allows_sign_out(self) -> bool:
        return False

    @property
    @typing.override
    def destructive_tool_policy(self) -> DestructiveToolPolicy:
        return _RUN_DESTRUCTIVE_TOOL_POLICY if self.runs_destructive_tools else _HIDE_DESTRUCTIVE_TOOL_POLICY

    @property
    @typing.override
    def instructions(self) -> str:
        return f'{_SCHEDULED_INSTRUCTIONS} {_SKIP_INSTRUCTIONS}' if self.allows_skip else _SCHEDULED_INSTRUCTIONS

    @typing.override
    def prompt(self, user_input: str) -> str:
        # A ZoneInfo prints its IANA name, which says more than an offset or an abbreviation like CST.
        # The weekday is spelled out because models often get it wrong when they work it out from a date.
        moment = self.fired_at.isoformat(timespec='minutes')
        when = f'{moment} {self.fired_at.tzinfo}, {_WEEKDAYS[self.fired_at.weekday()]}'
        return f'[Scheduled: {self.schedule_name}, {when}] {user_input}'
