import pydantic
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken

from conciergent import MemoryStore
from conciergent.mcp.storage import OAuthTokenStorage


def _storage(store: MemoryStore) -> OAuthTokenStorage:
    return OAuthTokenStorage(store, server='petstore', principal='slack:T:U')


async def test_tokens_round_trip():
    storage = _storage(MemoryStore())
    assert await storage.get_tokens() is None
    await storage.set_tokens(OAuthToken(access_token='a', token_type='Bearer'))
    loaded = await storage.get_tokens()
    assert loaded is not None
    assert loaded.access_token == 'a'


async def test_client_info_round_trips():
    storage = _storage(MemoryStore())
    assert await storage.get_client_info() is None
    await storage.set_client_info(
        OAuthClientInformationFull(
            client_id='c1', redirect_uris=[pydantic.AnyUrl('https://example.com/oauth/callback')]
        )
    )
    loaded = await storage.get_client_info()
    assert loaded is not None and loaded.client_id == 'c1'


async def test_tokens_are_isolated_per_principal():
    store = MemoryStore()
    await OAuthTokenStorage(store, server='petstore', principal='a').set_tokens(OAuthToken(access_token='a'))
    other = OAuthTokenStorage(store, server='petstore', principal='b')
    assert await other.get_tokens() is None
