import abc
import dataclasses
import re
import typing

from conciergent.group_chat import GroupChatPolicy, GroupChatTurn, ReplyTrigger, without_spans
from conciergent.identity import ChatSurface, make_principal


@dataclasses.dataclass(frozen=True)
class DiscordChat(abc.ABC):
    """The Discord chat a message or click came from, which decides how the bot reads it and replies there.

    A direct message and a server channel differ in what counts as talking to the bot, how a reply points
    at the message it answers, and whether the turn is shared, so each kind answers those questions itself.
    """

    user_id: str
    channel_id: str

    @property
    def principal(self) -> str:
        return make_principal(ChatSurface.discord, self.user_id)

    @abc.abstractmethod
    def turn_input(self, message: dict[str, typing.Any], *, bot_user_id: str | None) -> str:
        """The part of a typed message meant for the bot, or empty when it is not talking to the bot."""

    @abc.abstractmethod
    def reply_reference(self, message_id: str | None) -> str | None:
        """The message a text reply answers as a native reply, or None to post a plain message."""

    @abc.abstractmethod
    def group_chat_turn(self) -> GroupChatTurn | None:
        """The shared group-chat turn this runs as, or None for a direct turn."""


@dataclasses.dataclass(frozen=True)
class DiscordDirectChat(DiscordChat):
    """A direct message with the bot, one conversation per user, where every message is for the bot."""

    @typing.override
    def turn_input(self, message: dict[str, typing.Any], *, bot_user_id: str | None) -> str:
        return message.get('content') or ''

    @typing.override
    def reply_reference(self, message_id: str | None) -> str | None:
        return None

    @typing.override
    def group_chat_turn(self) -> GroupChatTurn | None:
        return None


@dataclasses.dataclass(frozen=True)
class DiscordServerChannel(DiscordChat):
    """A server channel or thread, shared by everyone in it, where a reply points at the message it answers."""

    # The name the speaker shows in this server.
    speaker: str
    reply_trigger: ReplyTrigger

    @typing.override
    def turn_input(self, message: dict[str, typing.Any], *, bot_user_id: str | None) -> str:
        content = message.get('content') or ''
        if self.reply_trigger == 'every_message':
            return content
        # Only a message that mentions the bot is for it, and the mention itself is removed.
        if bot_user_id is None or not any(user.get('id') == bot_user_id for user in message.get('mentions') or []):
            return ''
        mentions = re.finditer(rf'<@!?{re.escape(bot_user_id)}>', content)
        return without_spans(content, (match.span() for match in mentions))

    @typing.override
    def reply_reference(self, message_id: str | None) -> str | None:
        return message_id

    @typing.override
    def group_chat_turn(self) -> GroupChatTurn | None:
        return GroupChatTurn(
            conversation=make_principal(ChatSurface.discord, 'group', self.channel_id), speaker=self.speaker
        )


def discord_chat_from_message(message: dict[str, typing.Any], group_chat_policy: GroupChatPolicy) -> DiscordChat | None:
    """The chat a message came from, or None when it is not a human message the bot answers there."""
    author = message.get('author') or {}
    user_id = author.get('id')
    channel_id = message.get('channel_id')
    # Bot echoes are dropped.
    if author.get('bot') or not user_id or not channel_id:
        return None
    return _chat(user_id, channel_id, message, author, group_chat_policy)


def discord_chat_from_interaction(
    interaction: dict[str, typing.Any], group_chat_policy: GroupChatPolicy
) -> DiscordChat | None:
    """The chat a button click came from, or None when the bot does not answer there."""
    member = interaction.get('member')
    # A click in a direct message names the user directly, one in a server names them through the member.
    user = interaction.get('user') or (member or {}).get('user') or {}
    user_id = user.get('id')
    channel_id = interaction.get('channel_id')
    if not user_id or not channel_id:
        return None
    return _chat(user_id, channel_id, interaction, user, group_chat_policy)


def _chat(
    user_id: str,
    channel_id: str,
    data: dict[str, typing.Any],
    user: dict[str, typing.Any],
    group_chat_policy: GroupChatPolicy,
) -> DiscordChat | None:
    # Only a server message or click carries a guild_id, so its absence marks a direct message.
    if not data.get('guild_id'):
        return DiscordDirectChat(user_id, channel_id)
    if not group_chat_policy.enabled:
        return None
    return DiscordServerChannel(
        user_id,
        channel_id,
        speaker=_display_name(user, data.get('member')),
        reply_trigger=group_chat_policy.reply_trigger,
    )


def _display_name(user: dict[str, typing.Any], member: dict[str, typing.Any] | None) -> str:
    # The name the speaker shows in this server, their server nickname before their global display name.
    return (member or {}).get('nick') or user.get('global_name') or user.get('username') or user.get('id', '')
