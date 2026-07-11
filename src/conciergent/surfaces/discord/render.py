import typing

from conciergent.defaults import DEFAULTS
from conciergent.reply import Card, Section, Suggestion


BRAND_COLOR = DEFAULTS.surface.discord.brand_color
DESTRUCTIVE_COLOR = DEFAULTS.surface.discord.destructive_color

# The custom_id prefix tagging a suggestion button, so a click routes back to its source.
SUGGESTION_ACTION_PREFIX = 'suggestion'

# Whether a suggestion group takes many picks (open) or is consumed by the first pick (exclusive).
Scope = typing.Literal['exclusive', 'open']

# The markdown dialect hint injected into the agent's system prompt for this surface.
TEXT_FORMATTING_INSTRUCTION = (
    'Discord renders markdown. Use **bold**, _italic_, `code`, ```code blocks```, and "- " or "1. " for lists. '
    'Links show as raw URLs, so write the bare URL rather than [text](url).'
)

# Discord component styles and types.
_BUTTON = 2
_ACTION_ROW = 1
_STYLE_SECONDARY = 2
_STYLE_DANGER = 4
_STYLE_LINK = 5

# Discord caps an embed title at 256 characters, a footer at 2048, a button label at 80, and a custom_id at 100.
_TITLE_MAX = 256
_FOOTER_MAX = 2048
_LABEL_MAX = 80
_BUTTONS_PER_ROW = 5
_MAX_ROWS = 5


def build_card_message(
    card: Card,
    *,
    destructive: bool = False,
    brand_color: str = BRAND_COLOR,
    destructive_color: str = DESTRUCTIVE_COLOR,
) -> dict[str, typing.Any]:
    """Render a single card to a Discord message payload, one embed plus its button rows."""
    scope: Scope = 'exclusive' if destructive else 'open'
    color = destructive_color if destructive else brand_color
    embed = _build_embed(card, color)
    buttons = _card_buttons(card, scope=scope, card_index=0, destructive=destructive)
    return _message([embed], buttons)


def build_carousel_message(cards: list[Card], *, brand_color: str = BRAND_COLOR) -> dict[str, typing.Any]:
    """Render carousel cards into one message, one embed each, with pick-one button semantics."""
    embeds: list[dict[str, typing.Any]] = []
    buttons: list[dict[str, typing.Any]] = []
    for card_index, card in enumerate(cards):
        embeds.append(_build_embed(card, brand_color))
        buttons.extend(_card_buttons(card, scope='exclusive', card_index=card_index, destructive=False))
    return _message(embeds, buttons)


def build_text_message(text: str) -> dict[str, typing.Any]:
    return {'content': text}


def strip_components() -> dict[str, typing.Any]:
    """Return the interaction-update payload that removes the clicked message's buttons, keeping its content."""
    return {'components': []}


def parse_suggestion(custom_id: str) -> tuple[Scope, str] | None:
    """Return the scope and re-fed prompt encoded in a suggestion custom_id, or None for other components.

    The prompt is placed last and split with a bounded count, so a prompt that itself contains a colon survives.
    """
    parts = custom_id.split(':', 4)
    if parts[0] != SUGGESTION_ACTION_PREFIX or len(parts) < 5:
        return None
    scope: Scope = 'exclusive' if parts[1] == 'exclusive' else 'open'
    return scope, parts[4]


def _build_embed(card: Card, color: str) -> dict[str, typing.Any]:
    embed: dict[str, typing.Any] = {'title': card.header[:_TITLE_MAX], 'color': _color_int(color)}
    description = _description(card.sections)
    if description:
        embed['description'] = description
    if card.hero_image_url:
        embed['image'] = {'url': card.hero_image_url}
    if card.footnote:
        embed['footer'] = {'text': card.footnote[:_FOOTER_MAX]}
    return embed


def _description(sections: list[Section]) -> str:
    blocks: list[str] = []
    for section in sections:
        if section.text:
            blocks.append(section.text)
        if section.bullets:
            blocks.append('\n'.join(f'• {item.lstrip("•·▸-* ").strip()}' for item in section.bullets))
    return '\n\n'.join(blocks)


def _card_buttons(card: Card, *, scope: Scope, card_index: int, destructive: bool) -> list[dict[str, typing.Any]]:
    buttons: list[dict[str, typing.Any]] = []
    for link in card.links:
        buttons.append({'type': _BUTTON, 'style': _STYLE_LINK, 'label': link.label[:_LABEL_MAX], 'url': link.url})
    for index, suggestion in enumerate(card.suggestions):
        style = _STYLE_DANGER if destructive and index == 0 else _STYLE_SECONDARY
        buttons.append(
            {
                'type': _BUTTON,
                'style': style,
                'label': suggestion.label[:_LABEL_MAX],
                'custom_id': _suggestion_custom_id(scope, card_index, index, suggestion),
            }
        )
    return buttons


def _suggestion_custom_id(scope: Scope, card_index: int, index: int, suggestion: Suggestion) -> str:
    return f'{SUGGESTION_ACTION_PREFIX}:{scope}:{card_index}:{index}:{suggestion.prompt}'


def _message(embeds: list[dict[str, typing.Any]], buttons: list[dict[str, typing.Any]]) -> dict[str, typing.Any]:
    payload: dict[str, typing.Any] = {'embeds': embeds}
    rows = _rows(buttons)
    if rows:
        payload['components'] = rows
    return payload


def _rows(buttons: list[dict[str, typing.Any]]) -> list[dict[str, typing.Any]]:
    # Discord allows at most five buttons per action row and five rows per message; extra buttons are dropped.
    rows: list[dict[str, typing.Any]] = []
    for start in range(0, len(buttons), _BUTTONS_PER_ROW):
        if len(rows) == _MAX_ROWS:
            break
        rows.append({'type': _ACTION_ROW, 'components': buttons[start : start + _BUTTONS_PER_ROW]})
    return rows


def _color_int(color: str) -> int:
    return int(color.lstrip('#'), 16)
