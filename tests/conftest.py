import typing

import pytest
import redis.asyncio
from sqlalchemy.ext.asyncio import create_async_engine
from testcontainers.postgres import PostgresContainer
from testcontainers.redis import RedisContainer

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
