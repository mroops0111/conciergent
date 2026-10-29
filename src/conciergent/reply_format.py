import abc
import typing

from pydantic_ai import ToolOutput

from conciergent.reply import Card, Carousel, Reply


_FREE_INSTRUCTIONS = (
    'End each turn with exactly one reply, either plain text, a single reply_card, or a single reply_carousel, never a mix. '
    'Use plain text only for a short one-line answer with no list, entity, link, or follow-up. '
    'Use reply_card for anything richer, a synthesized answer, a status report, a single entity, or a plain list, '
    'and keep the whole message inside the card rather than writing text beside it. '
    'Use reply_carousel for a small set of distinct items that each deserve their own card and action, '
    'giving every option a suggestion or link so it can be chosen. '
    'Put a URL in a card link button instead of writing it inline, and offer next steps as suggestions.'
)


class ReplyFormat(abc.ABC):
    """The shape a turn's reply takes, the outputs the agent may end the turn with and how each becomes a reply.

    The chat context supplies one per turn, so the runner never branches on how a reply is shaped.
    """

    @property
    @abc.abstractmethod
    def outputs(self) -> list[typing.Any]:
        """The output types the agent may end the turn with, as Pydantic AI output specs."""

    @property
    @abc.abstractmethod
    def instructions(self) -> str:
        """The system instructions telling the agent how to end the turn."""

    @abc.abstractmethod
    def to_reply(self, output: typing.Any) -> Reply:
        """Turn the agent's final output, one of ``outputs``, into the reply the surface sends."""


class FreeReplyFormat(ReplyFormat):
    """The agent picks plain text, a card, or a carousel, and lays out a card itself."""

    @property
    @typing.override
    def outputs(self) -> list[typing.Any]:
        return [str, ToolOutput(Card, name='reply_card'), ToolOutput(Carousel, name='reply_carousel')]

    @property
    @typing.override
    def instructions(self) -> str:
        return _FREE_INSTRUCTIONS

    @typing.override
    def to_reply(self, output: typing.Any) -> Reply:
        return output
