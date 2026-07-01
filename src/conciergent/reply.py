"""Surface-agnostic reply model.

An agent does not emit Slack Block Kit or LINE Flex directly. It emits these
semantic structures, and each chat surface renders them into its own native
format. This is the single abstraction that lets one agent serve every surface;
custom layouts are achieved by overriding a surface renderer, never by changing
the agent.

The field descriptions read as instructions to the language model: these models
double as the agent's structured-output schema.
"""

from __future__ import annotations

import abc

from pydantic import BaseModel, Field


class Link(BaseModel):
    """A labelled hyperlink, rendered as a link or a button depending on the surface."""

    text: str = Field(description='The visible label for the link.')
    url: str = Field(description='The destination URL. Must be an absolute http(s) URL.')


class Suggestion(BaseModel):
    """A tappable suggestion (quick reply). Selecting it sends ``prompt`` back to the
    agent as if the user had typed it."""

    label: str = Field(description='Short button text shown to the user.')
    prompt: str = Field(description='The message sent back to the agent when the user taps this suggestion.')
    exclusive: bool = Field(
        default=False,
        description=(
            'If true, selecting this suggestion consumes the whole message '
            '(e.g. a pick-one choice); the other suggestions on the message stop accepting input.'
        ),
    )


class Section(BaseModel):
    """A block of text within a card."""

    text: str = Field(description='Body text for this section. Keep it concise; one idea per section.')
    heading: str | None = Field(default=None, description='Optional bold heading shown above the text.')


class Card(BaseModel):
    """A rich, structured reply: an optional title, one or more text sections, and
    optional links and suggestions."""

    title: str | None = Field(default=None, description='Optional card title.')
    sections: list[Section] = Field(default_factory=list, description='Ordered content blocks.')
    links: list[Link] = Field(default_factory=list, description='Optional link or button actions.')
    suggestions: list[Suggestion] = Field(
        default_factory=list, description='Optional quick replies offered to the user.'
    )
    footnote: str | None = Field(default=None, description='Optional small print shown at the bottom.')


class Carousel(BaseModel):
    """A horizontally scrollable set of cards, used to present several comparable options."""

    cards: list[Card] = Field(description='The cards to show, in order. Provide at least two.')


Reply = str | Card | Carousel
"""The terminal output of a turn: plain text, a single card, or a carousel of cards."""


class ReplySurface(abc.ABC):
    """A chat surface (Slack, LINE, ...) able to render the reply model natively.

    Implementations own all platform-specific knowledge; the runtime speaks only
    in terms of this interface.
    """

    @property
    def text_formatting_instruction(self) -> str:
        """Surface-specific formatting guidance injected into the agent's system prompt
        (for example, which markdown dialect the surface understands). Empty by default."""
        return ''

    @abc.abstractmethod
    async def send_text(self, text: str) -> None: ...

    @abc.abstractmethod
    async def send_card(self, card: Card, *, destructive: bool = False) -> None: ...

    @abc.abstractmethod
    async def send_carousel(self, cards: list[Card]) -> None: ...

    @abc.abstractmethod
    async def show_processing(self) -> None:
        """Signal to the user that work is in progress (a typing or loading indicator)."""
