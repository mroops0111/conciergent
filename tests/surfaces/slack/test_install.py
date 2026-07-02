import typing
import urllib.parse

import fastapi
import fastapi.testclient
import pytest

from conciergent import ChatSurface, MemoryStore
from conciergent.surfaces.slack import install
from conciergent.surfaces.slack.install import SlackInstallSettings, build_install_router


_SETTINGS = SlackInstallSettings(
    client_id='cid',
    client_secret='csecret',
    scopes=('chat:write', 'im:history'),
    base_url='https://example.com',
)


@pytest.fixture
def harness() -> tuple[fastapi.testclient.TestClient, MemoryStore]:
    store = MemoryStore()
    app = fastapi.FastAPI()
    app.include_router(build_install_router(settings=_SETTINGS, store=store))
    return fastapi.testclient.TestClient(app, follow_redirects=False), store


def test_install_redirects_to_slack_with_state(harness) -> None:
    client, _ = harness
    response = client.get('/oauth/slack/install')
    assert response.status_code == 307
    location = urllib.parse.urlparse(response.headers['location'])
    query = urllib.parse.parse_qs(location.query)
    assert location.netloc == 'slack.com'
    assert query['client_id'] == ['cid']
    assert query['scope'] == ['chat:write,im:history']
    assert query['redirect_uri'] == ['https://example.com/oauth/slack/callback']
    assert query['state'][0]


def test_callback_with_unknown_state_fails(harness) -> None:
    client, _ = harness
    response = client.get('/oauth/slack/callback', params={'code': 'c', 'state': 'forged'})
    assert response.status_code == 400


def test_callback_stores_the_bot_token(harness, monkeypatch: pytest.MonkeyPatch) -> None:
    client, store = harness

    async def fake_exchange(code: str, **kwargs: typing.Any) -> tuple[str, str]:
        assert code == 'the-code'
        return 'T77', 'xoxb-77'

    monkeypatch.setattr(install, '_exchange_code', fake_exchange)
    state = _issued_state(client)
    response = client.get('/oauth/slack/callback', params={'code': 'the-code', 'state': state})
    assert response.status_code == 200
    import asyncio

    assert asyncio.run(store.resolve_bot_token(ChatSurface.slack, 'T77')) == 'xoxb-77'


def test_state_is_single_use(harness, monkeypatch: pytest.MonkeyPatch) -> None:
    client, _ = harness

    async def fake_exchange(code: str, **kwargs: typing.Any) -> tuple[str, str]:
        return 'T77', 'xoxb-77'

    monkeypatch.setattr(install, '_exchange_code', fake_exchange)
    state = _issued_state(client)
    assert client.get('/oauth/slack/callback', params={'code': 'c', 'state': state}).status_code == 200
    assert client.get('/oauth/slack/callback', params={'code': 'c', 'state': state}).status_code == 400


def _issued_state(client: fastapi.testclient.TestClient) -> str:
    location = client.get('/oauth/slack/install').headers['location']
    return urllib.parse.parse_qs(urllib.parse.urlparse(location).query)['state'][0]
