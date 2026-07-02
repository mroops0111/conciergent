import typing

from ...reply import Card, Link, Section, Suggestion


BRAND_COLOR = '#586af2'
DESTRUCTIVE_COLOR = '#DC3545'
_MUTED_COLOR = '#888888'

# LINE caps a message's alt text at 400, kept at 40 to match the card title budget.
_ALT_TEXT_MAX = 40

# LINE caps quick-reply action labels at 20 characters and button action labels at 40,
# both below the reply model's 50-character label budget.
_CHIP_LABEL_MAX = 20
_BUTTON_LABEL_MAX = 40

# Where a card's suggestions land.
# Chips ride the message envelope as quick replies and vanish after the next message,
# buttons live in the bubble footer (a carousel bubble cannot carry chips),
# and the destructive button is the emphasized HITL confirm.
SuggestionPlacement = typing.Literal['chip', 'button', 'destructive_button']


def build_card_bubble(card: Card, *, suggestion_placement: SuggestionPlacement = 'chip') -> dict[str, typing.Any]:
    """Render one card to a Flex bubble."""
    bubble: dict[str, typing.Any] = {'type': 'bubble', 'size': 'kilo'}
    if card.title:
        bubble['header'] = {
            'type': 'box',
            'layout': 'vertical',
            'contents': [{'type': 'text', 'text': card.title, 'size': 'xs', 'weight': 'bold', 'color': BRAND_COLOR}],
        }
    bubble['body'] = _build_body(card, footer_follows=bool(card.links or suggestion_placement != 'chip'))
    footer = _build_footer(card, suggestion_placement=suggestion_placement)
    if footer is not None:
        bubble['footer'] = footer
    return bubble


def build_carousel(cards: list[Card]) -> dict[str, typing.Any]:
    """Render carousel cards to a Flex carousel, suggestions as footer buttons per bubble."""
    return {
        'type': 'carousel',
        'contents': [build_card_bubble(card, suggestion_placement='button') for card in cards],
    }


def build_quick_reply(suggestions: list[Suggestion]) -> dict[str, typing.Any] | None:
    """Render suggestions as quick-reply chips for the message envelope."""
    if not suggestions:
        return None
    return {
        'items': [
            {
                'type': 'action',
                'action': {'type': 'message', 'label': item.label[:_CHIP_LABEL_MAX], 'text': item.prompt},
            }
            for item in suggestions
        ]
    }


def alt_text(card: Card, fallback: str = 'Message') -> str:
    return (card.title or fallback)[:_ALT_TEXT_MAX]


def _build_body(card: Card, *, footer_follows: bool) -> dict[str, typing.Any]:
    contents: list[dict[str, typing.Any]] = []
    for section in card.sections:
        contents.extend(_section_nodes(section))
    if card.footnote:
        contents.append({'type': 'separator', 'margin': 'md'})
        contents.append({'type': 'text', 'text': card.footnote, 'size': 'xxs', 'color': _MUTED_COLOR, 'wrap': True})
    return {
        'type': 'box',
        'layout': 'vertical',
        'spacing': 'lg',
        'paddingBottom': 'sm' if footer_follows else 'xl',
        'contents': contents,
    }


def _section_nodes(section: Section) -> list[dict[str, typing.Any]]:
    nodes: list[dict[str, typing.Any]] = []
    if section.heading:
        nodes.append({'type': 'text', 'text': section.heading, 'size': 'sm', 'weight': 'bold', 'wrap': True})
    nodes.append({'type': 'text', 'text': section.text, 'size': 'md', 'wrap': True})
    return nodes


def _build_footer(card: Card, *, suggestion_placement: SuggestionPlacement) -> dict[str, typing.Any] | None:
    buttons: list[dict[str, typing.Any]] = []
    for index, link in enumerate(card.links):
        buttons.append(_link_button(link, primary=index == 0))
    if suggestion_placement != 'chip':
        destructive = suggestion_placement == 'destructive_button'
        for index, suggestion in enumerate(card.suggestions):
            buttons.append(_suggestion_button(suggestion, emphasized=destructive and index == 0))
    if not buttons:
        return None
    return {'type': 'box', 'layout': 'vertical', 'spacing': 'sm', 'contents': buttons}


def _link_button(link: Link, *, primary: bool) -> dict[str, typing.Any]:
    button: dict[str, typing.Any] = {
        'type': 'button',
        'height': 'sm',
        'style': 'primary' if primary else 'secondary',
        'action': {'type': 'uri', 'label': link.text[:_BUTTON_LABEL_MAX], 'uri': link.url},
    }
    if primary:
        button['color'] = BRAND_COLOR
    return button


def _suggestion_button(suggestion: Suggestion, *, emphasized: bool) -> dict[str, typing.Any]:
    button: dict[str, typing.Any] = {
        'type': 'button',
        'height': 'sm',
        'style': 'primary' if emphasized else 'link',
        'action': {'type': 'message', 'label': suggestion.label[:_BUTTON_LABEL_MAX], 'text': suggestion.prompt},
    }
    if emphasized:
        button['color'] = DESTRUCTIVE_COLOR
    return button
