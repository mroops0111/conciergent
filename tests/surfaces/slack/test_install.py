import typing
import urllib.parse

import pytest

from conciergent import ChatSurface
from conciergent.surfaces.slack import install
from tests.surfaces.slack.conftest import INSTALL_SETTINGS, InstallHarness


def _patch_exchange(
    monkeypatch: pytest.MonkeyPatch,
    *,
    result: tuple[str, str, str, str | None] = ('T77', 'Acme Inc', 'xoxb-77', 'slack:T77:U9'),
    error: Exception | None = None,
) -> None:
    async def _exchange(code: str, **kwargs: typing.Any) -> tuple[str, str, str, str | None]:
        if error is not None:
            raise error
        return result

    monkeypatch.setattr(install, '_exchange_code', _exchange)


async def test_install_redirects_to_slack_with_state(install_harness: InstallHarness) -> None:
    response = await install_harness.client.get('/oauth/slack/install')

    assert response.status_code == 307
    location = urllib.parse.urlparse(response.headers['location'])
    query = urllib.parse.parse_qs(location.query)
    assert location.netloc == 'slack.com'
    assert query['client_id'] == [INSTALL_SETTINGS.client_id]
    assert query['scope'] == [','.join(INSTALL_SETTINGS.scopes)]
    assert query['redirect_uri'] == [f'{INSTALL_SETTINGS.base_url}/oauth/slack/callback']
    assert query['state'][0]


async def test_callback_stores_the_bot_token(install_harness: InstallHarness, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_exchange(monkeypatch)
    state = await install_harness.issued_state()

    response = await install_harness.client.get('/oauth/slack/callback', params={'code': 'the-code', 'state': state})

    assert response.status_code == 200
    assert await install_harness.credential_store.resolve_bot_token(ChatSurface.slack, 'T77') == 'xoxb-77'


async def test_callback_with_unknown_state_fails(install_harness: InstallHarness) -> None:
    response = await install_harness.client.get('/oauth/slack/callback', params={'code': 'c', 'state': 'forged'})

    assert response.status_code == 400


async def test_callback_with_error_param_fails(install_harness: InstallHarness) -> None:
    state = await install_harness.issued_state()

    response = await install_harness.client.get(
        '/oauth/slack/callback', params={'error': 'access_denied', 'state': state}
    )

    assert response.status_code == 400


async def test_exchange_failure_renders_the_failure_page(
    install_harness: InstallHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_exchange(monkeypatch, error=RuntimeError('invalid_code'))
    state = await install_harness.issued_state()

    response = await install_harness.client.get('/oauth/slack/callback', params={'code': 'stale', 'state': state})

    assert response.status_code == 400


async def test_state_is_single_use(install_harness: InstallHarness, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_exchange(monkeypatch, result=('T77', 'Acme Inc', 'xoxb-77', None))
    state = await install_harness.issued_state()

    first = await install_harness.client.get('/oauth/slack/callback', params={'code': 'c', 'state': state})
    second = await install_harness.client.get('/oauth/slack/callback', params={'code': 'c', 'state': state})

    assert first.status_code == 200
    assert second.status_code == 400
