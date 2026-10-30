import asyncio
import builtins
import contextlib
import json
import logging
import socket
import threading
import time
import typing

import fastapi
import fastapi.testclient
import pydantic_ai
import pytest
import uvicorn
import yaml
from mcp.shared.auth import AuthorizationCodeResult
from pydantic_ai.mcp import MCPToolset
from pydantic_ai.models.test import TestModel

from conciergent import App, AppConfig, i18n
from conciergent.agent.runner import ChatRunner
from conciergent.chart import ChartCapability, ChartPublisher, ChartRenderer, DefaultChartStyle
from conciergent.config import GatewaySettings, GatewaySpec, build_app_config
from conciergent.i18n.lang import Lang
from conciergent.push import PushOutcome, UnknownPushError
from conciergent.store.chart import ChartStore
from conciergent.store.credential import CredentialStore
from conciergent.store.message import MessageStore
from conciergent.surfaces import Surface, SurfaceContext
from tests.conftest import CHART_BASE_URL, StubRunner, chart_renderer
from tests.push.conftest import RecordingPushSurface, push_settings


@pytest.fixture
def stores(messages_url: str, credentials_url: str) -> dict[str, typing.Any]:
    # The store objects the App needs, backed by real Redis and Postgres containers.
    return {
        'message_store': MessageStore.from_url(messages_url),
        'credential_store': CredentialStore.from_url(credentials_url),
    }


@pytest.fixture
def store_config(messages_url: str, credentials_url: str) -> dict[str, str]:
    return {'messages_url': messages_url, 'credentials_url': credentials_url}


def _silent_app(stores: dict[str, typing.Any], **overrides: typing.Any) -> App:
    return App(runner=StubRunner('ok').as_runner(), **stores, **overrides)


def _app_config(store_config: dict[str, str], **sections: typing.Any) -> AppConfig:
    agent = {'model': 'test', 'system_prompt': 'x', **sections.pop('agent', {})}
    return build_app_config({'agent': agent, 'store': store_config, **sections})


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
    iss = 'https://bot.example.com/github'

    async def scenario() -> AuthorizationCodeResult | None:
        waiter = asyncio.create_task(message_store.await_oauth_code(state, timeout_seconds=5))
        await asyncio.sleep(0)
        response = await asyncio.to_thread(
            client.get, '/oauth/mcp/callback', params={'code': code, 'state': state, 'iss': iss}
        )
        assert response.status_code == 200
        return await waiter

    assert asyncio.run(scenario()) == AuthorizationCodeResult(code=code, state=state, iss=iss)


def test_mcp_oauth_callback_rejects_missing_params(stores):
    app = _silent_app(stores)

    assert _client(app).get('/oauth/mcp/callback').status_code == 400


def test_from_app_config_mounts_configured_surfaces(store_config):
    config = _app_config(
        store_config,
        surface={
            'slack': {'enabled': True, 'signing_secret': 'sek', 'client_id': 'cid', 'client_secret': 'cs'},
            'line': {'enabled': True, 'channel_secret': 'ls', 'channel_access_token': 'lt'},
        },
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
        surface={'slack': {'enabled': True, 'signing_secret': 'sek'}},
        gateway={
            'enabled': True,
            'redis_url': 'redis://localhost:6379/0',
            'specs': [{'name': 'petstore', 'spec': './petstore.json'}],
        },
        server={'url': 'https://example.com'},
    )

    app = App.from_app_config(config)

    assert isinstance(app._runner, ChatRunner)
    # The spec is served by this same app, so the agent reaches it over loopback rather than the public URL.
    assert app._runner.mcp_servers == (mcp_server, 'http://127.0.0.1:8000/petstore/mcp')
    # The config already knows the spec needs no user's token, so only the external server is left to probe.
    assert app._runner._known_user_authorization == {'http://127.0.0.1:8000/petstore/mcp': False}


