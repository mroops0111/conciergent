import collections.abc
import re
import typing


# What makes the bot answer in a group chat: a message that mentions it, or every message.
ReplyTrigger = typing.Literal['mention', 'every_message']


class GroupChatPolicy(typing.NamedTuple):
    """The operator's choice of whether a surface answers in group chats, and which of their messages it answers.

    This is separate from whether the app can serve group chats at all, which ``ChatRunner.supports_group_chats``
    decides from the MCP servers. Once enabled, the bot answers in any group chat it is added to, the same openness
    as its direct messages.
    """

    enabled: bool = False
    reply_trigger: ReplyTrigger = 'mention'


class GroupChatTurn(typing.NamedTuple):
    """One message in a group chat, the conversation its members share and the member who sent it.

    Passing one to ``run_turn`` is what makes a turn a group turn. The runtime then checks the app can serve
    group chats, keeps the speaker's approvals their own, and reaches every MCP server without a user's token.
    """

    conversation: str
    speaker: str


def speaker_prompt(speaker: str | None, text: str) -> str:
    """Prefix a group message with its speaker's name, so the agent can tell members apart in a shared history."""
    return f'[{speaker}] {text}' if speaker else text


def without_spans(text: str, spans: collections.abc.Iterable[tuple[int, int]]) -> str:
    """Remove ``(start, end)`` code-point spans from ``text``, such as the mentions of the bot, and tidy the spacing."""
    for start, end in sorted(spans, reverse=True):
        text = text[:start] + text[end:]
    # Collapse the run of spaces a removed span leaves behind, keeping the message's own line breaks.
    return re.sub(r'[ \t]{2,}', ' ', text).strip()
