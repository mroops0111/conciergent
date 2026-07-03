import typing
import urllib.parse

import fastapi
import httpx
import pytest

from conciergent import ChatSurface
from conciergent.store.credential import CredentialStore
from conciergent.store.message import MessageStore
from conciergent.surfaces.slack import install
from conciergent.surfaces.slack.install import SlackInstallSettings, build_install_router


_SETTINGS = SlackInstallSettings(
    client_id='cid',
    client_secret='csecret',
    scopes=('chat:write', 'im:history'),
    base_url='https://example.com',
)


@pytest.fixture
async def harness(
    message_store: MessageStore, credential_store: CredentialStore
) -> typing.AsyncIterator[tuple[httpx.AsyncClient, CredentialStore]]:
    app = fastapi.FastAPI()
    app.include_router(
        build_install_router(settings=_SETTINGS, message_store=message_store, credential_store=credential_store)
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url='http://test') as client:
        yield client, credential_store


async def test_install_redirects_to_slack_with_state(harness) -> None:
    client, _ = harness
    response = await client.get('/oauth/slack/install')
    assert response.status_code == 307
    location = urllib.parse.urlparse(response.headers['location'])
    query = urllib.parse.parse_qs(location.query)
    assert location.netloc == 'slack.com'
    assert query['client_id'] == ['cid']
    assert query['scope'] == ['chat:write,im:history']
    assert query['redirect_uri'] == ['https://example.com/oauth/slack/callback']
    assert query['state'][0]


async def test_callback_with_unknown_state_fails(harness) -> None:
    client, _ = harness
    response = await client.get('/oauth/slack/callback', params={'code': 'c', 'state': 'forged'})
    assert response.status_code == 400


async def test_callback_stores_the_bot_token(harness, monkeypatch: pytest.MonkeyPatch) -> None:
    client, credential_store = harness

    async def fake_exchange(code: str, **kwargs: typing.Any) -> tuple[str, str, str | None]:
        assert code == 'the-code'
        return 'T77', 'xoxb-77', 'slack:T77:U9'

    monkeypatch.setattr(install, '_exchange_code', fake_exchange)
    state = await _issued_state(client)
    response = await client.get('/oauth/slack/callback', params={'code': 'the-code', 'state': state})
    assert response.status_code == 200
    assert await credential_store.resolve_bot_token(ChatSurface.slack, 'T77') == 'xoxb-77'


async def test_callback_with_error_param_fails(harness) -> None:
    client, _ = harness
    state = await _issued_state(client)
    response = await client.get('/oauth/slack/callback', params={'error': 'access_denied', 'state': state})
    assert response.status_code == 400


async def test_exchange_failure_renders_the_failure_page(harness, monkeypatch: pytest.MonkeyPatch) -> None:
    client, _ = harness

    async def failing_exchange(code: str, **kwargs: typing.Any) -> tuple[str, str, str | None]:
        raise RuntimeError('invalid_code')

    monkeypatch.setattr(install, '_exchange_code', failing_exchange)
    state = await _issued_state(client)
    response = await client.get('/oauth/slack/callback', params={'code': 'stale', 'state': state})
    assert response.status_code == 400


async def test_state_is_single_use(harness, monkeypatch: pytest.MonkeyPatch) -> None:
    client, _ = harness

    async def fake_exchange(code: str, **kwargs: typing.Any) -> tuple[str, str, str | None]:
        return 'T77', 'xoxb-77', None

    monkeypatch.setattr(install, '_exchange_code', fake_exchange)
    state = await _issued_state(client)
    assert (await client.get('/oauth/slack/callback', params={'code': 'c', 'state': state})).status_code == 200
    assert (await client.get('/oauth/slack/callback', params={'code': 'c', 'state': state})).status_code == 400


async def _issued_state(client: httpx.AsyncClient) -> str:
    response = await client.get('/oauth/slack/install')
    location = response.headers['location']
    return urllib.parse.parse_qs(urllib.parse.urlparse(location).query)['state'][0]
