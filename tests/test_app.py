import asyncio
import builtins
import typing

import fastapi
import fastapi.testclient
import pytest
import yaml

from conciergent import App, AppConfig, TurnResult, i18n
from conciergent.agent.runner import ChatRunner
from conciergent.config import GatewaySettings, GatewaySpec
from conciergent.i18n.lang import Lang
from conciergent.store.credential import CredentialStore
from conciergent.store.message import MessageStore
from conciergent.surfaces import Surface, SurfaceContext


class SilentAgent:
    async def run(
        self,
        user_input: str,
        *,
        principal: str,
        history: list[typing.Any],
        pending_approval: dict[str, typing.Any] | None,
        bridge: typing.Any = None,
        surface: typing.Any = None,
    ) -> TurnResult:
        return TurnResult(output='ok', history=[])


@pytest.fixture
def stores(messages_url: str, credentials_url: str) -> dict[str, typing.Any]:
    # The store objects the App needs; the containers back real Redis and Postgres.
    return {
        'message_store': MessageStore.from_url(messages_url),
        'credential_store': CredentialStore.from_url(credentials_url),
    }


@pytest.fixture
def store_config(messages_url: str, credentials_url: str) -> dict[str, str]:
    return {'messages_url': messages_url, 'credentials_url': credentials_url}


def _silent_app(stores: dict[str, typing.Any], **overrides: typing.Any) -> App:
    return App(runner=typing.cast(ChatRunner, SilentAgent()), **stores, **overrides)


def _app_config(store_config: dict[str, str], **sections: typing.Any) -> AppConfig:
    agent = {'model': 'test', 'system_prompt': 'x', **sections.pop('agent', {})}
    return AppConfig.model_validate({'agent': agent, 'store': store_config, **sections})


def _client(app: App) -> fastapi.testclient.TestClient:
    return fastapi.testclient.TestClient(app.build_asgi(), follow_redirects=False)


def test_healthz(stores):
    app = _silent_app(stores)

    assert _client(app).get('/healthz').status_code == 204


def test_mcp_oauth_callback_delivers_the_code(stores):
    message_store: MessageStore = stores['message_store']
    client = _client(_silent_app(stores))
    code = 'c9'
    state = 's9'

    async def scenario() -> str | None:
        waiter = asyncio.create_task(message_store.await_oauth_code(state, timeout_seconds=5))
        await asyncio.sleep(0)
        response = await asyncio.to_thread(client.get, '/oauth/mcp/callback', params={'code': code, 'state': state})
        assert response.status_code == 200
        return await waiter

    assert asyncio.run(scenario()) == code


def test_mcp_oauth_callback_rejects_missing_params(stores):
    app = _silent_app(stores)

    assert _client(app).get('/oauth/mcp/callback').status_code == 400


def test_from_app_config_mounts_configured_surfaces(store_config):
    config = _app_config(
        store_config,
        slack={'signing_secret': 'sek', 'client_id': 'cid', 'client_secret': 'cs'},
        line={'channel_secret': 'ls', 'channel_access_token': 'lt'},
    )

    client = _client(App.from_app_config(config))

    # An unsigned post reaching the route is rejected as unsigned, an unmounted route would be 404.
    assert client.post('/slack/events', content=b'{}').status_code == 401
    assert client.post('/slack/interactions', content=b'{}').status_code == 401
    assert client.get('/oauth/slack/install').status_code == 307
    assert client.post('/line/events', content=b'{}').status_code == 401


def test_surfaces_absent_when_not_configured(stores):
    client = _client(_silent_app(stores))

    assert client.post('/slack/events', content=b'{}').status_code == 404
    assert client.post('/line/events', content=b'{}').status_code == 404


def test_gateway_urls_join_the_agent_mcp_servers(store_config):
    mcp_server = 'https://example.com/mcp'
    config = _app_config(
        store_config,
        agent={'mcp_servers': [mcp_server]},
        slack={'signing_secret': 'sek'},
        gateway={'specs': [{'name': 'petstore', 'spec': './petstore.json'}]},
        server={'url': 'https://example.com'},
    )

    app = App.from_app_config(config)

    assert isinstance(app._runner, ChatRunner)
    assert app._runner.mcp_servers == (mcp_server, 'https://example.com/petstore/mcp')


def test_missing_gateway_extra_raises_a_helpful_error(monkeypatch, stores):
    real_import = builtins.__import__

    def no_gateway(name, *args, **kwargs):
        if name == 'openapi_mcp_gateway':
            raise ImportError(name)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, '__import__', no_gateway)
    app = _silent_app(stores, gateway_settings=GatewaySettings(specs=[GatewaySpec(name='petstore', spec='./x.json')]))

    with pytest.raises(RuntimeError, match=r'conciergent\[gateway\]'):
        app.build_asgi()


def test_a_custom_surface_mounts_without_touching_app(stores):
    payload = {'ok': 'yes'}

    class Teams(Surface):
        def build_routers(self, context: SurfaceContext) -> list[fastapi.APIRouter]:
            router = fastapi.APIRouter()

            @router.post('/teams/events')
            async def events() -> dict[str, str]:
                return payload

            return [router]

    client = _client(_silent_app(stores, surfaces=[Teams()]))

    assert client.post('/teams/events').json() == payload


def test_locales_dir_override_rebrands_shipped_text(tmp_path, store_config):
    override_header = '請稍候確認'
    (tmp_path / 'zh-TW.yml').write_text(
        yaml.safe_dump({'approval': {'header': override_header}}, allow_unicode=True), encoding='utf-8'
    )
    config = _app_config(store_config, slack={'signing_secret': 'sek'}, locales_dir=str(tmp_path))

    try:
        App.from_app_config(config)
        # The override wins for its one key and language, while untouched keys stay on the shipped catalog.
        assert i18n.t('approval.header', Lang.ZH_TW) == override_header
        assert i18n.t('approval.confirm', Lang.ZH_TW) == '確認'
        assert i18n.t('approval.header', Lang.EN) == 'Confirm'
    finally:
        # Restore the shipped catalog so the global override does not leak into other tests.
        i18n.load_overrides([])
