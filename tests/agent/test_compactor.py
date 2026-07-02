import typing

from pydantic_ai.messages import (
    ModelMessagesTypeAdapter,
    ModelRequest,
    ModelResponse,
    TextPart,
    UserPromptPart,
)
from pydantic_ai.models.test import TestModel
from pydantic_ai.usage import RequestUsage

from conciergent.agent import PydanticAICompactor


def _history(*, latest_input_tokens: int) -> list[typing.Any]:
    messages = [
        ModelRequest(parts=[UserPromptPart('first question')]),
        ModelResponse(parts=[TextPart('first answer')], usage=RequestUsage(input_tokens=10)),
        ModelRequest(parts=[UserPromptPart('second question')]),
        ModelResponse(parts=[TextPart('second answer')], usage=RequestUsage(input_tokens=latest_input_tokens)),
    ]
    return list(ModelMessagesTypeAdapter.dump_python(messages, mode='json'))


def _compactor(input_token_limit: int = 1000) -> PydanticAICompactor:
    return PydanticAICompactor(
        TestModel(call_tools=[], custom_output_text='the summary'), input_token_limit=input_token_limit
    )


async def test_below_threshold_keeps_history():
    assert await _compactor().compact_if_needed(_history(latest_input_tokens=100)) is None


async def test_above_threshold_summarizes_older_turns_and_keeps_the_last_exchange():
    replacement = await _compactor().compact_if_needed(_history(latest_input_tokens=900))
    assert replacement is not None
    messages = ModelMessagesTypeAdapter.validate_python(replacement)
    assert isinstance(messages[0], ModelRequest)
    assert isinstance(messages[1], ModelResponse)
    summary_part = messages[1].parts[0]
    assert isinstance(summary_part, TextPart)
    assert summary_part.content == 'the summary'
    last_request = messages[2]
    assert isinstance(last_request, ModelRequest)
    question_part = last_request.parts[0]
    assert isinstance(question_part, UserPromptPart)
    assert question_part.content == 'second question'


async def test_split_never_orphans_a_tool_return():
    from pydantic_ai.messages import ToolCallPart, ToolReturnPart

    tool_turn = [
        ModelResponse(
            parts=[ToolCallPart(tool_name='t', args={}, tool_call_id='c1')], usage=RequestUsage(input_tokens=900)
        ),
        ModelRequest(parts=[ToolReturnPart(tool_name='t', content='ok', tool_call_id='c1'), UserPromptPart('and?')]),
        ModelResponse(parts=[TextPart('done')], usage=RequestUsage(input_tokens=950)),
    ]

    # With an earlier clean exchange, the split lands there and the call stays with its return.
    messages = [
        ModelRequest(parts=[UserPromptPart('old question')]),
        ModelResponse(parts=[TextPart('old answer')], usage=RequestUsage(input_tokens=10)),
        ModelRequest(parts=[UserPromptPart('do it')]),
        *tool_turn,
    ]
    history = list(ModelMessagesTypeAdapter.dump_python(messages, mode='json'))
    replacement = await _compactor().compact_if_needed(history)
    assert replacement is not None
    decoded = ModelMessagesTypeAdapter.validate_python(replacement)
    kept_kinds = [type(part).__name__ for message in decoded for part in message.parts]
    assert 'ToolCallPart' in kept_kinds and 'ToolReturnPart' in kept_kinds

    # With no clean boundary before the tool turn, compaction declines instead of orphaning.
    unsplittable = [ModelRequest(parts=[UserPromptPart('do it')]), *tool_turn]
    history = list(ModelMessagesTypeAdapter.dump_python(unsplittable, mode='json'))
    assert await _compactor().compact_if_needed(history) is None


async def test_undecodable_history_is_left_alone():
    assert await _compactor().compact_if_needed([{'not': 'a message'}]) is None
