import abc
import typing

import pydantic

from conciergent.lang import Lang


class Link(pydantic.BaseModel):
    """A labelled hyperlink, rendered as a link or a button depending on the surface."""

    text: typing.Annotated[
        str,
        pydantic.Field(
            min_length=1, max_length=50, description='The visible label for the link, at most 50 characters.'
        ),
    ]
    url: typing.Annotated[str, pydantic.Field(description='The destination URL, an absolute http(s) URL.')]


class Suggestion(pydantic.BaseModel):
    """A tappable suggestion.

    Selecting it sends its prompt back to the agent as if the user had typed it.
    """

    label: typing.Annotated[
        str,
        pydantic.Field(
            min_length=1, max_length=50, description='Short button text shown to the user, at most 50 characters.'
        ),
    ]
    prompt: typing.Annotated[
        str,
        pydantic.Field(
            min_length=1,
            max_length=100,
            description='The message sent back to the agent when the user taps this suggestion, at most 100 characters.',
        ),
    ]
    exclusive: typing.Annotated[
        bool,
        pydantic.Field(
            description=(
                'If true, selecting this suggestion consumes the whole message, for example a pick-one choice, '
                'and the other suggestions on the message stop accepting input.'
            ),
        ),
    ] = False


class Section(pydantic.BaseModel):
    """A block of text within a card."""

    text: typing.Annotated[
        str,
        pydantic.Field(
            min_length=1,
            max_length=100,
            description='Body text for this section, at most 100 characters. Keep it concise, one idea per section.',
        ),
    ]
    heading: typing.Annotated[
        str | None,
        pydantic.Field(max_length=40, description='Optional bold heading shown above the text, at most 40 characters.'),
    ] = None


class Card(pydantic.BaseModel):
    """A rich reply with an optional title, text sections, and optional links and suggestions."""

    title: typing.Annotated[
        str | None,
        pydantic.Field(max_length=40, description='Optional card title, a short label of at most 40 characters.'),
    ] = None
    sections: list[Section] = pydantic.Field(
        default_factory=list, max_length=6, description='Ordered content blocks, at most 6.'
    )
    links: list[Link] = pydantic.Field(
        default_factory=list, max_length=5, description='Optional link or button actions, at most 5.'
    )
    suggestions: list[Suggestion] = pydantic.Field(
        default_factory=list, max_length=3, description='Optional quick replies offered to the user, at most 3.'
    )
    footnote: typing.Annotated[
        str | None,
        pydantic.Field(max_length=100, description='Optional small print shown at the bottom, at most 100 characters.'),
    ] = None


class Carousel(pydantic.BaseModel):
    """A horizontally scrollable set of option cards, closed by a fallback for when none of them fit."""

    options: typing.Annotated[
        list[Card],
        pydantic.Field(
            min_length=1,
            max_length=4,
            description='One to four option cards the user picks between, each with a pickable suggestion or link.',
        ),
    ]
    fallback: typing.Annotated[
        Card,
        pydantic.Field(description='Escape-hatch card shown after the options for when none of them fit.'),
    ]


# The terminal output of a turn, either plain text, a single card, or a carousel of cards.
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

    @property
    def lang(self) -> Lang | None:
        """The user's resolved UI language for this conversation, or None when the surface cannot tell.

        The agent renders localized text and asks the model to reply in this language.
        None falls back to English and to the language of the user's own message.
        """
        return None

    @abc.abstractmethod
    async def send_text(self, text: str) -> None: ...

    @abc.abstractmethod
    async def send_card(self, card: Card, *, destructive: bool = False) -> None: ...

    @abc.abstractmethod
    async def send_carousel(self, cards: list[Card]) -> None: ...

    @abc.abstractmethod
    async def show_processing(self) -> None:
        """Signal to the user that work is in progress, for example a typing or loading indicator."""