def test_a_per_user_oauth_spec_is_reached_at_the_public_url(store_config):
    config = _app_config(
        store_config,
        surface={'slack': {'enabled': True, 'signing_secret': 'sek'}},
        gateway={
            'enabled': True,
            'redis_url': 'redis://localhost:6379/0',
            'specs': [
                {'name': 'petstore', 'spec': './petstore.json', 'auth': {'type': 'bearer', 'token': 't'}},
                {'name': 'calendar', 'spec': './calendar.json', 'auth': {'type': 'oauth2'}},
            ],
        },
        server={'url': 'https://example.com', 'internal_url': 'http://conciergent:8000'},
    )

    app = App.from_app_config(config)

    # The OAuth discovery metadata names the public URL as the resource, so a per-user spec keeps it.
    assert app._runner.mcp_servers == ('http://conciergent:8000/petstore/mcp', 'https://example.com/calendar/mcp')


def test_a_bearer_spec_works_when_the_public_url_is_unreachable(store_config, messages_url, tmp_path):
    upstream = fastapi.FastAPI()
    authorizations: list[str | None] = []

    @upstream.get('/pets', operation_id='listPets')
    def list_pets(authorization: str | None = fastapi.Header(default=None)) -> list[dict[str, str]]:
        authorizations.append(authorization)
        return [{'name': 'Rex'}]

    spec = tmp_path / 'pets.json'
    spec.write_text(json.dumps(upstream.openapi()))
    with _serving(upstream, _free_port()) as upstream_url:
        port = _free_port()
        config = _app_config(
            store_config,
            surface={'slack': {'enabled': True, 'signing_secret': 'sek'}},
            gateway={
                'enabled': True,
                'redis_url': messages_url,
                'specs': [
                    {
                        'name': 'pets',
                        'spec': str(spec),
                        'base_url': upstream_url,
                        'auth': {'type': 'bearer', 'token': 'upstream-token'},
                    }
                ],
            },
            # A tunnel that routes only the chat webhook, so the public URL never reaches /pets/mcp.
            server={'host': '127.0.0.1', 'port': port, 'url': 'https://unreachable.invalid'},
        )
        app = App.from_app_config(config)
        with _serving(app.build_asgi(), port):
            (server,) = app._runner.mcp_servers
            agent = pydantic_ai.Agent(TestModel(), toolsets=[MCPToolset(server)])

            result = agent.run_sync('list the pets')

    assert 'Rex' in result.output
    assert authorizations == ['Bearer upstream-token']


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        return probe.getsockname()[1]


@contextlib.contextmanager
def _serving(asgi: fastapi.FastAPI, port: int) -> typing.Iterator[str]:
    """Serve ``asgi`` on ``port`` in a background thread until the block exits, yielding its URL."""
    server = uvicorn.Server(uvicorn.Config(asgi, host='127.0.0.1', port=port, log_config=None))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 10
        while not server.started:
            if time.monotonic() > deadline or not thread.is_alive():
                raise RuntimeError(f'the test server on port {port} did not start')
            time.sleep(0.05)
        yield f'http://127.0.0.1:{port}'
    finally:
        server.should_exit = True
        thread.join(timeout=10)


def test_missing_gateway_extra_raises_a_helpful_error(monkeypatch, stores):
    real_import = builtins.__import__

    def no_gateway(name, *args, **kwargs):
        if name == 'openapi_mcp_gateway':
            raise ImportError(name)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, '__import__', no_gateway)
    app = _silent_app(
        stores,
        gateway_settings=GatewaySettings(
            enabled=True, redis_url='redis://localhost:6379/0', specs=[GatewaySpec(name='petstore', spec='./x.json')]
        ),
    )

    with pytest.raises(RuntimeError, match=r'conciergent\[gateway\]'):
        app.build_asgi()


def _chart_capability(messages_url: str) -> ChartCapability:
    # A store of its own, since the test client serves on another event loop than the fixture's Redis client.
    store = ChartStore.from_url(messages_url, ttl_seconds=600)
    return ChartCapability(ChartPublisher(chart_renderer(), store, base_url=CHART_BASE_URL))


async def test_a_stored_chart_is_served_as_a_cacheable_png(stores, messages_url, chart_store: ChartStore):
    image = b'\x89PNG\r\n\x1a\nimage'
    chart_id = await chart_store.put(image)
    client = _client(_silent_app(stores, capabilities=[_chart_capability(messages_url)]))

    response = client.get(f'/charts/{chart_id}.png')

    assert response.status_code == 200
    assert response.content == image
    assert response.headers['content-type'] == 'image/png'
    assert response.headers['cache-control'] == 'public, max-age=600, immutable'


