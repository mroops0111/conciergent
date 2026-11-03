import contextlib
import json
import pathlib
import socket
import tempfile
import threading
import time
import typing

import fastapi
import pydantic_ai
import pytest
import redis.asyncio
import uvicorn
from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from pydantic_ai.mcp import MCPToolset
from pydantic_ai.models.test import TestModel
from sqlalchemy.ext.asyncio import create_async_engine
from testcontainers.postgres import PostgresContainer
from testcontainers.redis import RedisContainer

from conciergent import App, AppConfig, run_turn
from conciergent.agent.runner import ChatRunner
from conciergent.chart import ChartPublisher, ChartRenderer, ChartStyle, DefaultChartStyle
from conciergent.chat_context import ChatContext, DirectChatContext, GroupChatContext
from conciergent.config import build_app_config
from conciergent.defaults import defaults_layer
from conciergent.i18n.lang import Lang
from conciergent.reply import Card, Fact, Link, ReplySurface, Section, Suggestion
from conciergent.runtime import TurnResult
from conciergent.store.chart import ChartStore
from conciergent.store.credential import Base, CredentialStore
from conciergent.store.message import MessageStore


# The public URL the chart fixtures serve charts from.
CHART_BASE_URL = 'https://bot.example.com'
CHART_FONT = defaults_layer()['capability']['chart']['font']
# Collects the spans of every test, behind the one tracer provider the spans fixture sets for the session.
_SPAN_EXPORTER = InMemorySpanExporter()


class RecordingSurface(ReplySurface):
    """A reply surface that records every call, in an optional language, for asserting on what a turn sent."""

    def __init__(self, lang: Lang | None = None) -> None:
        self._lang = lang
        self.calls: list[tuple[str, typing.Any]] = []

    @property
    def lang(self) -> Lang | None:
        return self._lang

    async def send_text(self, text: str) -> None:
        self.calls.append(('text', text))

    async def send_card(self, card: Card, *, destructive: bool = False) -> None:
        self.calls.append(('card', (card, destructive)))

    async def send_carousel(self, cards: list[Card]) -> None:
        self.calls.append(('carousel', cards))

    async def show_processing(self) -> None:
        self.calls.append(('processing', None))

    async def acknowledge_silently(self) -> None:
        self.calls.append(('acknowledged', None))


class StubRunner:
    """Stands in for ChatRunner, recording each call. It echoes the input back unless a test scripts the output."""

    def __init__(
        self,
        output: typing.Any = None,
        *,
        new_history: list[typing.Any] | None = None,
        invalidate_history: bool = False,
    ) -> None:
        self.output = output
        self.new_history = new_history
        self.invalidate_history = invalidate_history
        self.credential_free = True
        self.credential_free_checks = 0
        self.bootstrap_result = False
        self.bootstrapped: list[str] = []
        self.inputs: list[str] = []
        # One entry per run, the keyword arguments a test may want to inspect beyond the input.
        self.calls: list[dict[str, typing.Any]] = []

    def as_runner(self) -> ChatRunner:
        # Callers only use the methods below, so the stand-in is cast to the concrete runner type.
        return typing.cast(ChatRunner, self)

    async def works_without_user_credentials(self) -> bool:
        self.credential_free_checks += 1
        return self.credential_free

    async def bootstrap(self, principal: str, *, bridge: typing.Any = None) -> bool:
        self.bootstrapped.append(principal)
        return self.bootstrap_result

    async def run(
        self,
        user_input: str,
        *,
        principal: str,
        history: list[typing.Any],
        pending_approval: dict[str, typing.Any] | None,
        bridge: typing.Any = None,
        surface: typing.Any = None,
        chat_context: ChatContext,
    ) -> TurnResult:
        self.inputs.append(user_input)
        self.calls.append(
            {
                'principal': principal,
                'bridge': bridge,
                'chat_context': chat_context,
                'speaker': chat_context.speaker if isinstance(chat_context, GroupChatContext) else None,
                'pending_approval': pending_approval,
                'history': history,
            }
        )
        output = self.output if self.output is not None else f'echo {user_input}'
        new_history = self.new_history if self.new_history is not None else [{'seen': user_input}]
        return TurnResult(output=output, history=new_history, invalidate_history=self.invalidate_history)


class PetsApi:
    """A stub upstream API with one listPets operation, recording the headers of each request it receives."""

    def __init__(self) -> None:
        self.request_headers: list[dict[str, str]] = []
        self.app = fastapi.FastAPI()

        @self.app.get('/pets', operation_id='listPets')
        def list_pets(request: fastapi.Request) -> list[dict[str, str]]:
            self.request_headers.append(dict(request.headers))
            return [{'name': 'Rex'}]


