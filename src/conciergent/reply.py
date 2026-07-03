import abc
import typing

import pydantic

from conciergent.i18n.lang import Lang


class Section(pydantic.BaseModel):
    """A block of body text within a card, with an optional bulleted list."""

    text: typing.Annotated[
        str,
        pydantic.Field(
            min_length=1,
            max_length=100,
            description='One paragraph of plain text, at most 100 characters. No markdown.',
        ),
    ]
    bullets: list[str] = pydantic.Field(
        default_factory=list,
        max_length=10,
        description=(
            'A list of items. Each item should be a single short statement. '
            'Provide only the item content. '
            'The renderer adds the bullet marker. '
            'Do not prefix items with `•`, `-`, `*`, or any other bullet character.'
        ),
    )

    @pydantic.model_validator(mode='after')
    def _require_text_or_bullets(self) -> typing.Self:
        if not self.text and not self.bullets:
            raise ValueError('Section must have `text`, and optionally `bullets`.')
        return self


class Link(pydantic.BaseModel):
    """A labelled hyperlink, rendered as a link or a button depending on the surface."""

    label: typing.Annotated[
        str,
        pydantic.Field(
            min_length=1,
            max_length=50,
            description='Button label, at most 50 characters. A short verb-led phrase such as "Open Task".',
        ),
    ]
    url: typing.Annotated[
        str,
        pydantic.Field(description='Target URL the user reaches when activating the button.'),
    ]


class Suggestion(pydantic.BaseModel):
    """A tappable suggestion.

    Selecting it sends its prompt back to the agent as if the user had typed it.
    """

    label: typing.Annotated[
        str,
        pydantic.Field(
            min_length=1,
            max_length=50,
            description=(
                'Button label that the user sees, at most 50 characters, e.g. "Show details" or "List more". '
                'Be precise if the suggestion is about a specific entity (e.g. "Show details of NDA-Acme").'
            ),
        ),
    ]
    prompt: typing.Annotated[
        str,
        pydantic.Field(
            min_length=1,
            max_length=100,
            description=(
                'Text posted to the agent as if the user typed it when the button is tapped, at most 100 characters. '
                'Phrase it as a natural follow-up question in the user\'s language, e.g. "Show details of NDA-Acme".'
            ),
        ),
    ]


class Card(pydantic.BaseModel):
    """A rich reply with a header, text sections, an optional hero image, and optional links and suggestions."""

    header: typing.Annotated[
        str,
        pydantic.Field(
            min_length=1,
            max_length=40,
            description=(
                'Concise one-line title, at most 40 characters. '
                'For a single card, a category label like "Tasks" or "Task Status". '
                'For a carousel option, the entity name (e.g. a template or task name). '
                'Keep it a short label or name, never a sentence or description.'
            ),
        ),
    ]
    hero_image_url: typing.Annotated[
        str | None,
        pydantic.Field(
            description=(
                'Optional hero image rendered above the header. '
                'Use for a single-entity card that has a thumbnail (a task detail, a template, a seal, etc.). '
                'Skip for list cards.'
            ),
        ),
    ] = None
    sections: list[Section] = pydantic.Field(
        min_length=1,
        max_length=6,
        description='Body content. Rendered top to bottom.',
    )
    footnote: typing.Annotated[
        str | None,
        pydantic.Field(
            max_length=100,
            description=(
                'Optional small note rendered under the body for caveats or context, at most 100 characters. '
                'Example: "Active templates only" or "Last refreshed 5 minutes ago". '
                'Keep it concise.'
            ),
        ),
    ] = None
    links: list[Link] = pydantic.Field(
        default_factory=list,
        max_length=5,
        description=(
            'Optional URL buttons rendered after the body. '
            'The first link is emphasized as the primary action; put the most likely next action first.'
        ),
    )
    suggestions: list[Suggestion] = pydantic.Field(
        default_factory=list,
        max_length=3,
        description=(
            'Optional quick-reply buttons. '
            "Tapping one posts the suggestion's prompt back to the agent as a user message. "
            'Use to nudge a likely follow-up question.'
        ),
    )


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
