import abc
import dataclasses
import typing

from conciergent.chat_context import ChatContext, DirectChatContext, GroupChatContext
from conciergent.group_chat import GroupChatPolicy, ReplyTrigger, without_spans
from conciergent.identity import ChatSurface, make_principal
from conciergent.surfaces.line import render
from conciergent.surfaces.line.surface import LineMessenger


@dataclasses.dataclass(frozen=True)
class LineChat(abc.ABC):
    """The chat a LINE event came from, which decides how the bot reads the event and replies there.

    A one-on-one chat and a group chat differ in where replies go, what counts as talking to the bot,
    and whether the turn is shared, so each kind answers those questions itself.
    """

    user_id: str

    @property
    @abc.abstractmethod
    def push_target(self) -> str:
        """The id a push message goes to once the reply token is spent."""

    @property
    @abc.abstractmethod
    def shows_loading(self) -> bool:
        """Whether LINE's loading animation can run here, which it only does in one-on-one chats."""

    def turn_input(self, event: dict[str, typing.Any]) -> str:
        """Return the text a message or suggestion tap feeds the agent, or empty when the event starts no turn.

        A suggestion tap is always addressed to the bot, so only a typed message is checked by ``_typed_input``.
        """
        if event.get('type') == 'postback':
            return render.parse_suggestion_postback((event.get('postback') or {}).get('data', '')) or ''
        if event.get('type') != 'message':
            return ''
        message = event.get('message') or {}
        text = message.get('text', '')
        if message.get('type') != 'text' or not text:
            return ''
        return self._typed_input(message, text)

    @abc.abstractmethod
    def _typed_input(self, message: dict[str, typing.Any], text: str) -> str:
        """The part of a typed text message meant for the bot, or empty when it is not talking to the bot."""

    @abc.abstractmethod
    def quote_token(self, event: dict[str, typing.Any]) -> str | None:
        """The quote token a text reply carries to show which message it answers, or None to quote nothing."""

    @abc.abstractmethod
    async def resolve_chat_context(self, messenger: LineMessenger) -> ChatContext:
        """The chat context this event runs in, looking up the speaker's name where the chat needs it."""


@dataclasses.dataclass(frozen=True)
class LineDirectChat(LineChat):
    """A one-on-one chat, where every message is for the bot and replies go back to the user."""

    @property
    @typing.override
    def push_target(self) -> str:
        return self.user_id

    @property
    @typing.override
    def shows_loading(self) -> bool:
        return True

    @typing.override
    def _typed_input(self, message: dict[str, typing.Any], text: str) -> str:
        return text

    @typing.override
    def quote_token(self, event: dict[str, typing.Any]) -> str | None:
        return None

    @typing.override
    async def resolve_chat_context(self, messenger: LineMessenger) -> ChatContext:
        return DirectChatContext(conversation=make_principal(ChatSurface.line, self.user_id))


@dataclasses.dataclass(frozen=True)
class LineGroupChat(LineChat):
    """A group, or a legacy multi-person room, shared by its members, where replies go to the whole chat."""

    kind: typing.Literal['group', 'room']
    chat_id: str
    reply_trigger: ReplyTrigger

    @property
    @typing.override
    def push_target(self) -> str:
        return self.chat_id

    @property
    @typing.override
    def shows_loading(self) -> bool:
        return False

    @typing.override
    def _typed_input(self, message: dict[str, typing.Any], text: str) -> str:
        if self.reply_trigger == 'every_message':
            return text
        # Only a message that mentions the bot is for it, and the mention itself is removed.
        mentionees = (message.get('mention') or {}).get('mentionees') or []
        spans = [
            _code_point_span(text, mentionee.get('index', 0), mentionee.get('length', 0))
            for mentionee in mentionees
            if mentionee.get('isSelf')
        ]
        return without_spans(text, spans) if spans else ''

    @typing.override
    def quote_token(self, event: dict[str, typing.Any]) -> str | None:
        # A typed message carries a quote token, a postback tap has no message of its own to quote.
        return (event.get('message') or {}).get('quoteToken')

    @typing.override
    async def resolve_chat_context(self, messenger: LineMessenger) -> ChatContext:
        speaker = await messenger.get_member_display_name(self.kind, self.chat_id, self.user_id)
        return GroupChatContext(
            conversation=make_principal(ChatSurface.line, self.kind, self.chat_id),
            # A member whose profile cannot be read still needs a stable label, so members stay distinct.
            speaker=speaker or f'user-{self.user_id[-4:]}',
        )


def line_chat_from_source(source: dict[str, typing.Any], group_chat_policy: GroupChatPolicy) -> LineChat | None:
    """The chat an event's source names, or None when the bot does not answer there."""
    user_id = source.get('userId')
    if not user_id:
        return None
    source_type = source.get('type')
    if source_type == 'user':
        return LineDirectChat(user_id)
    if not group_chat_policy.enabled:
        return None
    reply_trigger = group_chat_policy.reply_trigger
    if source_type == 'group' and source.get('groupId'):
        return LineGroupChat(user_id, kind='group', chat_id=source['groupId'], reply_trigger=reply_trigger)
    if source_type == 'room' and source.get('roomId'):
        return LineGroupChat(user_id, kind='room', chat_id=source['roomId'], reply_trigger=reply_trigger)
    return None


def _code_point_span(text: str, index: int, length: int) -> tuple[int, int]:
    # LINE counts mention offsets in UTF-16 code units, so an emoji before the mention shifts the Python index.
    return _code_point_offset(text, index), _code_point_offset(text, index + length)


def _code_point_offset(text: str, utf16_offset: int) -> int:
    units = 0
    for position, character in enumerate(text):
        if units >= utf16_offset:
            return position
        units += 2 if ord(character) > 0xFFFF else 1
    return len(text)
