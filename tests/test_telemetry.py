import asyncio
import json
import logging
import os
import subprocess
import sys
import typing

import fastmcp
import pydantic_ai
import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from pydantic_ai.models.test import TestModel

from conciergent import PendingApproval, telemetry
from conciergent.chat_context import DirectChatContext
from conciergent.logger import JsonFormatter
from conciergent.push.outcome import PushOutcome
from conciergent.runtime import OAuthHandoffExpiredError, StatefulOAuthBridge
from conciergent.store.message import MessageStore
from tests.conftest import (
    PetsApi,
    StubRunner,
    app_config,
    ask_through_gateway,
    drive_turn,
    make_card,
    serving_gateway,
)
from tests.push.conftest import STARTED_AT, executor, push_settings


_PRINCIPAL = 'line:U1'
_CONVERSATION = 'line:user:U1'


def _span(spans: InMemorySpanExporter, name: str) -> typing.Any:
    (found,) = [span for span in spans.get_finished_spans() if span.name == name]
    return found


async def _turn(output: typing.Any, message_store: MessageStore) -> None:
    await drive_turn(
        'hi', StubRunner(output), message_store, chat_context=DirectChatContext(conversation=_CONVERSATION)
    )


async def test_a_turn_is_one_span_naming_its_chat_and_how_it_ended(spans, message_store):
    await _turn('hello', message_store)

    turn = _span(spans, 'turn')
    assert turn.attributes['conciergent.conversation'] == _CONVERSATION
    assert turn.attributes['conciergent.chat'] == 'direct'
    assert turn.attributes['conciergent.turn.outcome'] == 'text'


async def test_a_parked_approval_ends_the_turn_span_as_requested_and_resuming_it_is_an_event(spans, message_store):
    await _turn(PendingApproval(card=make_card(), state={'step': 1}), message_store)
    assert _span(spans, 'turn').attributes['conciergent.turn.outcome'] == 'approval_requested'
    spans.clear()

    await _turn('done', message_store)

    turn = _span(spans, 'turn')
    assert [event.name for event in turn.events] == ['approval resumed']
    assert turn.attributes['conciergent.turn.outcome'] == 'text'


class _SilentBridge(StatefulOAuthBridge):
    async def _render_authorization_ui(self, authorize_url: str) -> None:
        pass


async def test_an_oauth_handoff_is_a_span_saying_whether_the_user_finished(spans, message_store):
    async def user_authorizes() -> None:
        await asyncio.sleep(0)
        await message_store.deliver_oauth_code('s1', 'code')

    task = asyncio.create_task(user_authorizes())
    await _SilentBridge(message_store).request_authorization('https://example.com/authorize?state=s1')
    await task
    assert _span(spans, 'oauth handoff').attributes['conciergent.oauth.completed'] is True
    spans.clear()

    with pytest.raises(OAuthHandoffExpiredError):
        await _SilentBridge(message_store, wait_timeout_seconds=0.1).request_authorization(
            'https://example.com/authorize?state=s2'
        )
    assert _span(spans, 'oauth handoff').attributes['conciergent.oauth.completed'] is False


async def test_a_push_span_holds_its_turn_and_records_the_outcome(spans, message_store):
    push_executor = executor(message_store, runner=StubRunner('ok'))

    assert await push_executor.run(push_settings(), STARTED_AT) is PushOutcome.delivered

    push = _span(spans, 'push')
    assert push.attributes['conciergent.push.name'] == 'daily-digest'
    assert push.attributes['conciergent.push.outcome'] == 'delivered'
    assert _span(spans, 'turn').parent.span_id == push.context.span_id


def _log_line() -> dict[str, typing.Any]:
    record = logging.LogRecord('conciergent.turn', logging.INFO, __file__, 1, 'Turn done', None, None)
    return json.loads(JsonFormatter().format(record))


