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


def test_from_app_config_mounts_slack_routes():
    config = AppConfig.model_validate(
        {
            'agent': {'model': 'test', 'system_prompt': 'x'},
            'slack': {'signing_secret': 'sek', 'client_id': 'cid', 'client_secret': 'cs'},
        }
    )
    client = _client(App.from_app_config(config))
    # An unsigned post reaching the route is rejected as unsigned, an unmounted route would be 404.
    assert client.post('/slack/events', content=b'{}').status_code == 401
    assert client.post('/slack/interactions', content=b'{}').status_code == 401
    assert client.get('/oauth/slack/install').status_code == 307


def test_slack_routes_absent_without_slack_settings():
    client = _client(App(agent=SilentAgent()))
    assert client.post('/slack/events', content=b'{}').status_code == 404
