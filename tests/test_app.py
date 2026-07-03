import asyncio
import typing

import fastapi.testclient
import pytest

from conciergent import App, AppConfig, TurnResult
from conciergent.agent.runner import ChatRunner
from conciergent.store.credential import CredentialStore
from conciergent.store.message import MessageStore


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


def _client(app: App) -> fastapi.testclient.TestClient:
    return fastapi.testclient.TestClient(app.build_asgi(), follow_redirects=False)


def test_healthz(stores):
    app = App(runner=typing.cast(ChatRunner, SilentAgent()), **stores)
    assert _client(app).get('/healthz').status_code == 204


def test_mcp_oauth_callback_delivers_the_code(stores):
    message_store: MessageStore = stores['message_store']
    app = App(runner=typing.cast(ChatRunner, SilentAgent()), **stores)
    client = _client(app)

    async def scenario() -> str | None:
        waiter = asyncio.create_task(message_store.await_oauth_code('s9', timeout_seconds=5))
        await asyncio.sleep(0)
        response = await asyncio.to_thread(client.get, '/oauth/mcp/callback', params={'code': 'c9', 'state': 's9'})
        assert response.status_code == 200
        return await waiter

    assert asyncio.run(scenario()) == 'c9'


def test_mcp_oauth_callback_rejects_missing_params(stores):
    app = App(runner=typing.cast(ChatRunner, SilentAgent()), **stores)
    assert _client(app).get('/oauth/mcp/callback').status_code == 400


def test_from_app_config_mounts_configured_surfaces(store_config):
    config = AppConfig.model_validate(
        {
            'agent': {'model': 'test', 'system_prompt': 'x'},
            'slack': {'signing_secret': 'sek', 'client_id': 'cid', 'client_secret': 'cs'},
            'line': {'channel_secret': 'ls', 'channel_access_token': 'lt'},
            'store': store_config,
        }
    )
    client = _client(App.from_app_config(config))
    # An unsigned post reaching the route is rejected as unsigned, an unmounted route would be 404.
    assert client.post('/slack/events', content=b'{}').status_code == 401
    assert client.post('/slack/interactions', content=b'{}').status_code == 401
    assert client.get('/oauth/slack/install').status_code == 307
    assert client.post('/line/events', content=b'{}').status_code == 401


def test_surfaces_absent_when_not_configured(stores):
    client = _client(App(runner=typing.cast(ChatRunner, SilentAgent()), **stores))
    assert client.post('/slack/events', content=b'{}').status_code == 404
    assert client.post('/line/events', content=b'{}').status_code == 404


def test_gateway_urls_join_the_agent_mcp_servers(store_config):
    config = AppConfig.model_validate(
        {
            'agent': {'model': 'test', 'system_prompt': 'x', 'mcp_servers': ['https://example.com/mcp']},
            'slack': {'signing_secret': 'sek'},
            'gateway': {'specs': [{'name': 'petstore', 'spec': './petstore.json'}]},
            'server': {'url': 'https://example.com'},
            'store': store_config,
        }
    )
    app = App.from_app_config(config)
    assert isinstance(app._runner, ChatRunner)
    assert app._runner.mcp_servers == ('https://example.com/mcp', 'https://example.com/petstore/mcp')


def test_missing_gateway_extra_raises_a_helpful_error(monkeypatch, stores):
    import builtins

    real_import = builtins.__import__

    def no_gateway(name, *args, **kwargs):
        if name == 'openapi_mcp_gateway':
            raise ImportError(name)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, '__import__', no_gateway)
    from conciergent.config import GatewaySettings, GatewaySpec

    app = App(
        runner=typing.cast(ChatRunner, SilentAgent()),
        gateway_settings=GatewaySettings(specs=[GatewaySpec(name='petstore', spec='./x.json')]),
        **stores,
    )
    try:
        app.build_asgi()
    except RuntimeError as error:
        assert 'conciergent[gateway]' in str(error)
    else:
        raise AssertionError('a missing gateway extra should raise')


def test_a_custom_surface_mounts_without_touching_app(stores):
    import fastapi as fastapi_module

    from conciergent.surfaces import Surface, SurfaceContext

    class Teams(Surface):
        def build_routers(self, context: SurfaceContext) -> list[fastapi_module.APIRouter]:
            router = fastapi_module.APIRouter()

            @router.post('/teams/events')
            async def events() -> dict[str, str]:
                return {'ok': 'yes'}

            return [router]

    client = _client(App(runner=typing.cast(ChatRunner, SilentAgent()), surfaces=[Teams()], **stores))
    assert client.post('/teams/events').json() == {'ok': 'yes'}


def test_locales_dir_override_rebrands_shipped_text(tmp_path, store_config):
    from conciergent import i18n
    from conciergent.i18n.lang import Lang

    (tmp_path / 'zh-TW.yml').write_text('approval:\n  header: 請稍候確認\n', encoding='utf-8')
    config = AppConfig.model_validate(
        {
            'agent': {'model': 'test', 'system_prompt': 'x'},
            'slack': {'signing_secret': 'sek'},
            'locales_dir': str(tmp_path),
            'store': store_config,
        }
    )
    try:
        App.from_app_config(config)
        # The override wins for its one key and language, while untouched keys stay on the shipped catalog.
        assert i18n.t('approval.header', Lang.ZH_TW) == '請稍候確認'
        assert i18n.t('approval.confirm', Lang.ZH_TW) == '確認'
        assert i18n.t('approval.header', Lang.EN) == 'Confirm'
    finally:
        # Restore the shipped catalog so the global override does not leak into other tests.
        i18n.load_overrides([])
