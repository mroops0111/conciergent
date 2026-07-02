from conciergent import Card, Link, Section, Suggestion
from conciergent.surfaces.line import render


def _card() -> Card:
    return Card(
        title='Tasks',
        sections=[Section(text='You have two tasks.', heading='Today')],
        links=[Link(text='Open', url='https://example.com/tasks'), Link(text='Docs', url='https://example.com/docs')],
        suggestions=[Suggestion(label='More', prompt='List more tasks')],
        footnote='Active only',
    )


def test_bubble_carries_title_header_and_body():
    bubble = render.build_card_bubble(_card())
    header_text = bubble['header']['contents'][0]
    assert header_text['text'] == 'Tasks'
    assert header_text['color'] == render.BRAND_COLOR
    body_texts = [node['text'] for node in bubble['body']['contents'] if node['type'] == 'text']
    assert body_texts == ['Today', 'You have two tasks.', 'Active only']


def test_footnote_is_separated_and_muted():
    contents = render.build_card_bubble(_card())['body']['contents']
    assert contents[-2]['type'] == 'separator'
    assert contents[-1]['size'] == 'xxs'


def test_first_link_is_primary_brand_button():
    footer = render.build_card_bubble(_card())['footer']['contents']
    assert footer[0]['style'] == 'primary'
    assert footer[0]['color'] == render.BRAND_COLOR
    assert footer[1]['style'] == 'secondary'
    assert footer[0]['action'] == {'type': 'uri', 'label': 'Open', 'uri': 'https://example.com/tasks'}


def test_chip_placement_keeps_suggestions_out_of_the_footer():
    bubble = render.build_card_bubble(_card(), suggestion_placement='chip')
    labels = [button['action'].get('label') for button in bubble['footer']['contents']]
    assert 'More' not in labels
    quick_reply = render.build_quick_reply(_card().suggestions)
    assert quick_reply is not None
    assert quick_reply['items'][0]['action'] == {'type': 'message', 'label': 'More', 'text': 'List more tasks'}


def test_destructive_placement_emphasizes_the_first_suggestion():
    card = Card(
        title='Confirm', suggestions=[Suggestion(label='Yes', prompt='Yes'), Suggestion(label='No', prompt='No')]
    )
    footer = render.build_card_bubble(card, suggestion_placement='destructive_button')['footer']['contents']
    assert footer[0]['style'] == 'primary'
    assert footer[0]['color'] == render.DESTRUCTIVE_COLOR
    assert footer[1]['style'] == 'link'


def test_carousel_renders_bubbles_with_button_suggestions():
    cards = [Card(title='A', suggestions=[Suggestion(label='Pick', prompt='Pick A')]), Card(title='B')]
    carousel = render.build_carousel(cards)
    assert carousel['type'] == 'carousel'
    first = carousel['contents'][0]
    assert first['footer']['contents'][0]['action']['text'] == 'Pick A'


def test_labels_are_clamped_to_line_action_caps():
    long_label = 'x' * 50
    chips = render.build_quick_reply([Suggestion(label=long_label, prompt='p')])
    assert chips is not None
    assert len(chips['items'][0]['action']['label']) == 20
    card = Card(links=[Link(text=long_label, url='https://example.com')])
    footer = render.build_card_bubble(card, suggestion_placement='button')['footer']['contents']
    assert len(footer[0]['action']['label']) == 40


def test_alt_text_truncates_to_forty():
    card = Card(title='x' * 40)
    assert render.alt_text(card) == 'x' * 40
    assert render.alt_text(Card(), 'fallback') == 'fallback'
