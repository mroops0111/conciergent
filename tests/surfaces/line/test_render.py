from conciergent import Card, Link, Section, Suggestion
from conciergent.surfaces.line import render


def _card() -> Card:
    return Card(
        header='Tasks',
        sections=[Section(text='You have two tasks.', bullets=['Review NDA', 'Sign lease'])],
        links=[Link(label='Open', url='https://example.com/tasks'), Link(label='Docs', url='https://example.com/docs')],
        suggestions=[Suggestion(label='More', prompt='List more tasks')],
        footnote='Active only',
    )


def test_bubble_carries_header_body_and_bullets():
    bubble = render.build_card_bubble(_card())

    header_text = bubble['header']['contents'][0]
    assert header_text['text'] == 'Tasks'
    assert header_text['color'] == render.BRAND_COLOR
    body = bubble['body']['contents']
    assert body[0] == {'type': 'text', 'text': 'You have two tasks.', 'size': 'md', 'wrap': True}
    bullet_box = body[1]
    assert bullet_box['type'] == 'box'
    assert [node['text'] for node in bullet_box['contents']] == ['• Review NDA', '• Sign lease']
    assert bullet_box['contents'][0]['color'] == render.BULLET_TEXT_COLOR


def test_boxes_carry_reference_padding():
    bubble = render.build_card_bubble(_card())

    assert bubble['header']['paddingStart'] == 'xl'
    assert bubble['body']['paddingTop'] == 'md'
    assert bubble['footer']['flex'] == 0
    assert bubble['footer']['paddingBottom'] == 'lg'


def test_footnote_is_separated_and_muted():
    contents = render.build_card_bubble(_card())['body']['contents']

    assert contents[-2]['type'] == 'separator'
    assert contents[-1]['size'] == 'xxs'
    assert contents[-1]['color'] == render.FOOTNOTE_COLOR


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
    assert quick_reply[0]['action'] == {'type': 'message', 'label': 'More', 'text': 'List more tasks'}


def test_destructive_placement_emphasizes_the_first_suggestion():
    card = Card(
        header='Confirm',
        sections=[Section(text='Delete this?')],
        suggestions=[Suggestion(label='Yes', prompt='Yes'), Suggestion(label='No', prompt='No')],
    )

    footer = render.build_card_bubble(card, suggestion_placement='destructive_button')['footer']['contents']

    assert footer[0]['style'] == 'primary'
    assert footer[0]['color'] == render.DESTRUCTIVE_COLOR
    assert footer[1]['style'] == 'secondary'


def test_carousel_renders_bubbles_with_button_suggestions():
    cards = [
        Card(header='A', sections=[Section(text='a')], suggestions=[Suggestion(label='Pick', prompt='Pick A')]),
        Card(header='B', sections=[Section(text='b')]),
    ]

    carousel = render.build_carousel(cards)

    assert carousel['type'] == 'carousel'
    first = carousel['contents'][0]
    assert first['footer']['contents'][0]['action']['text'] == 'Pick A'


def test_hero_image_renders_above_the_bubble():
    hero_image_url = 'https://example.com/seal.png'
    card = Card(header='Seal', sections=[Section(text='ready')], hero_image_url=hero_image_url)

    bubble = render.build_card_bubble(card)

    assert bubble['hero'] == {
        'type': 'image',
        'url': hero_image_url,
        'size': 'full',
        'aspectMode': 'cover',
        'aspectRatio': '20:13',
    }


def test_labels_are_not_truncated():
    # The tightest label cap is the suggestion's, so a label at that cap exercises both without tripping validation.
    long_label = 'x' * 20

    chips = render.build_quick_reply([Suggestion(label=long_label, prompt='p')])
    assert chips[0]['action']['label'] == long_label

    card = Card(header='H', sections=[Section(text='b')], links=[Link(label=long_label, url='https://example.com')])
    footer = render.build_card_bubble(card, suggestion_placement='button')['footer']['contents']
    assert footer[0]['action']['label'] == long_label


def test_alt_text_uses_the_header():
    header = 'x' * 40
    card = Card(header=header, sections=[Section(text='b')])

    assert render.alt_text(card) == header
