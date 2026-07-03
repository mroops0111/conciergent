import typing

from conciergent.defaults import DEFAULTS
from conciergent.reply import Card, Link, Section, Suggestion


BRAND_COLOR = DEFAULTS.surface.brand_color
DESTRUCTIVE_COLOR = DEFAULTS.surface.destructive_color

# The action_id prefixes tagging an interactive element, so the webhook routes a click back to its source.
SUGGESTION_ACTION_PREFIX = 'suggestion'
LINK_ACTION_PREFIX = 'link'

# Whether a suggestion group takes many picks (open) or is consumed by the first pick (exclusive).
Scope = typing.Literal['exclusive', 'open']

# The mrkdwn dialect hint injected into the agent's system prompt for this surface.
TEXT_FORMATTING_INSTRUCTION = (
    'Slack renders mrkdwn only. Use *bold* with single asterisks, _italic_, ~strike~, `code`, '
    '<URL|text> for links, and "- " or "1. " for lists. Never use **double asterisks** or [text](url).'
)


# Slack caps the header at 150 plain-text characters, button labels at 75, button values at 2000, and urls at 240.
_HEADER_MAX = 150
_LABEL_MAX = 75
_VALUE_MAX = 2000
_URL_MAX = 240


def build_card_blocks(
    card: Card,
    *,
    scope: Scope,
    card_index: int = 0,
    include_header: bool = False,
    destructive: bool = False,
) -> list[dict[str, typing.Any]]:
    """Render one card to Block Kit blocks.

    The header is normally carried as the message's top-level text, which Slack shows as a bold
    preamble, so it is only emitted as a block when ``include_header`` asks for it (carousel cards).
    """
    blocks: list[dict[str, typing.Any]] = []
    if include_header:
        blocks.append(_build_markdown_section(f'*{card.header[:_HEADER_MAX]}*'))
    if card.hero_image_url:
        blocks.append({'type': 'image', 'image_url': card.hero_image_url, 'alt_text': card.header[:_HEADER_MAX]})
    for section in card.sections:
        blocks.extend(_section_blocks(section))
    if card.footnote:
        blocks.append({'type': 'context', 'elements': [{'type': 'mrkdwn', 'text': card.footnote}]})
    if card.links:
        blocks.append(_build_links_block(card.links))
    suggestions = _build_suggestions_block(card.suggestions, scope=scope, card_index=card_index, destructive=destructive)
    if suggestions is not None:
        blocks.append(suggestions)
    return blocks


def build_card_payload(
    card: Card,
    *,
    destructive: bool = False,
    brand_color: str = BRAND_COLOR,
    destructive_color: str = DESTRUCTIVE_COLOR,
) -> dict[str, typing.Any]:
    """Render a single card message, a color-striped attachment plus the header as preamble text."""
    scope: Scope = 'exclusive' if destructive else 'open'
    blocks = build_card_blocks(card, scope=scope, destructive=destructive)
    color = destructive_color if destructive else brand_color
    return {'text': card.header, 'attachments': [{'color': color, 'blocks': blocks}]}


def build_carousel_payload(cards: list[Card], *, brand_color: str = BRAND_COLOR) -> dict[str, typing.Any]:
    """Render carousel cards into one message, divider-separated, with pick-one button semantics."""
    blocks: list[dict[str, typing.Any]] = []
    for index, card in enumerate(cards):
        if index:
            blocks.append({'type': 'divider'})
        blocks.extend(build_card_blocks(card, scope='exclusive', card_index=index, include_header=True))
    return {'text': '', 'attachments': [{'color': brand_color, 'blocks': blocks}]}


def build_processing_patch(message: dict[str, typing.Any], status_text: str) -> dict[str, typing.Any]:
    """Patch an interacted message in place, disabling every button and appending a status line.

    When the message used attachments the status line lands inside the last attachment,
    so the color stripe keeps covering it; otherwise it appends to the top-level blocks.
    Exactly one of ``blocks`` or ``attachments`` is set, mirroring the shape of the original message.
    """
    status = _build_markdown_section(f'*{status_text}*')
    patch: dict[str, typing.Any] = {'replace_original': True, 'text': message.get('text', '')}
    attachments = message.get('attachments') or []
    if attachments:
        last_index = len(attachments) - 1
        new_attachments: list[dict[str, typing.Any]] = []
        for index, attachment in enumerate(attachments):
            blocks = _without_actions(attachment.get('blocks', []))
            if index == last_index:
                blocks = [*blocks, status]
            new_attachments.append({**attachment, 'blocks': blocks})
        patch['attachments'] = new_attachments
    else:
        patch['blocks'] = [*_without_actions(message.get('blocks', [])), status]
    return patch


def parse_suggestion_scope(action_id: str) -> Scope | None:
    """Return the scope encoded in a suggestion action id, or None for non-suggestion actions."""
    parts = action_id.split(':')
    if parts[0] != SUGGESTION_ACTION_PREFIX or len(parts) < 2:
        return None
    return 'exclusive' if parts[1] == 'exclusive' else 'open'


def _section_blocks(section: Section) -> list[dict[str, typing.Any]]:
    blocks: list[dict[str, typing.Any]] = []
    if section.text:
        blocks.append(_build_markdown_section(section.text))
    if section.bullets:
        bullets = '\n'.join(f'• {item.lstrip("•·▸-* ").strip()}' for item in section.bullets)
        blocks.append(_build_markdown_section(bullets))
    return blocks


def _build_markdown_section(text: str) -> dict[str, typing.Any]:
    return {'type': 'section', 'text': {'type': 'mrkdwn', 'text': text}}


def _build_links_block(links: list[Link]) -> dict[str, typing.Any]:
    elements: list[dict[str, typing.Any]] = []
    for index, link in enumerate(links):
        button: dict[str, typing.Any] = {
            'type': 'button',
            'text': {'type': 'plain_text', 'text': link.label[:_LABEL_MAX]},
            'url': link.url,
            'action_id': f'{LINK_ACTION_PREFIX}:{link.url[:_URL_MAX]}',
        }
        if index == 0:
            button['style'] = 'primary'
        elements.append(button)
    return {'type': 'actions', 'elements': elements}


def _build_suggestions_block(
    suggestions: list[Suggestion], *, scope: Scope, card_index: int, destructive: bool = False
) -> dict[str, typing.Any] | None:
    if not suggestions:
        return None
    elements: list[dict[str, typing.Any]] = []
    for index, suggestion in enumerate(suggestions):
        button: dict[str, typing.Any] = {
            'type': 'button',
            'text': {'type': 'plain_text', 'text': suggestion.label[:_LABEL_MAX]},
            'value': suggestion.prompt[:_VALUE_MAX],
            'action_id': f'{SUGGESTION_ACTION_PREFIX}:{scope}:{card_index}:{index}',
        }
        if destructive and index == 0:
            button['style'] = 'danger'
        elements.append(button)
    return {'type': 'actions', 'elements': elements}


def _without_actions(blocks: list[dict[str, typing.Any]]) -> list[dict[str, typing.Any]]:
    return [block for block in blocks if block.get('type') != 'actions']
