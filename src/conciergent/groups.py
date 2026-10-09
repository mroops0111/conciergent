import collections.abc
import re
import typing


ReplyTo = typing.Literal['mention', 'all']


class GroupPolicy(typing.NamedTuple):
    """Which group chats a surface serves, and which messages in them start a turn.

    A group turn runs with no per-user authorization, so the app only serves groups
    when none of its MCP servers needs one (see ``ChatRunner.supports_groups``).
    """

    enabled: bool = False
    # Platform ids of the group chats the bot answers in, such as a LINE groupId or a Slack channel id.
    allowed: frozenset[str] = frozenset()
    # ``mention`` answers only messages that mention the bot, ``all`` answers every message in an allowed group.
    reply_to: ReplyTo = 'mention'

    def admits(self, *chat_ids: str | None) -> bool:
        """Report whether any of the given ids names an allowed group, for example a channel or its server."""
        return self.enabled and any(chat_id in self.allowed for chat_id in chat_ids if chat_id)


class GroupTurn(typing.NamedTuple):
    """One message in a group chat, the conversation its members share and the member who sent it.

    Passing one to ``run_turn`` is what makes a turn a group turn. The runtime then checks the app can serve groups,
    keeps the speaker's approvals their own, and reaches every MCP server without a user's token.
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
