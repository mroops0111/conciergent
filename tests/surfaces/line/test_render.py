from conciergent import Link, Section, Suggestion
from conciergent.surfaces.line import render
from tests.conftest import digest_card, make_card, tasks_card


def test_bubble_carries_header_body_and_bullets():
    bubble = render.build_card_bubble(tasks_card())

    assert bubble['header']['backgroundColor'] == render.BRAND_COLOR
    header_text = bubble['header']['contents'][0]
    assert header_text['text'] == 'Tasks'
    assert header_text['color'] == render.HEADER_TEXT_COLOR
    assert header_text['size'] == 'lg'
    body = bubble['body']['contents']
    assert body[0] == {'type': 'text', 'text': 'You have two tasks.', 'size': 'md', 'wrap': True}
    bullet_box = body[1]
    assert bullet_box['type'] == 'box'
    assert [node['text'] for node in bullet_box['contents']] == ['• Review NDA', '• Sign lease']
    assert bullet_box['contents'][0]['color'] == render.BULLET_TEXT_COLOR


def test_boxes_carry_reference_padding():
    bubble = render.build_card_bubble(tasks_card())

    assert bubble['header']['paddingStart'] == 'xl'
    assert bubble['body']['paddingTop'] == 'lg'
    assert bubble['footer']['flex'] == 0
    assert bubble['footer']['paddingBottom'] == 'lg'


def test_subtitle_renders_under_the_header():
    header = render.build_card_bubble(digest_card())['header']['contents']

    assert [node['text'] for node in header] == ['Daily digest', 'Yesterday']
    assert header[1]['size'] == 'sm'


def test_sections_are_separated_and_titled():
    body = render.build_card_bubble(digest_card())['body']['contents']

    assert [node['type'] for node in body] == ['text', 'box', 'separator', 'text', 'box']
    assert body[0] == {'type': 'text', 'text': 'Totals', 'size': 'md', 'weight': 'bold', 'wrap': True}
    assert body[2]['margin'] == 'lg'
    assert body[3]['text'] == 'Anomalies'


def test_facts_split_the_width_and_align_the_value_to_the_end():
    facts_box = render.build_card_bubble(digest_card())['body']['contents'][1]
    label, value = facts_box['contents'][0]['contents']

    assert facts_box['contents'][0]['layout'] == 'horizontal'
    assert (label['text'], label['color'], label['flex'], label['wrap']) == ('Orders', render.FACT_LABEL_COLOR, 1, True)
    assert (value['text'], value['align'], value['flex'], value['wrap']) == ('42', 'end', 1, True)


def test_footnote_is_separated_and_muted():
    contents = render.build_card_bubble(tasks_card())['body']['contents']

    assert contents[-2]['type'] == 'separator'
    assert contents[-1]['size'] == 'xxs'
    assert contents[-1]['color'] == render.FOOTNOTE_COLOR


def test_first_link_is_primary_brand_button():
    footer = render.build_card_bubble(tasks_card())['footer']['contents']

    assert footer[0]['style'] == 'primary'
    assert footer[0]['color'] == render.BRAND_COLOR
    assert footer[1]['style'] == 'secondary'
    assert footer[0]['action'] == {'type': 'uri', 'label': 'Open', 'uri': 'https://example.com/tasks'}


def test_chip_placement_keeps_suggestions_out_of_the_footer():
    card = tasks_card()

    bubble = render.build_card_bubble(card, suggestion_placement='chip')

    labels = [button['action'].get('label') for button in bubble['footer']['contents']]
    assert 'More' not in labels

    quick_reply = render.build_quick_reply(card.suggestions)
    assert quick_reply[0]['action'] == {
        'type': 'postback',
        'label': 'More',
        'data': 'suggestion:List more tasks',
        'displayText': 'List more tasks',
    }


def test_destructive_placement_emphasizes_the_first_suggestion():
    card = make_card(suggestions=[Suggestion(label='Yes', prompt='Yes'), Suggestion(label='No', prompt='No')])

    footer = render.build_card_bubble(card, suggestion_placement='destructive_button')['footer']['contents']

    assert footer[0]['style'] == 'primary'
    assert footer[0]['color'] == render.DESTRUCTIVE_COLOR
    assert footer[1]['style'] == 'secondary'


def test_carousel_renders_bubbles_with_button_suggestions():
    cards = [make_card(header='A', suggestions=[Suggestion(label='Pick', prompt='Pick A')]), make_card(header='B')]

    carousel = render.build_carousel(cards)

    assert carousel['type'] == 'carousel'
    first = carousel['contents'][0]
    assert first['footer']['contents'][0]['action']['data'] == 'suggestion:Pick A'


def test_hero_image_renders_above_the_bubble():
    hero_image_url = 'https://example.com/seal.png'

    bubble = render.build_card_bubble(make_card(hero_image_url=hero_image_url))

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

    card = make_card(links=[Link(label=long_label, url='https://example.com')])
    footer = render.build_card_bubble(card, suggestion_placement='button')['footer']['contents']
    assert footer[0]['action']['label'] == long_label


def test_alt_text_uses_the_header():
    header = 'x' * 40

    assert render.alt_text(make_card(header=header, sections=[Section(text='b')])) == header
