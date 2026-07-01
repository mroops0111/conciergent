import time

import pytest
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from pydantic import AnyUrl

from conciergent import MemoryStore
from conciergent.mcp.storage import MCPTokenStorage


def _client_info() -> OAuthClientInformationFull:
    return OAuthClientInformationFull(client_id='cid', redirect_uris=[AnyUrl('https://example.test/cb')])


def _token(expires_in: int | None = 3600) -> OAuthToken:
    return OAuthToken(access_token='at', token_type='Bearer', expires_in=expires_in, refresh_token='rt')


def _storage() -> tuple[MCPTokenStorage, MemoryStore]:
    store = MemoryStore()
    return MCPTokenStorage(store, server='https://mcp.test/x', principal='slack:T:U'), store


async def test_client_info_round_trip():
    storage, _ = _storage()
    assert await storage.get_client_info() is None
    await storage.set_client_info(_client_info())
    loaded = await storage.get_client_info()
    assert loaded is not None
    assert loaded.client_id == 'cid'


async def test_set_tokens_requires_client_info_first():
    storage, _ = _storage()
    with pytest.raises(RuntimeError):
        await storage.set_tokens(_token())


async def test_token_round_trip_and_absolute_expiry():
    storage, _ = _storage()
    await storage.set_client_info(_client_info())
    before = time.time()
    await storage.set_tokens(_token(expires_in=3600))

    tokens, expires_at = await storage.get_tokens_with_expiry()
    assert tokens is not None
    assert tokens.access_token == 'at'
    assert tokens.refresh_token == 'rt'
    assert expires_at is not None
    assert before + 3600 <= expires_at <= time.time() + 3600

    # get_tokens drops the expiry but still validates cleanly (no leaked _expires_at key).
    plain = await storage.get_tokens()
    assert plain is not None
    assert plain.access_token == 'at'


async def test_token_without_lifetime_has_no_expiry():
    storage, _ = _storage()
    await storage.set_client_info(_client_info())
    await storage.set_tokens(_token(expires_in=None))
    tokens, expires_at = await storage.get_tokens_with_expiry()
    assert tokens is not None
    assert expires_at is None


async def test_scoping_is_per_server_and_principal():
    store = MemoryStore()
    a = MCPTokenStorage(store, server='s1', principal='p1')
    b = MCPTokenStorage(store, server='s1', principal='p2')
    await a.set_client_info(_client_info())
    assert await b.get_client_info() is None
