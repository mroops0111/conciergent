import abc
import re
import typing

import pydantic

from conciergent.i18n.lang import Lang


# A bullet marker the model may write at the start of an item.
_BULLET_MARKER = re.compile(r'^\s*(?:[•·▸]\s*|[-*]\s+)')


class Fact(pydantic.BaseModel):
    """A label paired with its value, such as a count or an amount, rendered so the values line up."""

    label: typing.Annotated[
        str,
        pydantic.Field(
            min_length=1,
            max_length=20,
            description='What the value measures, at most 20 characters, e.g. "Orders" or "Revenue".',
        ),
    ]
    value: typing.Annotated[
        str,
        pydantic.Field(
            min_length=1,
            max_length=30,
            description='The value alone, at most 30 characters, e.g. "42" or "$12,300". Do not repeat the label.',
        ),
    ]


class Section(pydantic.BaseModel):
    """A block of a card's body, with an optional title, text, and either a bulleted list or a set of facts."""

    title: typing.Annotated[
        str | None,
        pydantic.Field(
            max_length=40,
            description=(
                'Optional short heading for the section, at most 40 characters, e.g. "Totals". '
                'Use it when a card has several sections that each cover a different topic.'
            ),
        ),
    ] = None
    text: typing.Annotated[
        str | None,
        pydantic.Field(
            min_length=1,
            max_length=100,
            description='One paragraph of plain text, at most 100 characters. No markdown.',
        ),
    ] = None
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
    facts: list[Fact] = pydantic.Field(
        default_factory=list,
        max_length=10,
        description=(
            'Label and value pairs, such as counts and amounts, rendered so the values line up. '
            'Use instead of bullets when every item is a figure with a label.'
        ),
    )

    @pydantic.field_validator('bullets')
    @classmethod
    def _strip_bullet_markers(cls, bullets: list[str]) -> list[str]:
        # Surfaces add their own marker, so one the model wrote anyway is dropped.
        # A dash or an asterisk counts only before a space, so a value like "-5%" keeps its sign.
        return [_BULLET_MARKER.sub('', item).strip() for item in bullets]

    @pydantic.model_validator(mode='after')
    def _require_content(self) -> typing.Self:
        if not self.text and not self.bullets and not self.facts:
            raise ValueError('a section must have `text`, `bullets`, or `facts`')
        if self.bullets and self.facts:
            raise ValueError('a section has either `bullets` or `facts`, not both')
        return self


class Link(pydantic.BaseModel):
    """A labelled hyperlink, rendered as a link or a button depending on the surface."""

    label: typing.Annotated[
        str,
        pydantic.Field(
            min_length=1,
            max_length=40,
            description='Button label, at most 40 characters. A short verb-led phrase such as "Open Task".',
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
            max_length=20,
            description=(
                'Button label the user sees, at most 20 characters. '
                'Name the specific entity so the user knows which one it refers to, but fit within 20 by dropping filler words, '
                'e.g. prefer "NDA-Acme details" over "Show the details of NDA-Acme".'
            ),
        ),
    ]
    prompt: typing.Annotated[
        str,
        pydantic.Field(
            min_length=1,
            max_length=40,
            description=(
                'Text posted to the agent as if the user typed it when the button is tapped, at most 40 characters. '
                'Phrase it as a natural follow-up question in the user\'s language, e.g. "Show details of NDA-Acme".'
            ),
        ),
    ]


class Card(pydantic.BaseModel):
    """A rich reply with a header and body sections.

    It may also carry a subtitle, a hero image, links, and suggestions.
    """

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
    subtitle: typing.Annotated[
        str | None,
        pydantic.Field(
            max_length=60,
            description=(
                'Optional one line under the header, at most 60 characters, '
                'giving the period or scope the card covers, e.g. "Yesterday, all stores".'
            ),
        ),
    ] = None
    hero_image_url: typing.Annotated[
        str | None,
        pydantic.Field(
            description=(
                'Optional hero image rendered above the header. '
                'Use for a single-entity card that has a thumbnail (a task detail, a template, a seal, etc.), '
                'or for an image a tool made for this answer, using the URL exactly as the tool returned it. '
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


# The terminal output of a turn.
Reply = str | Card | Carousel


class ReplySurface(abc.ABC):
    """A chat surface able to render the reply model in its own native format.

    Implementations own all platform-specific knowledge, so the runtime speaks only in terms of this interface.
    Custom presentation is achieved by overriding a surface, never by changing the agent.
    """

    @property
    def text_formatting_instruction(self) -> str:
        """Return surface-specific formatting guidance to inject into the agent's system prompt.

        For example, the markdown dialect the surface understands.
        It is empty by default.
        """
        return ''

    @property
    def lang(self) -> Lang | None:
        """The user's resolved UI language for this conversation, or None when the surface cannot tell.

        The agent renders localized text and asks the model to reply in this language.
        None falls back to English and to the language of the user's own message.
        """
        return None

    @property
    def brand_color(self) -> str | None:
        """The color the surface draws its cards in, for anything outside the surface that should match them.

        None when the surface has no brand color, which leaves such things in their own default colors.
        """
        return None

    @abc.abstractmethod
    async def send_text(self, text: str) -> None: ...

    @abc.abstractmethod
    async def send_card(self, card: Card, *, destructive: bool = False) -> None: ...

    @abc.abstractmethod
    async def send_carousel(self, cards: list[Card]) -> None: ...

    async def acknowledge_silently(self) -> None:
        """Settle the event behind a turn that is dropped without a reply, showing nothing to anyone.

        Most platforms need nothing, while one that requires every button click to be answered overrides this.
        """
        return None

    @abc.abstractmethod
    async def show_processing(self) -> None:
        """Signal to the user that work is in progress, for example a typing or loading indicator."""
