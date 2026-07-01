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


def test_carousel_holds_cards():
    carousel = Carousel(cards=[Card(title='a'), Card(title='b')])
    assert len(carousel.cards) == 2
