import pydantic
import pytest

from conciergent import Card, Carousel, Section, Suggestion


def test_card_defaults():
    card = Card(header='Hi', sections=[Section(text='body')])
    assert card.header == 'Hi'
    assert card.sections[0].text == 'body'
    assert card.sections[0].bullets == []
    assert card.hero_image_url is None
    assert card.links == []
    assert card.suggestions == []
    assert card.footnote is None


def test_field_descriptions_survive_for_llm_schema():
    # The field descriptions are the agent's structured-output instructions.
    # If they vanish, the agent loses its guidance, so guard them here.
    schema = Card.model_json_schema()
    assert schema['properties']['header']['description']
    assert Suggestion.model_json_schema()['properties']['prompt']['description']


def test_carousel_holds_options_and_fallback():
    carousel = Carousel(
        options=[Card(header='a', sections=[Section(text='x')])],
        fallback=Card(header='b', sections=[Section(text='y')]),
    )
    assert len(carousel.options) == 1
    assert carousel.fallback.header == 'b'


def test_length_guardrails_reject_oversized_text():
    # The length caps guard the LLM against unrenderable output, so keep them enforced.
    with pytest.raises(pydantic.ValidationError):
        Section(text='x' * 101)
    with pytest.raises(pydantic.ValidationError):
        Card(header='x' * 41, sections=[Section(text='b')])
    with pytest.raises(pydantic.ValidationError):
        Suggestion(label='x' * 51, prompt='ok')


def test_card_requires_a_header_and_a_section():
    with pytest.raises(pydantic.ValidationError):
        Card(sections=[Section(text='b')])
    with pytest.raises(pydantic.ValidationError):
        Card(header='Hi')
