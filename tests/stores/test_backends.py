import asyncio
import typing

import fakeredis.aioredis
import pytest

from conciergent import MemoryStore, Store
from conciergent.stores.composite import CompositeStore
from conciergent.stores.postgres import PostgresStore
from conciergent.stores.redis import RedisStore


def _memory_store() -> Store:
    return MemoryStore()


def _redis_store() -> Store:
    return RedisStore(fakeredis.aioredis.FakeRedis())


def _postgres_store() -> Store:
    # The SQL backend runs the same code on sqlite in tests, only the engine URL differs.
    return PostgresStore.from_url('sqlite+aiosqlite://')


def _composite_store() -> Store:
    # The reference production layout, expiring messages on Redis and durable credentials in SQL.
    return CompositeStore(messages=_redis_store(), credentials=_postgres_store())


@pytest.fixture(
    params=[_memory_store, _redis_store, _postgres_store, _composite_store],
    ids=['memory', 'redis', 'postgres', 'composite'],
)
async def store(request: pytest.FixtureRequest) -> Store:
    built = typing.cast(Store, request.param())
    await built.prepare()
    return built


async def test_history_round_trips(store: Store):
    assert await store.load_history('p') == []
    await store.append_history('p', [{'a': 1}], ttl_seconds=60)
    await store.append_history('p', [{'b': 2}], ttl_seconds=60)
    assert await store.load_history('p') == [{'a': 1}, {'b': 2}]


async def test_history_is_isolated_per_principal(store: Store):
    await store.append_history('a', ['x'], ttl_seconds=60)
    assert await store.load_history('b') == []


async def test_replace_history_collapses_all_turns(store: Store):
    await store.append_history('p', [1], ttl_seconds=60)
    await store.append_history('p', [2], ttl_seconds=60)
    await store.replace_history('p', ['summary', 2], ttl_seconds=60)
    assert await store.load_history('p') == ['summary', 2]


async def test_history_keeps_only_recent_turns(store: Store):
    for turn in range(12):
        await store.append_history('p', [turn], ttl_seconds=60)
    assert await store.load_history('p') == list(range(2, 12))


async def test_dedupe_reports_repeats(store: Store):
    assert await store.dedupe('e1', ttl_seconds=60) is False
    assert await store.dedupe('e1', ttl_seconds=60) is True
    assert await store.dedupe('e2', ttl_seconds=60) is False


async def test_approval_is_taken_once(store: Store):
    assert await store.take_approval('p') is None
    await store.park_approval('p', {'k': 'v'}, ttl_seconds=60)
    assert await store.take_approval('p') == {'k': 'v'}
    assert await store.take_approval('p') is None


async def test_mcp_token_round_trips(store: Store):
    assert await store.get_mcp_token('https://example.com/mcp', 'u1') is None
    await store.set_mcp_token('https://example.com/mcp', 'u1', {'access_token': 'a'})
    assert await store.get_mcp_token('https://example.com/mcp', 'u1') == {'access_token': 'a'}
    assert await store.get_mcp_token('https://example.com/mcp', 'u2') is None


async def test_mcp_client_round_trips(store: Store):
    await store.set_mcp_client('https://example.com/mcp', {'client_id': 'c'})
    assert await store.get_mcp_client('https://example.com/mcp') == {'client_id': 'c'}


async def test_bot_token_round_trips(store: Store):
    assert await store.resolve_bot_token('slack', 'T1') is None
    await store.set_bot_token('slack', 'T1', 'xoxb-1')
    assert await store.resolve_bot_token('slack', 'T1') == 'xoxb-1'


async def test_oauth_code_reaches_the_waiter(store: Store):
    async def deliver() -> None:
        await asyncio.sleep(0.05)
        await store.deliver_oauth_code('s1', 'code-1')

    task = asyncio.create_task(deliver())
    assert await store.await_oauth_code('s1', timeout_seconds=5) == 'code-1'
    await task


async def test_oauth_code_wait_times_out(store: Store):
    assert await store.await_oauth_code('nobody', timeout_seconds=0.3) is None


async def test_oauth_code_zero_timeout_checks_once(store: Store):
    assert await store.await_oauth_code('nobody', timeout_seconds=0) is None
    await store.deliver_oauth_code('ready', 'code-r')
    assert await store.await_oauth_code('ready', timeout_seconds=0) == 'code-r'
