from conciergent.store.credential import CredentialStore


_SERVER = 'https://example.com/mcp'


async def test_mcp_token_round_trips(credential_store: CredentialStore):
    assert await credential_store.get_mcp_token(_SERVER, 'u1') is None

    await credential_store.set_mcp_token(_SERVER, 'u1', {'access_token': 'a'})

    assert await credential_store.get_mcp_token(_SERVER, 'u1') == {'access_token': 'a'}
    assert await credential_store.get_mcp_token(_SERVER, 'u2') is None


async def test_mcp_client_round_trips(credential_store: CredentialStore):
    await credential_store.set_mcp_client(_SERVER, {'client_id': 'c'})

    assert await credential_store.get_mcp_client(_SERVER) == {'client_id': 'c'}


async def test_bot_token_round_trips(credential_store: CredentialStore):
    assert await credential_store.resolve_bot_token('slack', 'T1') is None

    await credential_store.set_bot_token('slack', 'T1', 'xoxb-1')

    assert await credential_store.resolve_bot_token('slack', 'T1') == 'xoxb-1'
