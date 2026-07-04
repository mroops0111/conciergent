import typing

from conciergent.defaults import DEFAULTS
from conciergent.reply import Card, Link, Suggestion


BRAND_COLOR = DEFAULTS.surface.line.brand_color
DESTRUCTIVE_COLOR = DEFAULTS.surface.line.destructive_color

# Mid-gray for bullet list items inside a card body, slightly darker than the body text for gentle hierarchy.
BULLET_TEXT_COLOR = '#555555'

# Light gray for the footnote line at the bottom of a card, subtle enough to recede behind the body.
FOOTNOTE_COLOR = '#888888'

# The card header is reused as the push-notification alt text, capped to keep the preview compact.
ALT_TEXT_MAX_LENGTH = 40

# Where a card's suggestions land in the final LINE message.
# * 'chip': quick-reply chips at message-envelope level (default for a normal reply card)
# * 'button': inline buttons in the bubble footer, link style (carousel options)
# * 'destructive_button': inline buttons in the bubble footer, primary red + secondary gray (HITL approval)
SuggestionPlacement = typing.Literal['chip', 'button', 'destructive_button']


def build_card_bubble(
    card: Card,
    *,
    suggestion_placement: SuggestionPlacement = 'chip',
    brand_color: str = BRAND_COLOR,
    destructive_color: str = DESTRUCTIVE_COLOR,
) -> dict[str, typing.Any]:
    """Render one card to a Flex bubble."""
    in_footer_suggestions = [] if suggestion_placement == 'chip' else card.suggestions
    has_footer = bool(card.links or in_footer_suggestions)
    bubble: dict[str, typing.Any] = {
        'type': 'bubble',
        'size': 'kilo',
        'header': _build_header(card.header, brand_color=brand_color),
        'body': _build_body(card, has_footer=has_footer),
    }
    if card.hero_image_url:
        bubble['hero'] = _build_hero(card.hero_image_url)
    if has_footer:
        bubble['footer'] = _build_footer(
            card.links,
            in_footer_suggestions,
            suggestions_destructive=(suggestion_placement == 'destructive_button'),
            brand_color=brand_color,
            destructive_color=destructive_color,
        )
    return bubble


def build_carousel(
    cards: list[Card], *, brand_color: str = BRAND_COLOR, destructive_color: str = DESTRUCTIVE_COLOR
) -> dict[str, typing.Any]:
    """Render carousel cards to a Flex carousel, suggestions as footer buttons per bubble."""
    return {
        'type': 'carousel',
        'contents': [
            build_card_bubble(
                card, suggestion_placement='button', brand_color=brand_color, destructive_color=destructive_color
            )
            for card in cards
        ],
    }


def build_quick_reply(suggestions: list[Suggestion]) -> list[dict[str, typing.Any]]:
    """Render suggestions as quick-reply chip actions for the message envelope."""
    return [
        {'type': 'action', 'action': {'type': 'message', 'label': suggestion.label, 'text': suggestion.prompt}}
        for suggestion in suggestions
    ]


def alt_text(card: Card) -> str:
    return card.header[:ALT_TEXT_MAX_LENGTH]


def _build_header(header: str, *, brand_color: str) -> dict[str, typing.Any]:
    return {
        'type': 'box',
        'layout': 'vertical',
        'paddingTop': 'xl',
        'paddingBottom': 'sm',
        'paddingStart': 'xl',
        'paddingEnd': 'xl',
        'contents': [{'type': 'text', 'text': header, 'size': 'xs', 'weight': 'bold', 'color': brand_color}],
    }


def _build_body(card: Card, *, has_footer: bool) -> dict[str, typing.Any]:
    contents: list[dict[str, typing.Any]] = []
    for section in card.sections:
        if section.text:
            contents.append({'type': 'text', 'text': section.text, 'size': 'md', 'wrap': True})
        if section.bullets:
            contents.append(
                {
                    'type': 'box',
                    'layout': 'vertical',
                    'spacing': 'sm',
                    'contents': [
                        {
                            'type': 'text',
                            'text': f'• {item.lstrip("•·▸-* ").strip()}',
                            'size': 'sm',
                            'wrap': True,
                            'color': BULLET_TEXT_COLOR,
                        }
                        for item in section.bullets
                    ],
                }
            )
    if card.footnote:
        contents.append({'type': 'separator', 'margin': 'md'})
        contents.append(
            {
                'type': 'text',
                'text': card.footnote,
                'size': 'xxs',
                'color': FOOTNOTE_COLOR,
                'wrap': True,
                'margin': 'sm',
            }
        )
    return {
        'type': 'box',
        'layout': 'vertical',
        'spacing': 'lg',
        'paddingTop': 'md',
        'paddingBottom': 'sm' if has_footer else 'xl',
        'paddingStart': 'xl',
        'paddingEnd': 'xl',
        'contents': contents,
    }


def _build_footer(
    links: list[Link],
    suggestions: list[Suggestion],
    *,
    suggestions_destructive: bool,
    brand_color: str,
    destructive_color: str,
) -> dict[str, typing.Any]:
    contents: list[dict[str, typing.Any]] = []
    for index, link in enumerate(links):
        button: dict[str, typing.Any] = {
            'type': 'button',
            'style': 'primary' if index == 0 else 'secondary',
            'height': 'sm',
            'action': {'type': 'uri', 'label': link.label, 'uri': link.url},
        }
        if index == 0:
            button['color'] = brand_color
        contents.append(button)
    for index, suggestion in enumerate(suggestions):
        button = {
            'type': 'button',
            'height': 'sm',
            'action': {'type': 'message', 'label': suggestion.label, 'text': suggestion.prompt},
        }
        if suggestions_destructive:
            button['style'] = 'primary' if index == 0 else 'secondary'
            if index == 0:
                button['color'] = destructive_color
        else:
            button['style'] = 'link'
        contents.append(button)
    return {
        'type': 'box',
        'layout': 'vertical',
        'spacing': 'sm',
        'flex': 0,
        'paddingTop': 'none',
        'paddingBottom': 'lg',
        'paddingStart': 'xl',
        'paddingEnd': 'xl',
        'contents': contents,
    }


def _build_hero(image_url: str) -> dict[str, typing.Any]:
    return {'type': 'image', 'url': image_url, 'size': 'full', 'aspectMode': 'cover', 'aspectRatio': '20:13'}