# One container serves the whole session.
# Each test is isolated by a flush in Redis or a fresh schema in Postgres.


@pytest.fixture(scope='session')
def messages_url() -> typing.Iterator[str]:
    with RedisContainer('redis:7') as container:
        host = container.get_container_host_ip()
        port = container.get_exposed_port(6379)
        yield f'redis://{host}:{port}/0'


@pytest.fixture(scope='session')
def credentials_url() -> typing.Iterator[str]:
    with PostgresContainer('postgres:16-alpine', driver='asyncpg') as container:
        yield container.get_connection_url()


@pytest.fixture
def spans() -> typing.Iterator[InMemorySpanExporter]:
    """The spans finished during the test, from a tracer provider shared by the whole session.

    OpenTelemetry takes one global tracer provider per process, so it is set once and only cleared between tests.
    """
    if not isinstance(trace.get_tracer_provider(), TracerProvider):
        provider = TracerProvider()
        provider.add_span_processor(SimpleSpanProcessor(_SPAN_EXPORTER))
        trace.set_tracer_provider(provider)
    _SPAN_EXPORTER.clear()
    yield _SPAN_EXPORTER
    _SPAN_EXPORTER.clear()


@pytest.fixture
def control_socket() -> typing.Iterator[str]:
    """A path for a control socket, kept short since macOS caps a Unix socket path near 104 bytes."""
    with tempfile.TemporaryDirectory(dir='/tmp') as directory:
        yield str(pathlib.Path(directory) / 'control.sock')


@pytest.fixture
def store_config(messages_url: str, credentials_url: str) -> dict[str, str]:
    """The store section of a config, pointing at the session's Redis and Postgres containers."""
    return {'messages_url': messages_url, 'credentials_url': credentials_url}


@pytest.fixture
async def message_store(messages_url: str) -> typing.AsyncIterator[MessageStore]:
    client = redis.asyncio.Redis.from_url(messages_url)
    await client.flushdb()
    try:
        yield MessageStore(client)
    finally:
        await client.aclose()


@pytest.fixture
async def credential_store(credentials_url: str) -> typing.AsyncIterator[CredentialStore]:
    engine = create_async_engine(credentials_url)
    # A fresh schema per test keeps credential rows from leaking between tests.
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
        await connection.run_sync(Base.metadata.create_all)
    try:
        yield CredentialStore(engine)
    finally:
        await engine.dispose()


@pytest.fixture
async def chart_store(messages_url: str) -> typing.AsyncIterator[ChartStore]:
    client = redis.asyncio.Redis.from_url(messages_url)
    await client.flushdb()
    try:
        yield ChartStore(client, ttl_seconds=60)
    finally:
        await client.aclose()


@pytest.fixture
def chart_publisher(chart_store: ChartStore) -> ChartPublisher:
    return ChartPublisher(chart_renderer(), chart_store, base_url=CHART_BASE_URL)


def chart_spec(**overrides: typing.Any) -> dict[str, typing.Any]:
    """A small Vega-Lite bar chart with inline rows, which ``overrides`` replace key by key."""
    return {
        'data': {'values': [{'day': 'Mon', 'count': 3}, {'day': 'Tue', 'count': 5}]},
        'mark': 'bar',
        'encoding': {
            'x': {'field': 'day', 'type': 'nominal'},
            'y': {'field': 'count', 'type': 'quantitative'},
        },
        **overrides,
    }


def chart_renderer(*, style: ChartStyle | None = None, max_rows: int = 100) -> ChartRenderer:
    return ChartRenderer(style or DefaultChartStyle(font=CHART_FONT), max_rows=max_rows)


def make_card(**overrides: typing.Any) -> Card:
    """A card with one line of text, which ``overrides`` replace field by field."""
    fields: dict[str, typing.Any] = {'header': 'Tasks', 'sections': [Section(text='Body')], **overrides}
    return Card(**fields)


def tasks_card() -> Card:
    """A card with text, bullets, two links, a suggestion, and a footnote, for checking where each part lands."""
    return make_card(
        sections=[Section(text='You have two tasks.', bullets=['Review NDA', 'Sign lease'])],
        links=[Link(label='Open', url='https://example.com/tasks'), Link(label='Docs', url='https://example.com/docs')],
        suggestions=[Suggestion(label='More', prompt='List more tasks')],
        footnote='Active only',
    )


