import typing

from ...reply import Card, Link, Section, Suggestion


BRAND_COLOR = '#586af2'
DESTRUCTIVE_COLOR = '#DC3545'

# Slack caps header and plain text at 150 characters, button labels at 75, and button values at 2000.
_TITLE_MAX = 150
_LABEL_MAX = 75
_VALUE_MAX = 2000

SUGGESTION_ACTION_PREFIX = 'suggestion'
LINK_ACTION_PREFIX = 'link'

Scope = typing.Literal['exclusive', 'open']

TEXT_FORMATTING_INSTRUCTION = (
    'Slack renders mrkdwn only. Use *bold* with single asterisks, _italic_, ~strike~, `code`, '
    '<URL|text> for links, and "- " or "1. " for lists. Never use **double asterisks** or [text](url).'
)


def build_card_blocks(
    card: Card,
    *,
    scope: Scope,
    card_index: int = 0,
    include_title: bool = False,
    destructive: bool = False,
) -> list[dict[str, typing.Any]]:
    """Render one card to Block Kit blocks.

    The title is normally carried as the message's top-level text, which Slack shows as a bold
    preamble, so it is only emitted as a block when ``include_title`` asks for it (carousel cards).
    """
    blocks: list[dict[str, typing.Any]] = []
    if include_title and card.title:
        blocks.append(_markdown_section(f'*{card.title[:_TITLE_MAX]}*'))
    blocks.extend(_section_block(section) for section in card.sections)
    if card.links:
        blocks.append(_links_block(card.links))
    if card.suggestions:
        blocks.append(_suggestions_block(card.suggestions, scope=scope, card_index=card_index, destructive=destructive))
    if card.footnote:
        blocks.append({'type': 'context', 'elements': [{'type': 'mrkdwn', 'text': card.footnote}]})
    return blocks


def build_card_payload(card: Card, *, destructive: bool = False) -> dict[str, typing.Any]:
    """Render a single card message, a color-striped attachment plus the title as preamble text."""
    scope: Scope = 'exclusive' if destructive else 'open'
    blocks = build_card_blocks(card, scope=scope, destructive=destructive)
    color = DESTRUCTIVE_COLOR if destructive else BRAND_COLOR
    return {'text': card.title or '', 'attachments': [{'color': color, 'blocks': blocks}]}


def build_carousel_payload(cards: list[Card]) -> dict[str, typing.Any]:
    """Render carousel cards into one message, divider-separated, with pick-one button semantics."""
    blocks: list[dict[str, typing.Any]] = []
    for index, card in enumerate(cards):
        if index:
            blocks.append({'type': 'divider'})
        blocks.extend(build_card_blocks(card, scope='exclusive', card_index=index, include_title=True))
    return {'text': '', 'attachments': [{'color': BRAND_COLOR, 'blocks': blocks}]}


def build_processing_patch(message: dict[str, typing.Any], status_text: str) -> dict[str, typing.Any]:
    """Patch an interacted message in place, disabling every button and appending a status line.

    When the message used attachments the status line lands inside the last attachment,
    so the color stripe keeps covering it.
    """
    status = _markdown_section(f'*{status_text}*')
    attachments = [
        {**attachment, 'blocks': _without_actions(attachment.get('blocks', []))}
        for attachment in message.get('attachments', [])
    ]
    blocks = _without_actions(message.get('blocks', []))
    if attachments:
        attachments[-1]['blocks'].append(status)
    else:
        blocks = [*blocks, status]
    return {
        'replace_original': True,
        'text': message.get('text', ''),
        'blocks': blocks,
        'attachments': attachments,
    }


def parse_suggestion_scope(action_id: str) -> Scope | None:
    """Return the scope encoded in a suggestion action id, or None for non-suggestion actions."""
    parts = action_id.split(':')
    if parts[0] != SUGGESTION_ACTION_PREFIX or len(parts) < 2:
        return None
    return 'exclusive' if parts[1] == 'exclusive' else 'open'


def _section_block(section: Section) -> dict[str, typing.Any]:
    text = f'*{section.heading}*\n{section.text}' if section.heading else section.text
    return _markdown_section(text)


def _markdown_section(text: str) -> dict[str, typing.Any]:
    return {'type': 'section', 'text': {'type': 'mrkdwn', 'text': text}}


def _links_block(links: list[Link]) -> dict[str, typing.Any]:
    elements: list[dict[str, typing.Any]] = []
    for index, link in enumerate(links):
        button: dict[str, typing.Any] = {
            'type': 'button',
            'text': {'type': 'plain_text', 'text': link.text[:_LABEL_MAX]},
            'url': link.url,
            'action_id': f'{LINK_ACTION_PREFIX}:{link.url[:240]}',
        }
        if index == 0:
            button['style'] = 'primary'
        elements.append(button)
    return {'type': 'actions', 'elements': elements}


def _suggestions_block(
    suggestions: list[Suggestion], *, scope: Scope, card_index: int, destructive: bool = False
) -> dict[str, typing.Any]:
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
