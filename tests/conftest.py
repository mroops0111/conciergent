import typing

import pytest
import redis.asyncio
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from sqlalchemy.ext.asyncio import create_async_engine
from testcontainers.postgres import PostgresContainer
from testcontainers.redis import RedisContainer

from conciergent.agent.runner import ChatRunner
from conciergent.i18n.lang import Lang
from conciergent.reply import Card, ReplySurface
from conciergent.runtime import TurnResult
from conciergent.store.credential import Base, CredentialStore
from conciergent.store.message import MessageStore


# One container per session; per-test isolation is a flush (Redis) or a fresh schema (Postgres).


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


def destructive_server(calls: list[int]) -> FastMCP:
    """An in-process MCP server whose one tool is marked destructive, so calling it parks an approval first."""
    server = FastMCP('test')

    @server.tool(annotations=ToolAnnotations(destructiveHint=True))
    def delete_it(x: int) -> str:
        calls.append(x)
        return 'deleted'

    return server


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
        self.groups_supported = True
        self.group_checks = 0
        self.bootstrap_result = False
        self.bootstrapped: list[str] = []
        self.inputs: list[str] = []
        # One entry per run, the keyword arguments a test may want to inspect beyond the input.
        self.calls: list[dict[str, typing.Any]] = []

    def as_runner(self) -> ChatRunner:
        # Callers only use the methods below, so the stand-in is cast to the concrete runner type.
        return typing.cast(ChatRunner, self)

    async def supports_groups(self) -> bool:
        self.group_checks += 1
        return self.groups_supported

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
        group: typing.Any = None,
    ) -> TurnResult:
        self.inputs.append(user_input)
        self.calls.append(
            {
                'principal': principal,
                'bridge': bridge,
                'group': group,
                'speaker': group.speaker if group is not None else None,
                'pending_approval': pending_approval,
                'history': history,
            }
        )
        output = self.output if self.output is not None else f'echo {user_input}'
        new_history = self.new_history if self.new_history is not None else [{'seen': user_input}]
        return TurnResult(output=output, history=new_history, invalidate_history=self.invalidate_history)
