import pydantic
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken

from conciergent.agent.mcp.storage import OAuthTokenStorage
from conciergent.store.credential import CredentialStore


def _storage(credential_store: CredentialStore) -> OAuthTokenStorage:
    return OAuthTokenStorage(credential_store, server='petstore', principal='slack:T:U')


async def test_tokens_round_trip(credential_store: CredentialStore):
    storage = _storage(credential_store)
    assert await storage.get_tokens() is None
    await storage.set_tokens(OAuthToken(access_token='a', token_type='Bearer'))
    loaded = await storage.get_tokens()
    assert loaded is not None
    assert loaded.access_token == 'a'


async def test_client_info_round_trips(credential_store: CredentialStore):
    storage = _storage(credential_store)
    assert await storage.get_client_info() is None
    await storage.set_client_info(
        OAuthClientInformationFull(
            client_id='c1', redirect_uris=[pydantic.AnyUrl('https://example.com/oauth/callback')]
        )
    )
    loaded = await storage.get_client_info()
    assert loaded is not None and loaded.client_id == 'c1'


async def test_tokens_are_isolated_per_principal(credential_store: CredentialStore):
    await OAuthTokenStorage(credential_store, server='petstore', principal='a').set_tokens(OAuthToken(access_token='a'))
    other = OAuthTokenStorage(credential_store, server='petstore', principal='b')
    assert await other.get_tokens() is None
