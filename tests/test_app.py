import asyncio
import typing

import fastapi.testclient

from conciergent import AgentResult, App, AppConfig, ChatAgent, MemoryStore


class SilentAgent(ChatAgent):
    async def run(
        self,
        user_input: str,
        *,
        principal: str,
        history: list[typing.Any],
        pending: dict[str, typing.Any] | None,
        bridge: typing.Any = None,
        surface: typing.Any = None,
    ) -> AgentResult:
        return AgentResult(output='ok', history=[])


def _client(app: App) -> fastapi.testclient.TestClient:
    return fastapi.testclient.TestClient(app.build_asgi(), follow_redirects=False)


def test_healthz():
    app = App(agent=SilentAgent())
    assert _client(app).get('/healthz').json() == {'status': 'ok'}


def test_mcp_oauth_callback_delivers_the_code():
    store = MemoryStore()
    app = App(agent=SilentAgent(), store=store)
    client = _client(app)

    async def scenario() -> str | None:
        waiter = asyncio.create_task(store.await_oauth_code('s9', timeout_seconds=5))
        await asyncio.sleep(0)
        response = await asyncio.to_thread(client.get, '/oauth/mcp/callback', params={'code': 'c9', 'state': 's9'})
        assert response.status_code == 200
        return await waiter

    assert asyncio.run(scenario()) == 'c9'


def test_mcp_oauth_callback_rejects_missing_params():
    app = App(agent=SilentAgent())
    assert _client(app).get('/oauth/mcp/callback').status_code == 400


def test_from_app_config_mounts_configured_surfaces():
    config = AppConfig.model_validate(
        {
            'agent': {'model': 'test', 'system_prompt': 'x'},
            'slack': {'signing_secret': 'sek', 'client_id': 'cid', 'client_secret': 'cs'},
            'line': {'channel_secret': 'ls', 'channel_access_token': 'lt'},
        }
    )
    client = _client(App.from_app_config(config))
    # An unsigned post reaching the route is rejected as unsigned, an unmounted route would be 404.
    assert client.post('/slack/events', content=b'{}').status_code == 401
    assert client.post('/slack/interactions', content=b'{}').status_code == 401
    assert client.get('/oauth/slack/install').status_code == 307
    assert client.post('/line/events', content=b'{}').status_code == 401


def test_surfaces_absent_when_not_configured():
    client = _client(App(agent=SilentAgent()))
    assert client.post('/slack/events', content=b'{}').status_code == 404
    assert client.post('/line/events', content=b'{}').status_code == 404


def test_gateway_urls_join_the_agent_mcp_servers():
    config = AppConfig.model_validate(
        {
            'agent': {'model': 'test', 'system_prompt': 'x', 'mcp_servers': ['https://example.com/mcp']},
            'gateway': {'specs': [{'name': 'petstore', 'spec': './petstore.json'}]},
            'server': {'url': 'https://example.com'},
        }
    )
    app = App.from_app_config(config)
    from conciergent.agent import PydanticAIAgent

    assert isinstance(app.agent, PydanticAIAgent)
    assert app.agent.mcp_servers == ('https://example.com/mcp', 'https://example.com/petstore/mcp')


def test_missing_gateway_extra_raises_a_helpful_error(monkeypatch):
    import builtins

    real_import = builtins.__import__

    def no_gateway(name, *args, **kwargs):
        if name == 'openapi_mcp_gateway':
            raise ImportError(name)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, '__import__', no_gateway)
    from conciergent.config import GatewaySettings, GatewaySpec

    app = App(
        agent=SilentAgent(),
        gateway=GatewaySettings(specs=[GatewaySpec(name='petstore', spec='./x.json')]),
    )
    try:
        app.build_asgi()
    except RuntimeError as error:
        assert 'conciergent[gateway]' in str(error)
    else:
        raise AssertionError('a missing gateway extra should raise')


def test_a_custom_surface_mounts_without_touching_app():
    import fastapi as fastapi_module

    from conciergent.surfaces import Surface, SurfaceContext

    class Teams(Surface):
        def build_routers(self, context: SurfaceContext) -> list[fastapi_module.APIRouter]:
            router = fastapi_module.APIRouter()

            @router.post('/teams/events')
            async def events() -> dict[str, str]:
                return {'ok': 'yes'}

            return [router]

    client = _client(App(agent=SilentAgent(), surfaces=[Teams()]))
    assert client.post('/teams/events').json() == {'ok': 'yes'}


def test_locales_dir_override_rebrands_shipped_text(tmp_path):
    from conciergent import i18n
    from conciergent.lang import Lang

    (tmp_path / 'zh-TW.yml').write_text('approval:\n  header: 請稍候確認\n', encoding='utf-8')
    config = AppConfig.model_validate({'agent': {'model': 'test', 'system_prompt': 'x'}, 'locales_dir': str(tmp_path)})
    try:
        App.from_app_config(config)
        # The override wins for its one key and language, while untouched keys stay on the shipped catalog.
        assert i18n.t('approval.header', Lang.ZH_TW) == '請稍候確認'
        assert i18n.t('approval.confirm', Lang.ZH_TW) == '確認'
        assert i18n.t('approval.header', Lang.EN) == 'Confirm'
    finally:
        # Restore the shipped catalog so the global override does not leak into other tests.
        i18n.load_overrides([])
