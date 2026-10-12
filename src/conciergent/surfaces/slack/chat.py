import abc
import dataclasses
import re
import typing

from conciergent.chat_context import ChatContext, DirectChatContext, GroupChatContext
from conciergent.group_chat import GroupChatPolicy, ReplyTrigger, without_spans
from conciergent.identity import ChatSurface, make_principal


# The channel types a group message arrives from: a public channel, a private one, and a multi-person DM.
_CHANNEL_TYPES = frozenset({'channel', 'group', 'mpim'})


@dataclasses.dataclass(frozen=True)
class SlackChat(abc.ABC):
    """The Slack chat a message or click came from, which decides how the bot reads it and replies there.

    A direct message and a channel differ in which events count, whose conversation a thread is,
    and whether the turn is shared, so each kind answers those questions itself.
    """

    team_id: str
    user_id: str
    channel: str
    # The thread the reply goes in, which is also the conversation's scope.
    thread_ts: str | None

    @property
    def principal(self) -> str:
        return make_principal(ChatSurface.slack, self.team_id, self.user_id)

    @property
    @abc.abstractmethod
    def conversation(self) -> str:
        """The history scope, one per Slack thread."""

    @abc.abstractmethod
    def answers(self, event: dict[str, typing.Any]) -> bool:
        """Whether a typed message event is for the bot, so it starts a turn."""

    @abc.abstractmethod
    def turn_input(self, event: dict[str, typing.Any], *, bot_user_id: str | None) -> str:
        """The text of a typed message the agent reads."""

    @abc.abstractmethod
    def exclusive_pick_key(self, message_ts: str) -> str:
        """The dedupe key for a pick that consumes a whole message, such as a confirmation."""

    @abc.abstractmethod
    def resolve_chat_context(self, display_name: str | None) -> ChatContext:
        """The chat context this message runs in, given the sender's Slack display name."""

    @abc.abstractmethod
    def reply_addressee(self, *, typed: bool) -> str | None:
        """The user a text reply mentions to show who it answers, or None to mention no one."""


@dataclasses.dataclass(frozen=True)
class SlackDirectChat(SlackChat):
    """A direct message with the bot, where each thread belongs to its one user."""

    @property
    @typing.override
    def conversation(self) -> str:
        return f'{self.principal}:{self.thread_ts}' if self.thread_ts else self.principal

    @typing.override
    def answers(self, event: dict[str, typing.Any]) -> bool:
        return event.get('type') == 'message'

    @typing.override
    def turn_input(self, event: dict[str, typing.Any], *, bot_user_id: str | None) -> str:
        return event.get('text', '')

    @typing.override
    def exclusive_pick_key(self, message_ts: str) -> str:
        return f'slack:interaction:{self.channel}:{message_ts}'

    @typing.override
    def resolve_chat_context(self, display_name: str | None) -> ChatContext:
        return DirectChatContext(conversation=self.conversation)

    @typing.override
    def reply_addressee(self, *, typed: bool) -> str | None:
        return None


@dataclasses.dataclass(frozen=True)
class SlackChannelChat(SlackChat):
    """A channel or multi-person DM the bot was added to, where each thread is shared by everyone in it."""

    reply_trigger: ReplyTrigger

    @property
    @typing.override
    def conversation(self) -> str:
        scope = make_principal(ChatSurface.slack, self.team_id, 'group', self.channel)
        return f'{scope}:{self.thread_ts}' if self.thread_ts else scope

    @typing.override
    def answers(self, event: dict[str, typing.Any]) -> bool:
        # With mention only app_mention counts. With every_message every message does, and app_mention is skipped
        # because the same message also arrives as a plain message event.
        if self.reply_trigger == 'mention':
            return event.get('type') == 'app_mention'
        return event.get('type') == 'message'

    @typing.override
    def turn_input(self, event: dict[str, typing.Any], *, bot_user_id: str | None) -> str:
        text = event.get('text', '')
        if not bot_user_id:
            return text
        mentions = re.finditer(rf'<@{re.escape(bot_user_id)}(?:\|[^>]*)?>', text)
        return without_spans(text, (match.span() for match in mentions))

    @typing.override
    def exclusive_pick_key(self, message_ts: str) -> str:
        # Each member picks for themselves, so one member's tap never swallows another's,
        # such as a passer-by tapping the confirmation another member is waiting on.
        return f'slack:interaction:{self.channel}:{message_ts}:{self.user_id}'

    @typing.override
    def resolve_chat_context(self, display_name: str | None) -> ChatContext:
        return GroupChatContext(conversation=self.conversation, speaker=display_name or self.user_id)

    @typing.override
    def reply_addressee(self, *, typed: bool) -> str | None:
        # A button tap has no message of its own to answer.
        return self.user_id if typed else None


def slack_chat_from_event(
    team_id: str, event: dict[str, typing.Any], group_chat_policy: GroupChatPolicy
) -> SlackChat | None:
    """The chat a message event came from, or None when it is not a fresh human message the bot answers there."""
    user_id = event.get('user')
    channel = event.get('channel')
    # Bot echoes and edits are dropped.
    if event.get('bot_id') or event.get('subtype') or not user_id or not channel:
        return None
    thread_ts = event.get('thread_ts') or event.get('ts')
    if event.get('channel_type') == 'im':
        return SlackDirectChat(team_id, user_id, channel, thread_ts)
    is_channel_event = event.get('type') == 'app_mention' or event.get('channel_type') in _CHANNEL_TYPES
    if not (is_channel_event and group_chat_policy.enabled):
        return None
    return SlackChannelChat(team_id, user_id, channel, thread_ts, reply_trigger=group_chat_policy.reply_trigger)


def slack_chat_from_interaction(payload: dict[str, typing.Any], group_chat_policy: GroupChatPolicy) -> SlackChat | None:
    """The chat a button click came from, or None when the bot does not answer there."""
    team_id = (payload.get('team') or {}).get('id', '')
    user_id = (payload.get('user') or {}).get('id', '')
    channel = (payload.get('channel') or {}).get('id', '')
    message = payload.get('message') or {}
    thread_ts = message.get('thread_ts') or message.get('ts')
    # Direct-message channel ids start with D, any other channel is one the bot was added to.
    if channel.startswith('D'):
        return SlackDirectChat(team_id, user_id, channel, thread_ts)
    if not group_chat_policy.enabled:
        return None
    return SlackChannelChat(team_id, user_id, channel, thread_ts, reply_trigger=group_chat_policy.reply_trigger)