def test_a_json_log_line_carries_the_ids_of_the_span_it_is_logged_in(spans):
    assert 'trace_id' not in _log_line()

    with trace.get_tracer('test').start_as_current_span('turn') as span:
        line = _log_line()

    assert line['trace_id'] == trace.format_trace_id(span.get_span_context().trace_id)
    assert line['span_id'] == trace.format_span_id(span.get_span_context().span_id)


def test_a_spec_call_carries_the_turn_trace_to_the_upstream_api(spans, store_config, messages_url, tmp_path):
    pets = PetsApi()

    with (
        serving_gateway(pets, spec_dir=tmp_path, store=store_config, redis_url=messages_url) as app,
        trace.get_tracer('test').start_as_current_span('turn') as span,
    ):
        ask_through_gateway(app)

    trace_id = trace.format_trace_id(span.get_span_context().trace_id)
    traceparents = [headers.get('traceparent', '') for headers in pets.request_headers]
    assert traceparents
    assert all(traceparent.split('-')[1] == trace_id for traceparent in traceparents)


@pytest.fixture
def traced_agents(spans: InMemorySpanExporter) -> typing.Iterator[None]:
    """Undo the process-wide instrumentation ``telemetry.setup`` turns on, so later tests run uninstrumented."""
    fastmcp_mode = fastmcp.settings.telemetry_mode
    yield
    pydantic_ai.Agent.instrument_all(False)
    fastmcp.settings.telemetry_mode = fastmcp_mode


@pytest.mark.parametrize('capture_content', [False, True])
async def test_setup_keeps_the_conversation_out_of_spans_unless_content_capture_is_on(
    spans, traced_agents, capture_content
):
    # The shared test provider is already set, so setup takes it as the host's.
    telemetry.setup(capture_content=capture_content)

    await pydantic_ai.Agent(TestModel()).run('a private question')

    recorded = ' '.join(str(value) for span in spans.get_finished_spans() for value in (span.attributes or {}).values())
    assert ('a private question' in recorded) is capture_content


_SETUP_PROBE = """
import json
import fastmcp
from opentelemetry import trace
from conciergent import telemetry
{before}
telemetry.setup(capture_content=False)
provider = trace.get_tracer_provider()
print(json.dumps({{
    'provider': type(provider).__name__,
    'service': str(getattr(provider, 'resource', None) and provider.resource.attributes.get('service.name')),
    'host_kept': {host_kept},
    'fastmcp_mode': fastmcp.settings.telemetry_mode,
}}))
"""


def _setup_in_a_fresh_process(
    *, before: str = '', host_kept: str = 'None', **environment: str
) -> dict[str, typing.Any]:
    # One tracer provider per process, so each setup runs in its own interpreter.
    probe = _SETUP_PROBE.format(before=before, host_kept=host_kept)
    env = {key: value for key, value in os.environ.items() if not key.startswith('OTEL_')} | environment
    result = subprocess.run([sys.executable, '-c', probe], capture_output=True, text=True, check=True, env=env)
    return json.loads(result.stdout.strip().splitlines()[-1])


def test_setup_exports_over_otlp_as_conciergent_and_lets_the_mcp_sdk_own_mcp_spans():
    result = _setup_in_a_fresh_process()

    assert result['provider'] == 'TracerProvider'
    assert result['service'] == 'conciergent'
    assert result['fastmcp_mode'] == 'propagation_only'


def test_setup_keeps_a_service_name_the_environment_sets():
    assert _setup_in_a_fresh_process(OTEL_SERVICE_NAME='bot')['service'] == 'bot'


def test_setup_leaves_a_tracer_provider_the_host_already_set():
    host = (
        'from opentelemetry.sdk.trace import TracerProvider\nhost = TracerProvider()\ntrace.set_tracer_provider(host)\n'
    )

    assert _setup_in_a_fresh_process(before=host, host_kept='trace.get_tracer_provider() is host')['host_kept'] is True


def test_telemetry_is_off_unless_enabled(store_config):
    config = app_config(store_config)

    assert config.telemetry.enabled is False
    assert config.telemetry.capture_content is False
