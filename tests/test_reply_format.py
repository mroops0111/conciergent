import typing

import pydantic
import pytest

from conciergent.reply import Card, CardSequence, Fact, Section
from conciergent.reply_format import CardReplyFormat, CardShape, FreeReplyFormat
from tests.conftest import card_shape


def _format(**overrides: typing.Any) -> CardReplyFormat:
    return CardReplyFormat(CardShape.model_validate(card_shape(**overrides)))


def _fill(reply_format: CardReplyFormat, output: dict[str, typing.Any]) -> CardSequence:
    """Validate ``output`` as the agent's tool call would be, then turn it into the reply."""
    (tool_output,) = reply_format.outputs
    reply = reply_format.to_reply(tool_output.output.model_validate(output))
    assert isinstance(reply, CardSequence)
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
    reply = _fill(_format(), {'store': 'Taipei', 'totals': {'facts': {'value_1': '42', 'value_2': '3'}}})

    assert reply.cards == [
        Card(
            header='Taipei · Daily summary',
            sections=[
                Section(title='Totals', facts=[Fact(label='Orders', value='42'), Fact(label='Refunds', value='3')])
            ],
        )
    ]


def test_an_omitted_optional_value_drops_its_row_and_an_omitted_section_is_left_out():
    reply = _fill(_format(), {'store': 'Taipei', 'totals': {'facts': {'value_1': '42'}}})

    (card,) = reply.cards
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
            'placeholders': {'task': "The task's name."},
            'content': ['text', 'facts'],
            'repeated': True,
        }
    ]
    reply_format = _format(header='Overdue tasks', placeholders={}, sections=sections)

    reply = _fill(
        reply_format,
        {
            'tasks': [
                {'task': 'Renew lease', 'facts': [{'label': 'Due', 'value': '10/26'}]},
                {'task': 'File taxes', 'text': 'Waiting on the accountant.'},
            ]
        },
    )

    (card,) = reply.cards
    assert card.sections == [
        Section(title='Renew lease', facts=[Fact(label='Due', value='10/26')]),
        Section(title='File taxes', text='Waiting on the accountant.'),
    ]


def test_a_repeated_card_sends_one_card_per_item():
    reply_format = _format(repeated=True)
    card = {'totals': {'facts': {'value_1': '42'}}}

    reply = _fill(reply_format, {'cards': [{'store': 'Taipei', **card}, {'store': 'Tainan', **card}]})

    assert [card.header for card in reply.cards] == ['Taipei · Daily summary', 'Tainan · Daily summary']
    assert reply_format.outputs[0].name == 'reply_cards'
    assert 'at most 5 cards' in reply_format.instructions


def test_the_output_schema_carries_the_declared_descriptions():
    sections = [{'name': 'notes', 'content': 'text', 'description': 'Anything worth a second look.'}]
    (tool_output,) = _format(sections=sections).outputs

    schema = tool_output.output.model_json_schema()

    assert schema['properties']['store']['description'] == "The store's name."
    # A section's description travels on its own definition, which the property refers to.
    assert schema['$defs']['NotesSection']['description'] == 'Anything worth a second look.'
    assert tool_output.name == 'reply_card'


@pytest.mark.parametrize(
    ('overrides', 'message'),
    [
        ({'header': '{city} · Daily summary'}, r"placeholders \['city'\] are used but not described"),
        ({'header': 'Daily summary'}, r"placeholders \['store'\] are described but no template uses them"),
        ({'header': '{store.name}'}, 'write a placeholder as a plain name'),
        ({'sections': [{'name': 'json', 'content': 'text'}]}, "'json' is reserved"),
        ({'sections': [{'name': 'Totals', 'content': 'text'}]}, 'should match pattern'),
        ({'sections': [{'name': 'a', 'content': 'text'}, {'name': 'a', 'content': 'text'}]}, 'its own name'),
        ({'sections': [{'name': 'store', 'content': 'text'}]}, 'name both a placeholder and a section'),
        ({'sections': [{'name': 'a', 'content': ['bullets', 'facts']}]}, 'either bullets or facts'),
        ({'sections': [{'name': 'a', 'content': 'text', 'labels': ['Orders']}]}, 'need facts in its content'),
        ({'sections': [{'name': 'a', 'content': 'facts', 'labels': ['Orders', 'Orders']}]}, 'each named once'),
        ({'sections': [{'name': 'a', 'content': 'text', 'colour': 'red'}]}, 'Extra inputs are not permitted'),
    ],
)
def test_a_broken_shape_is_rejected_when_the_config_loads(overrides: dict[str, typing.Any], message: str):
    with pytest.raises(pydantic.ValidationError, match=message):
        CardShape.model_validate(card_shape(**overrides))
