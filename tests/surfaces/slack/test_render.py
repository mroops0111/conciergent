from conciergent import Card, Link, Section, Suggestion
from conciergent.surfaces.slack import render


def _card() -> Card:
    return Card(
        title='Tasks',
        sections=[Section(text='You have two tasks.', heading='Today')],
        links=[Link(text='Open', url='https://example.com/tasks')],
        suggestions=[Suggestion(label='List more', prompt='List more tasks')],
        footnote='Active only',
    )


def test_card_payload_carries_title_as_preamble_and_color_stripe():
    payload = render.build_card_payload(_card())
    assert payload['text'] == 'Tasks'
    attachment = payload['attachments'][0]
    assert attachment['color'] == render.BRAND_COLOR
    kinds = [block['type'] for block in attachment['blocks']]
    assert kinds == ['section', 'actions', 'actions', 'context']


def test_section_heading_renders_bold_above_text():
    blocks = render.build_card_blocks(_card(), scope='open')
    assert blocks[0]['text']['text'] == '*Today*\nYou have two tasks.'


def test_first_link_is_primary():
    blocks = render.build_card_blocks(_card(), scope='open')
    link_buttons = blocks[1]['elements']
    assert link_buttons[0]['style'] == 'primary'
    assert link_buttons[0]['action_id'].startswith(f'{render.LINK_ACTION_PREFIX}:')


def test_destructive_card_uses_danger_styling_and_exclusive_scope():
    card = Card(
        title='Confirm', suggestions=[Suggestion(label='Yes', prompt='Yes'), Suggestion(label='No', prompt='No')]
    )
    payload = render.build_card_payload(card, destructive=True)
    attachment = payload['attachments'][0]
    assert attachment['color'] == render.DESTRUCTIVE_COLOR
    buttons = attachment['blocks'][0]['elements']
    assert buttons[0]['style'] == 'danger'
    assert buttons[0]['action_id'] == f'{render.SUGGESTION_ACTION_PREFIX}:exclusive:0:0'
    assert 'style' not in buttons[1]


def test_carousel_collapses_cards_with_dividers_and_titles():
    cards = [Card(title='A'), Card(title='B')]
    payload = render.build_carousel_payload(cards)
    blocks = payload['attachments'][0]['blocks']
    assert [block['type'] for block in blocks] == ['section', 'divider', 'section']
    assert blocks[0]['text']['text'] == '*A*'


def test_processing_patch_strips_buttons_and_appends_status():
    message = {
        'text': 'Tasks',
        'attachments': [{'color': '#586af2', 'blocks': [{'type': 'section'}, {'type': 'actions', 'elements': []}]}],
    }
    patch = render.build_processing_patch(message, 'Working...')
    assert patch['replace_original'] is True
    blocks = patch['attachments'][0]['blocks']
    assert [block['type'] for block in blocks] == ['section', 'section']
    assert blocks[-1]['text']['text'] == '*Working...*'


def test_suggestion_scope_parses():
    assert render.parse_suggestion_scope('suggestion:exclusive:0:1') == 'exclusive'
    assert render.parse_suggestion_scope('suggestion:open:0:1') == 'open'
    assert render.parse_suggestion_scope('link:https://example.com') is None
