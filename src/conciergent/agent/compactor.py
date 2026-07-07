import json
import logging
import typing

import pydantic
from genai_prices import data_snapshot
from pydantic_ai import Agent
from pydantic_ai.messages import (
    ModelMessage,
    ModelMessagesTypeAdapter,
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models import Model


logger = logging.getLogger(__name__)

# The compaction thresholds, as fractions of the model's input token limit.
# Compaction fires once history passes the trigger ratio, then shrinks it toward the target ratio.
_COMPACTION_TRIGGER_RATIO = 0.80
_COMPACTION_TARGET_RATIO = 0.20
_SUMMARY_MIN_TARGET_CHARS = 200

# Used when the metadata has no window for the model, low enough to sit under the smallest bundled
# provider window so compaction still fires and never lets history overrun the real limit.
_FALLBACK_INPUT_TOKEN_LIMIT = 128000

_COMPACTION_SUMMARY_STUB = '(Earlier conversation summary)'
_INSTRUCTIONS = (
    'You compact prior chat history into a pickup summary for the assistant. '
    'Capture who the user is, what they asked, and what the assistant did or discovered. '
    'Preserve identifiers, names, emails, URLs, dates, and numbers verbatim, '
    'and keep any pending approval or unresolved state. '
    'Skip small talk, retries, and corrected errors. '
    'Write plain text in the conversation language, third person past tense, no markdown, no preamble.'
)


class HistorySummarizer:
    """Summarize older turns with a small model run when the last request neared the token limit.

    The trigger reads what the model actually saw, the ``input_tokens`` of the latest real response.
    The latest exchange is kept verbatim and everything before it collapses into a summary pair.
    """

    def __init__(
        self,
        model: Model | str,
        *,
        input_token_limit: int | None = None,
        trigger_ratio: float = _COMPACTION_TRIGGER_RATIO,
        target_ratio: float = _COMPACTION_TARGET_RATIO,
    ) -> None:
        # An unset limit is auto-detected from the model, so compaction is on by default.
        self._input_token_limit = input_token_limit if input_token_limit is not None else resolve_input_token_limit(model)
        self._trigger_ratio = trigger_ratio
        self._target_ratio = target_ratio
        self._agent: Agent[None, str] = Agent(model, output_type=str, instructions=_INSTRUCTIONS)

    async def compact_if_needed(self, history: list[typing.Any]) -> list[typing.Any] | None:
        try:
            messages = ModelMessagesTypeAdapter.validate_python(history)
        except pydantic.ValidationError:
            return None
        if _latest_input_tokens(messages) < int(self._input_token_limit * self._trigger_ratio):
            return None
        to_compact, to_keep = _split_before_last_exchange(messages)
        if not to_compact:
            return None
        transcript = _render_transcript(to_compact)
        target_chars = max(int(len(transcript) * self._target_ratio), _SUMMARY_MIN_TARGET_CHARS)
        result = await self._agent.run(f'Target length: under {target_chars} characters.\n\nTranscript:\n{transcript}')
        summary_pair: list[ModelMessage] = [
            ModelRequest(parts=[UserPromptPart(_COMPACTION_SUMMARY_STUB)]),
            ModelResponse(parts=[TextPart(result.output)]),
        ]
        replacement = ModelMessagesTypeAdapter.dump_python([*summary_pair, *to_keep], mode='json')
        return list(replacement)


def resolve_input_token_limit(model: Model | str) -> int:
    """Return the model's context window in tokens, read from the bundled genai-prices metadata.

    The model is a ``provider:model`` reference, for example ``openai:gpt-4o-mini``. An unknown model
    falls back to a conservative window so compaction still runs, and an explicit config value overrides this.
    """
    reference = model if isinstance(model, str) else model.model_name
    provider_id, separator, model_ref = reference.partition(':')
    if not separator:
        provider_id, model_ref = '', reference
    try:
        snapshot = data_snapshot.get_snapshot()
        provider = next((candidate for candidate in snapshot.providers if candidate.id == provider_id), None)
        info = provider.find_model(model_ref, all_providers=snapshot.providers) if provider is not None else None
        if info is not None and info.context_window is not None:
            return info.context_window
    except Exception:
        logger.warning('could not resolve an input token limit for %r, using the fallback', reference, exc_info=True)
    return _FALLBACK_INPUT_TOKEN_LIMIT


def _latest_input_tokens(messages: list[ModelMessage]) -> int:
    # Synthesized summary responses carry zero input tokens and are skipped,
    # so the metric always reflects a real model call.
    for message in reversed(messages):
        if isinstance(message, ModelResponse) and message.usage.input_tokens > 0:
            return message.usage.input_tokens
    return 0


def _split_before_last_exchange(messages: list[ModelMessage]) -> tuple[list[ModelMessage], list[ModelMessage]]:
    """Split so the latest user exchange stays verbatim and everything earlier gets compacted.

    A resumed approval turn packs tool returns and the new user prompt into one request.
    Splitting there would orphan the returns from their summarized-away calls and the provider
    would reject the history, so only a request with no tool returns is a safe boundary.
    """
    for index in range(len(messages) - 1, -1, -1):
        message = messages[index]
        if (
            isinstance(message, ModelRequest)
            and any(isinstance(part, UserPromptPart) for part in message.parts)
            and not any(isinstance(part, ToolReturnPart) for part in message.parts)
        ):
            return list(messages[:index]), list(messages[index:])
    return list(messages), []


def _render_transcript(messages: list[ModelMessage]) -> str:
    lines: list[str] = []
    for message in messages:
        for part in message.parts:
            if isinstance(part, UserPromptPart):
                lines.append(f'User: {_as_text(part.content)}')
            elif isinstance(part, TextPart):
                lines.append(f'Assistant: {part.content}')
            elif isinstance(part, ToolCallPart):
                lines.append(f'Assistant called Tool[{part.tool_name}] with {_as_text(part.args)}')
            elif isinstance(part, ToolReturnPart):
                lines.append(f'Tool[{part.tool_name}] returned: {_as_text(part.content)}')
    return '\n'.join(lines)


def _as_text(value: typing.Any) -> str:
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
