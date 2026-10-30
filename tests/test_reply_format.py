import typing

import pydantic
import pytest

from conciergent.reply import Card, Fact, Section
from conciergent.reply_format import CardReplyFormat, CardShape, FreeReplyFormat
from tests.conftest import card_shape


def _format(**overrides: typing.Any) -> CardReplyFormat:
    return CardReplyFormat(CardShape.model_validate(card_shape(**overrides)))


def _fill(reply_format: CardReplyFormat, output: dict[str, typing.Any]) -> Card:
    """Validate ``output`` as the agent's tool call would be, then turn it into the reply."""
    (tool_output,) = reply_format.outputs
    reply = reply_format.to_reply(tool_output.output.model_validate(output))
    assert isinstance(reply, Card)
    return reply


def test_free_format_offers_text_a_card_and_a_carousel():
    reply_format = FreeReplyFormat()
    card = Card(header='h', sections=[Section(text='b')])

    assert [getattr(output, 'name', output) for output in reply_format.outputs] == [
        str,
        'reply_card',
        'reply_carousel',
    ]
    assert reply_format.to_reply(card) is card


def test_a_shaped_card_fills_its_templates_in_the_declared_order():
    card = _fill(_format(), {'store': 'Taipei', 'totals': {'facts': {'value_1': '42', 'value_2': '3'}}})

    assert card == Card(
        header='Daily summary for Taipei',
        sections=[Section(title='Totals', facts=[Fact(label='Orders', value='42'), Fact(label='Refunds', value='3')])],
    )


def test_an_omitted_optional_value_drops_its_row_and_an_omitted_section_is_left_out():
    card = _fill(_format(), {'store': 'Taipei', 'totals': {'facts': {'value_1': '42'}}})

    assert [section.title for section in card.sections] == ['Totals']
    assert card.sections[0].facts == [Fact(label='Orders', value='42')]


@pytest.mark.parametrize(
    'output',
    [
        # A required label's value is missing.
        {'store': 'Taipei', 'totals': {'facts': {}}},
        # A required section is missing.
        {'store': 'Taipei'},
        # The header grows past its 40 characters once the placeholder is filled.
        {'store': 'x' * 30, 'totals': {'facts': {'value_1': '42'}}},
    ],
)
def test_an_output_that_breaks_the_shape_goes_back_to_the_agent(output: dict[str, typing.Any]):
    (tool_output,) = _format().outputs

    with pytest.raises(pydantic.ValidationError):
        tool_output.output.model_validate(output)


def test_a_repeated_section_adds_one_section_per_item_with_its_own_title():
    sections = [
        {
            'name': 'tasks',
            'title': '{task}',
            'placeholders': {'task': {'description': "The task's name."}},
            'content': ['text', 'facts'],
            'repeated': True,
        }
    ]
    reply_format = _format(header='Overdue tasks', placeholders={}, sections=sections)

    card = _fill(
        reply_format,
        {
            'tasks': [
                {'task': 'Renew lease', 'facts': [{'label': 'Due', 'value': '10/26'}]},
                {'task': 'File taxes', 'text': 'Waiting on the accountant.'},
            ]
        },
    )

    assert card.sections == [
        Section(title='Renew lease', facts=[Fact(label='Due', value='10/26')]),
        Section(title='File taxes', text='Waiting on the accountant.'),
    ]


def test_the_output_schema_carries_the_declared_descriptions():
    sections = [{'name': 'notes', 'content': 'text', 'description': 'Anything worth a second look.'}]
    (tool_output,) = _format(sections=sections).outputs

    schema = tool_output.output.model_json_schema()

    assert schema['properties']['store']['description'] == (
        'The store\'s name. It fills {store} in the header "Daily summary for {store}". '
        'The rest of that text is added for you, so write only this part.'
    )
    # A section's description travels on its own definition, which the property refers to.
    assert schema['$defs']['NotesSection']['description'] == (
        'The "notes" section, shown without a title. Anything worth a second look.'
    )
    assert tool_output.name == 'reply_card'
    assert tool_output.description is not None and 'fixed text are set for you' in tool_output.description


