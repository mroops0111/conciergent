from conciergent.reply import Carousel, Link, Section, Suggestion
from conciergent.surfaces.discord import render
from tests.conftest import digest_card, make_card


def test_card_renders_an_embed_with_title_and_bulleted_description() -> None:
    message = render.build_card_message(make_card(sections=[Section(text='Hello', bullets=['a', 'b'])]))
    embed = message['embeds'][0]
    assert embed['title'] == 'Tasks'
    assert 'Hello' in embed['description']
    assert '• a' in embed['description']


def test_description_keeps_the_subtitle_titles_and_facts_in_section_order() -> None:
    embed = render.build_card_message(digest_card())['embeds'][0]

    assert embed['description'] == (
        '_Yesterday_\n\n**Totals**\nOrders: **42**\nRevenue: **$12,300**\n\n**Anomalies**\n• #A1023 is negative'
    )
    assert 'fields' not in embed


def test_description_is_capped_at_the_discord_limit() -> None:
    sections = [Section(text='x' * 100, bullets=['y' * 1000] * 10) for _ in range(6)]

    embed = render.build_card_message(make_card(sections=sections))['embeds'][0]

    assert len(embed['description']) == 4096


def test_links_become_link_buttons_without_a_custom_id() -> None:
    message = render.build_card_message(make_card(links=[Link(label='Open', url='https://example.com')]))
    button = message['components'][0]['components'][0]
    assert button['style'] == 5
    assert button['url'] == 'https://example.com'
    assert 'custom_id' not in button


def test_suggestion_custom_id_round_trips_a_prompt_containing_a_colon() -> None:
    message = render.build_card_message(make_card(suggestions=[Suggestion(label='More', prompt='Show more: NDA')]))
    button = message['components'][0]['components'][0]
    parsed = render.parse_suggestion(button['custom_id'])
    assert parsed == ('open', 'Show more: NDA')


def test_destructive_card_uses_the_destructive_color_and_a_danger_button() -> None:
    card = make_card(
        suggestions=[Suggestion(label='Confirm', prompt='confirm'), Suggestion(label='Cancel', prompt='cancel')]
    )
    message = render.build_card_message(card, destructive=True)
    assert message['embeds'][0]['color'] == int(render.DESTRUCTIVE_COLOR.lstrip('#'), 16)
    buttons = message['components'][0]['components']
    assert buttons[0]['style'] == 4
    assert buttons[1]['style'] == 2
    assert render.parse_suggestion(buttons[0]['custom_id']) == ('exclusive', 'confirm')


def test_carousel_renders_one_embed_per_card_in_order() -> None:
    carousel = Carousel(
        options=[
            make_card(header='A', suggestions=[Suggestion(label='A', prompt='pick A')]),
            make_card(header='B', suggestions=[Suggestion(label='B', prompt='pick B')]),
        ],
        fallback=make_card(header='None fit'),
    )
    message = render.build_carousel_message([*carousel.options, carousel.fallback])
    assert [embed['title'] for embed in message['embeds']] == ['A', 'B', 'None fit']


def test_parse_suggestion_ignores_a_non_suggestion_custom_id() -> None:
    assert render.parse_suggestion('other:thing') is None
