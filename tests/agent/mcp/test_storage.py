import time

import pydantic
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken

from conciergent.agent.mcp.storage import OAuthTokenStorage
from conciergent.store.credential import CredentialStore


_SERVER = 'petstore'
_PRINCIPAL = 'slack:T:U'


def _storage(credential_store: CredentialStore) -> OAuthTokenStorage:
    return OAuthTokenStorage(credential_store, server=_SERVER, principal=_PRINCIPAL)


async def test_tokens_round_trip(credential_store: CredentialStore):
    storage = _storage(credential_store)
    access_token = 'a'
    assert await storage.get_tokens() is None

    await storage.set_tokens(OAuthToken(access_token=access_token, token_type='Bearer'))

    loaded = await storage.get_tokens()
    assert loaded is not None
    assert loaded.access_token == access_token


async def test_set_tokens_persists_an_absolute_expiry(credential_store: CredentialStore):
    storage = _storage(credential_store)
    await storage.set_tokens(OAuthToken(access_token='a', token_type='Bearer', expires_in=3600))

    tokens, expires_at = await storage.get_tokens_with_expiry()
    assert tokens is not None and tokens.access_token == 'a'
    assert expires_at is not None and expires_at > time.time() + 3000


async def test_expiry_is_absent_when_the_token_has_no_lifetime(credential_store: CredentialStore):
    storage = _storage(credential_store)
    await storage.set_tokens(OAuthToken(access_token='a', token_type='Bearer'))

    _, expires_at = await storage.get_tokens_with_expiry()
    assert expires_at is None


async def test_client_info_round_trips(credential_store: CredentialStore):
    storage = _storage(credential_store)
    client_id = 'c1'
    assert await storage.get_client_info() is None

    await storage.set_client_info(
        OAuthClientInformationFull(
            client_id=client_id, redirect_uris=[pydantic.AnyUrl('https://example.com/oauth/callback')]
        )
    )

    loaded = await storage.get_client_info()
    assert loaded is not None and loaded.client_id == client_id


async def test_delete_tokens_signs_the_principal_out(credential_store: CredentialStore):
    storage = _storage(credential_store)
    await storage.set_tokens(OAuthToken(access_token='a', token_type='Bearer'))

    await storage.delete_tokens()

    assert await storage.get_tokens() is None


async def test_delete_tokens_leaves_other_principals(credential_store: CredentialStore):
    await OAuthTokenStorage(credential_store, server=_SERVER, principal='a').set_tokens(OAuthToken(access_token='a'))
    other = OAuthTokenStorage(credential_store, server=_SERVER, principal='b')
    await other.set_tokens(OAuthToken(access_token='b'))

    await OAuthTokenStorage(credential_store, server=_SERVER, principal='a').delete_tokens()

    assert await other.get_tokens() is not None


async def test_tokens_are_isolated_per_principal(credential_store: CredentialStore):
    await OAuthTokenStorage(credential_store, server=_SERVER, principal='a').set_tokens(OAuthToken(access_token='a'))
    other = OAuthTokenStorage(credential_store, server=_SERVER, principal='b')

    assert await other.get_tokens() is None
