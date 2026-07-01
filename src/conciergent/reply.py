import abc
import typing

import pydantic


class Link(pydantic.BaseModel):
    """A labelled hyperlink, rendered as a link or a button depending on the surface."""

    text: typing.Annotated[str, pydantic.Field(description='The visible label for the link.')]
    url: typing.Annotated[str, pydantic.Field(description='The destination URL, an absolute http(s) URL.')]


class Suggestion(pydantic.BaseModel):
    """A tappable suggestion.

    Selecting it sends its prompt back to the agent as if the user had typed it.
    """

    label: typing.Annotated[str, pydantic.Field(description='Short button text shown to the user.')]
    prompt: typing.Annotated[
        str,
        pydantic.Field(description='The message sent back to the agent when the user taps this suggestion.'),
    ]
    exclusive: typing.Annotated[
        bool,
        pydantic.Field(
            description=(
                'If true, selecting this suggestion consumes the whole message, for example a pick-one '
                'choice, and the other suggestions on the message stop accepting input.'
            ),
        ),
    ] = False


class Section(pydantic.BaseModel):
    """A block of text within a card."""

    text: typing.Annotated[
        str,
        pydantic.Field(description='Body text for this section. Keep it concise, one idea per section.'),
    ]
    heading: typing.Annotated[
        str | None,
        pydantic.Field(description='Optional bold heading shown above the text.'),
    ] = None


class Card(pydantic.BaseModel):
    """A rich reply with an optional title, text sections, and optional links and suggestions."""

    title: typing.Annotated[str | None, pydantic.Field(description='Optional card title.')] = None
    sections: list[Section] = pydantic.Field(default_factory=list, description='Ordered content blocks.')
    links: list[Link] = pydantic.Field(default_factory=list, description='Optional link or button actions.')
    suggestions: list[Suggestion] = pydantic.Field(
        default_factory=list, description='Optional quick replies offered to the user.'
    )
    footnote: typing.Annotated[
        str | None,
        pydantic.Field(description='Optional small print shown at the bottom.'),
    ] = None


class Carousel(pydantic.BaseModel):
    """A horizontally scrollable set of cards for presenting several comparable options."""

    cards: typing.Annotated[
        list[Card],
        pydantic.Field(description='The cards to show, in order. Provide at least two.'),
    ]


# The terminal output of a turn: plain text, a single card, or a carousel of cards.
Reply = str | Card | Carousel


class ReplySurface(abc.ABC):
    """A chat surface able to render the reply model in its own native format.

    Implementations own all platform-specific knowledge, so the runtime speaks only in terms of this interface.
    Custom presentation is achieved by overriding a surface, never by changing the agent.
    """

    @property
    def text_formatting_instruction(self) -> str:
        """Return surface-specific formatting guidance to inject into the agent's system prompt.

        For example, the markdown dialect the surface understands; empty by default.
        """
        return ''

    @abc.abstractmethod
    async def send_text(self, text: str) -> None: ...

    @abc.abstractmethod
    async def send_card(self, card: Card, *, destructive: bool = False) -> None: ...

    @abc.abstractmethod
    async def send_carousel(self, cards: list[Card]) -> None: ...

    @abc.abstractmethod
    async def show_processing(self) -> None:
        """Signal to the user that work is in progress, for example a typing or loading indicator."""