def digest_card() -> Card:
    """A report card with a subtitle, a titled section of facts, and a titled section of bullets."""
    return make_card(
        header='Daily digest',
        subtitle='Yesterday',
        sections=[
            Section(title='Totals', facts=[Fact(label='Orders', value='42'), Fact(label='Revenue', value='$12,300')]),
            Section(title='Anomalies', bullets=['#A1023 is negative']),
        ],
    )


def card_shape(**overrides: typing.Any) -> dict[str, typing.Any]:
    """A card shape as a manifest declares it, which ``overrides`` replace key by key.

    It has a templated header, a facts section with a required and an optional label, and an optional bullets section.
    """
    return {
        'header': 'Daily summary for {store}',
        'placeholders': {'store': {'description': "The store's name."}},
        'sections': [
            {'name': 'totals', 'title': 'Totals', 'labels': ['Orders', {'label': 'Refunds', 'required': False}]},
            {'name': 'anomalies', 'title': 'Anomalies', 'content': 'bullets', 'required': False},
        ],
        **overrides,
    }


def destructive_server(calls: list[int]) -> MCPServer:
    """An in-process MCP server whose one tool is marked destructive, so calling it parks an approval first."""
    server = MCPServer('test')

    @server.tool(annotations=ToolAnnotations(destructive_hint=True))
    def delete_it(x: int) -> str:
        calls.append(x)
        return 'deleted'

    return server


def free_port() -> int:
    """A TCP port on loopback that nothing listens on right now."""
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        return probe.getsockname()[1]


@contextlib.contextmanager
def serving(asgi: fastapi.FastAPI, port: int) -> typing.Iterator[str]:
    """Serve ``asgi`` on ``port`` in a background thread until the block exits, yielding its URL."""
    server = uvicorn.Server(uvicorn.Config(asgi, host='127.0.0.1', port=port, log_config=None))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 10
        while not server.started:
            if time.monotonic() > deadline or not thread.is_alive():
                raise RuntimeError(f'the test server on port {port} did not start')
            time.sleep(0.05)
        yield f'http://127.0.0.1:{port}'
    finally:
        server.should_exit = True
        thread.join(timeout=10)


def app_config(store: dict[str, str], **sections: typing.Any) -> AppConfig:
    """A validated config with a placeholder agent, the given store, and ``sections`` merged over the defaults."""
    agent = {'model': 'test', 'system_prompt': 'x', **sections.pop('agent', {})}
    return build_app_config({'agent': agent, 'store': store, **sections})


async def drive_turn(
    user_input: str,
    runner: StubRunner,
    message_store: MessageStore,
    *,
    principal: str = 'slack:T:U',
    chat_context: ChatContext | None = None,
) -> RecordingSurface:
    """Run one turn through ``runner``, in a direct chat with ``principal`` unless ``chat_context`` names another."""
    surface = RecordingSurface()
    await run_turn(
        user_input,
        principal=principal,
        chat_context=chat_context or DirectChatContext(conversation=principal),
        runner=runner.as_runner(),
        surface=surface,
        message_store=message_store,
    )
    return surface


@contextlib.contextmanager
def serving_gateway(
    pets: PetsApi,
    *,
    spec_dir: pathlib.Path,
    store: dict[str, str],
    redis_url: str,
    spec_auth: dict[str, typing.Any] | None = None,
    server: dict[str, typing.Any] | None = None,
) -> typing.Iterator[App]:
    """Serve ``pets`` and an app that embeds it as a gateway spec, yielding the app while both are up."""
    spec = spec_dir / 'pets.json'
    spec.write_text(json.dumps(pets.app.openapi()))
    with serving(pets.app, free_port()) as upstream_url:
        port = free_port()
        spec_config: dict[str, typing.Any] = {'name': 'pets', 'spec': str(spec), 'base_url': upstream_url}
        if spec_auth is not None:
            spec_config['auth'] = spec_auth
        config = app_config(
            store,
            surface={'slack': {'enabled': True, 'signing_secret': 'sek'}},
            gateway={'enabled': True, 'redis_url': redis_url, 'specs': [spec_config]},
            server={'host': '127.0.0.1', 'port': port, **(server or {})},
        )
        app = App.from_app_config(config)
        with serving(app.build_asgi(), port):
            yield app


def ask_through_gateway(app: App) -> str:
    """Have a test agent call every tool of ``app``'s one embedded spec, returning the agent's output."""
    (server,) = app._runner.mcp_servers
    agent = pydantic_ai.Agent(TestModel(), toolsets=[MCPToolset(server)])
    return agent.run_sync('list the pets').output