@pytest.mark.parametrize(
    ('overrides', 'message'),
    [
        ({'header': 'Daily summary for {city}'}, r"placeholders \['city'\] are used but not declared"),
        ({'header': 'Daily summary'}, r"placeholders \['store'\] are declared but no template uses them"),
        ({'header': '{store.name}'}, 'write a placeholder as a plain name'),
        ({'sections': [{'name': 'json', 'content': 'text'}]}, "'json' is reserved"),
        ({'sections': [{'name': 'Totals', 'content': 'text'}]}, 'should match pattern'),
        ({'sections': [{'name': 'a', 'content': 'text'}, {'name': 'a', 'content': 'text'}]}, 'its own name'),
        ({'sections': [{'name': 'store', 'content': 'text'}]}, 'name both a placeholder and a section'),
        ({'sections': [{'name': 'a', 'content': ['bullets', 'facts']}]}, 'either bullets or facts'),
        ({'sections': [{'name': 'a', 'content': 'text', 'labels': ['Orders']}]}, 'need facts in its content'),
        ({'sections': [{'name': 'a', 'content': 'facts', 'labels': ['Orders', 'Orders']}]}, 'each named once'),
        ({'sections': [{'name': 'a', 'content': 'text', 'colour': 'red'}]}, 'Extra inputs are not permitted'),
        # A shape describes one card, and repetition lives in a section.
        ({'repeated': True}, 'Extra inputs are not permitted'),
        ({'sections': [{'name': 'a', 'title': 'A'}]}, 'needs `content`, or `labels`'),
        # A placeholder's description is spelled out, so guidance never looks like displayed text.
        ({'placeholders': {'store': "The store's name."}}, 'valid dictionary or instance of PlaceholderShape'),
        ({'placeholders': {'store': {'description': 'Name.', 'pattern': '('}}}, 'not a valid pattern'),
        ({'sections': [{'name': 'a', 'labels': [{'label': 'Orders', 'pattern': '('}]}]}, 'not a valid pattern'),
    ],
)
def test_a_broken_shape_is_rejected_when_the_config_loads(overrides: dict[str, typing.Any], message: str):
    with pytest.raises(pydantic.ValidationError, match=message):
        CardShape.model_validate(card_shape(**overrides))


def test_fixed_labels_hold_facts_without_naming_the_content():
    (section,) = CardShape.model_validate(card_shape()).sections[:1]

    assert section.content is None
    assert section.kinds == ['facts']


def test_a_placeholder_pattern_sends_a_value_in_another_format_back_to_the_agent():
    placeholders = {
        'store': {'description': "The store's name."},
        'date': {'description': 'The day.', 'pattern': r'^\d{1,2}/\d{1,2}$'},
    }
    reply_format = _format(subtitle='{date}', placeholders=placeholders)
    (tool_output,) = reply_format.outputs
    card = {'store': 'Taipei', 'totals': {'facts': {'value_1': '42'}}}

    with pytest.raises(pydantic.ValidationError, match='should match pattern'):
        tool_output.output.model_validate({**card, 'date': 'October 29'})
    assert _fill(reply_format, {**card, 'date': '10/29'}).subtitle == '10/29'


def test_a_label_carries_its_own_guidance_and_pattern():
    labels = ['Orders', {'label': 'Last order', 'description': 'The time, as HH:MM.', 'pattern': r'^\d{2}:\d{2}$'}]
    reply_format = _format(sections=[{'name': 'totals', 'title': 'Totals', 'labels': labels}])
    (tool_output,) = reply_format.outputs
    schema = tool_output.output.model_json_schema()['$defs']['TotalsFacts']['properties']['value_2']

    assert (
        schema['description']
        == 'The value shown beside the label "Last order", which is added for you. The time, as HH:MM.'
    )
    with pytest.raises(pydantic.ValidationError, match='should match pattern'):
        tool_output.output.model_validate(
            {'store': 'Taipei', 'totals': {'facts': {'value_1': '42', 'value_2': 'noon'}}}
        )
    card = _fill(reply_format, {'store': 'Taipei', 'totals': {'facts': {'value_1': '42', 'value_2': '21:40'}}})
    assert card.sections[0].facts[1] == Fact(label='Last order', value='21:40')
