import pydantic
import pytest

from conciergent import Card, Carousel, Section, Suggestion


def test_card_defaults():
    card = Card(title='Hi', sections=[Section(text='body')])
    assert card.title == 'Hi'
    assert card.sections[0].text == 'body'
    assert card.links == []
    assert card.suggestions == []
    assert card.footnote is None


def test_suggestion_defaults_to_non_exclusive():
    suggestion = Suggestion(label='Yes', prompt='do it')
    assert suggestion.exclusive is False


def test_field_descriptions_survive_for_llm_schema():
    # The field descriptions are the agent's structured-output instructions.
    # If they vanish, the agent loses its guidance, so guard them here.
    schema = Card.model_json_schema()
    assert schema['properties']['title']['description']
    assert Suggestion.model_json_schema()['properties']['prompt']['description']


def test_carousel_holds_options_and_fallback():
    carousel = Carousel(options=[Card(title='a')], fallback=Card(title='b'))
    assert len(carousel.options) == 1
    assert carousel.fallback.title == 'b'


def test_length_guardrails_reject_oversized_text():
    # The length caps guard the LLM against unrenderable output, so keep them enforced.
    with pytest.raises(pydantic.ValidationError):
        Section(text='x' * 101)
    with pytest.raises(pydantic.ValidationError):
        Card(title='x' * 41)
    with pytest.raises(pydantic.ValidationError):
        Suggestion(label='x' * 51, prompt='ok')