async def test_an_unknown_chart_is_not_found(stores, messages_url, chart_store: ChartStore):
    client = _client(_silent_app(stores, capabilities=[_chart_capability(messages_url)]))

    assert client.get('/charts/missing-but-well-formed-id-0123.png').status_code == 404
    assert client.get('/charts/short.png').status_code == 404


def test_without_charts_the_route_is_absent(stores):
    assert _client(_silent_app(stores)).get('/charts/missing-but-well-formed-id-0123.png').status_code == 404


def test_enabled_charts_give_the_agent_its_chart_tool(store_config):
    config = _app_config(
        store_config,
        surface={'line': {'enabled': True, 'channel_secret': 's', 'channel_access_token': 't'}},
        capability={'chart': {'enabled': True, 'ttl_seconds': 3600}},
        server={'url': CHART_BASE_URL},
    )

    app = App.from_app_config(config)

    capability = app._capabilities[0]
    assert isinstance(capability, ChartCapability)
    assert app._runner.capabilities == (capability,)
    assert capability.publisher.store.ttl_seconds == 3600


def test_charts_on_a_url_platforms_cannot_fetch_log_a_warning(store_config, caplog):
    config = _app_config(
        store_config,
        surface={'line': {'enabled': True, 'channel_secret': 's', 'channel_access_token': 't'}},
        capability={'chart': {'enabled': True}},
        server={'url': 'http://localhost:8000'},
    )

    with caplog.at_level(logging.WARNING, logger='conciergent.config'):
        App.from_app_config(config)

    assert any('public HTTPS' in record.getMessage() for record in caplog.records)


def test_charts_are_off_by_default(store_config):
    config = _app_config(store_config, surface={'slack': {'enabled': True, 'signing_secret': 'sek'}})

    app = App.from_app_config(config)

    assert app._capabilities == []
    assert app._runner.capabilities == ()


def test_missing_charts_extra_raises_a_helpful_error(monkeypatch):
    real_import = builtins.__import__

    def no_charts(name, *args, **kwargs):
        if name == 'vl_convert':
            raise ImportError(name)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, '__import__', no_charts)

    with pytest.raises(RuntimeError, match=r'conciergent\[charts\]'):
        ChartRenderer(DefaultChartStyle(font='sans-serif'), max_rows=10)


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
    config = _app_config(
        store_config, surface={'slack': {'enabled': True, 'signing_secret': 'sek'}}, locales_dir=str(tmp_path)
    )

    try:
        App.from_app_config(config)
        # The override wins for its one key and language, while untouched keys stay on the shipped catalog.
        assert i18n.t('approval.header', Lang.ZH_TW) == override_header
        assert i18n.t('approval.confirm', Lang.ZH_TW) == '確認'
        assert i18n.t('approval.header', Lang.EN) == 'Confirm'
    finally:
        # Restore the shipped catalog so the global override does not leak into other tests.
        i18n.load_overrides([])


def test_push_runs_the_named_push_once_and_pushes_the_reply(stores):
    surface = RecordingPushSurface()
    app = _silent_app(stores, surfaces=[surface], pushes=[push_settings()])

    assert app.push('daily-digest') is PushOutcome.delivered
    assert surface.targets == ['C1'] and surface.texts() == ['ok']


def test_push_names_the_configured_pushes_when_the_name_is_unknown(stores):
    app = _silent_app(stores, surfaces=[RecordingPushSurface()], pushes=[push_settings()])

    with pytest.raises(UnknownPushError, match="no push named 'weekly', the configured ones are: daily-digest"):
        app.push('weekly')


def test_fire_fails_with_the_failure_message_when_an_mcp_server_needs_a_users_token(stores):
    runner = StubRunner('ok')
    # What the startup probe reports for an MCP server outside the gateway that needs a per-user authorization.
    runner.credential_free = False
    surface = RecordingPushSurface()
    app = App(
        runner=runner.as_runner(),
        **stores,
        surfaces=[surface],
        pushes=[push_settings(failure_message='baby is down')],
    )

    assert app.push('daily-digest') is PushOutcome.failed
    assert runner.calls == [] and surface.texts() == ['baby is down']
