import asyncio
import json
import logging
import os
import subprocess
import sys
import typing

import fastapi
import pydantic_ai
import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from pydantic_ai.mcp import MCPToolset
from pydantic_ai.models.test import TestModel

from conciergent import App, PendingApproval, run_turn
from conciergent.chat_context import DirectChatContext
from conciergent.config import build_app_config
from conciergent.logger import JsonFormatter
from conciergent.push.executor import PushExecutor
from conciergent.push.outcome import PushOutcome
from conciergent.runtime import OAuthHandoffExpiredError, StatefulOAuthBridge
from conciergent.store.message import MessageStore
from tests.conftest import RecordingSurface, StubRunner, free_port, make_card, serving
from tests.push.conftest import STARTED_AT, RecordingPushSurface, push_settings


_PRINCIPAL = 'line:U1'
_CONVERSATION = 'line:user:U1'


@pytest.fixture
def store_config(messages_url: str, credentials_url: str) -> dict[str, str]:
    return {'messages_url': messages_url, 'credentials_url': credentials_url}


def _span(spans: InMemorySpanExporter, name: str) -> typing.Any:
    (found,) = [span for span in spans.get_finished_spans() if span.name == name]
    return found


async def _turn(output: typing.Any, message_store: MessageStore) -> None:
    await run_turn(
        'hi',
        principal=_PRINCIPAL,
        chat_context=DirectChatContext(conversation=_CONVERSATION),
        runner=StubRunner(output).as_runner(),
        surface=RecordingSurface(),
        message_store=message_store,
    )


async def test_a_turn_is_one_span_naming_its_chat_and_how_it_ended(spans, message_store):
    await _turn('hello', message_store)

    turn = _span(spans, 'turn')
    assert turn.attributes['conciergent.conversation'] == _CONVERSATION
    assert turn.attributes['conciergent.chat'] == 'DirectChatContext'
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
    surface = RecordingPushSurface()
    executor = PushExecutor(
        surfaces={surface.name: surface}, runner=StubRunner('ok').as_runner(), message_store=message_store
    )

    assert await executor.run(push_settings(), STARTED_AT) is PushOutcome.delivered

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


def test_a_bearer_spec_call_carries_the_turn_trace_to_the_upstream_api(spans, store_config, messages_url, tmp_path):
    upstream = fastapi.FastAPI()
    traceparents: list[str | None] = []

    @upstream.get('/pets', operation_id='listPets')
    def list_pets(traceparent: str | None = fastapi.Header(default=None)) -> list[dict[str, str]]:
        traceparents.append(traceparent)
        return [{'name': 'Rex'}]

    spec = tmp_path / 'pets.json'
    spec.write_text(json.dumps(upstream.openapi()))
    with serving(upstream, free_port()) as upstream_url:
        port = free_port()
        config = build_app_config(
            {
                'agent': {'model': 'test', 'system_prompt': 'x'},
                'store': store_config,
                'surface': {'slack': {'enabled': True, 'signing_secret': 'sek'}},
                'gateway': {
                    'enabled': True,
                    'redis_url': messages_url,
                    'specs': [{'name': 'pets', 'spec': str(spec), 'base_url': upstream_url}],
                },
                'server': {'host': '127.0.0.1', 'port': port},
            }
        )
        app = App.from_app_config(config)
        with serving(app.build_asgi(), port):
            (server,) = app._runner.mcp_servers
            agent = pydantic_ai.Agent(TestModel(), toolsets=[MCPToolset(server)])
            with trace.get_tracer('test').start_as_current_span('turn') as span:
                agent.run_sync('list the pets')

    trace_id = trace.format_trace_id(span.get_span_context().trace_id)
    assert traceparents and all(value is not None and value.split('-')[1] == trace_id for value in traceparents)


_SETUP_PROBE = """
import json
import fastmcp
from opentelemetry import trace
from pydantic_ai import Agent
from conciergent import telemetry
{before}
telemetry.setup(capture_content=False)
provider = trace.get_tracer_provider()
print(json.dumps({{
    'provider': type(provider).__name__,
    'service': str(getattr(provider, 'resource', None) and provider.resource.attributes.get('service.name')),
    'host_kept': {host_kept},
    'include_content': Agent._instrument_default.include_content,
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


def test_setup_exports_over_otlp_as_conciergent_and_keeps_content_out():
    result = _setup_in_a_fresh_process()

    assert result['provider'] == 'TracerProvider'
    assert result['service'] == 'conciergent'
    assert result['include_content'] is False
    assert result['fastmcp_mode'] == 'propagation_only'


def test_setup_keeps_a_service_name_the_environment_sets():
    assert _setup_in_a_fresh_process(OTEL_SERVICE_NAME='bot')['service'] == 'bot'


def test_setup_leaves_a_tracer_provider_the_host_already_set():
    host = (
        'from opentelemetry.sdk.trace import TracerProvider\nhost = TracerProvider()\ntrace.set_tracer_provider(host)\n'
    )

    assert _setup_in_a_fresh_process(before=host, host_kept='trace.get_tracer_provider() is host')['host_kept'] is True


def test_telemetry_is_off_unless_enabled(store_config):
    config = build_app_config({'agent': {'model': 'test', 'system_prompt': 'x'}, 'store': store_config})

    assert config.telemetry.enabled is False
    assert config.telemetry.capture_content is False
