"""End-to-end MCP OAuth against a real embedded gateway: authorize, reuse the stored token, then refresh it.

An upstream app plays both the OAuth provider and the protected API. The gateway runs ``authorization_code``,
so it is the authorization server the MCP client talks to, and it returns an RFC 9207 ``iss`` on the redirect.
A bridge plays the user by following the authorize URL's redirects until they reach the client's redirect URI.
"""

import asyncio
import collections.abc
import socket
import threading
import time
import typing
import urllib.parse

import fastapi
import httpx
import pytest
import uvicorn
from mcp.shared.auth import AuthorizationCodeResult
from pydantic_ai import Agent
from pydantic_ai.models.test import TestModel

from conciergent.agent.mcp.client import build_toolset
from conciergent.agent.mcp.storage import OAuthTokenStorage
from conciergent.runtime import OAuthBridge
from conciergent.store.credential import CredentialStore


gateway_module = pytest.importorskip('openapi_mcp_gateway')

_REDIRECT_URI = 'http://127.0.0.1:1/oauth/mcp/callback'
_PRINCIPAL = 'line:U1'
# Short enough that the test can wait it out to exercise the refresh, long enough to survive one turn.
_MCP_TOKEN_TTL_SECONDS = 1


class _ClickingBridge(OAuthBridge):
    def __init__(self) -> None:
        self.calls = 0
        self.received: AuthorizationCodeResult | None = None

    @typing.override
    async def request_authorization(self, authorize_url: str) -> AuthorizationCodeResult:
        self.calls += 1
        url = authorize_url
        async with httpx.AsyncClient() as client:
            for _ in range(10):
                if url.startswith(_REDIRECT_URI):
                    query = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(url).query))
                    self.received = AuthorizationCodeResult(
                        code=query['code'], state=query.get('state'), iss=query.get('iss')
                    )
                    return self.received
                response = await client.get(url)
                assert response.is_redirect, response.text
                url = urllib.parse.urljoin(url, response.headers['location'])
        raise AssertionError('the authorize URL never redirected back to the client')


def _upstream() -> fastapi.FastAPI:
    app = fastapi.FastAPI()

    @app.get('/authorize', include_in_schema=False)
    def authorize(redirect_uri: str, state: str) -> fastapi.responses.RedirectResponse:
        query = urllib.parse.urlencode({'code': 'upstream-code', 'state': state})
        return fastapi.responses.RedirectResponse(f'{redirect_uri}?{query}')

    @app.post('/token', include_in_schema=False)
    def token() -> dict[str, typing.Any]:
        return {'access_token': 'upstream-token', 'token_type': 'Bearer', 'expires_in': 3600}

    @app.get('/pets', operation_id='list_pets')
    def list_pets(authorization: str = fastapi.Header(default='')) -> list[dict[str, str]]:
        if authorization != 'Bearer upstream-token':
            raise fastapi.HTTPException(401)
        return [{'name': 'mochi'}]

    return app


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def _serve_in_thread(app: fastapi.FastAPI, port: int) -> uvicorn.Server:
    # Each server gets its own thread and loop: the gateway fetches the upstream spec synchronously at startup.
    server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=port, log_level='warning'))
    threading.Thread(target=server.run, daemon=True).start()
    deadline = time.monotonic() + 10
    while not server.started:
        if time.monotonic() > deadline:
            raise RuntimeError('server did not start')
        time.sleep(0.02)
    return server


@pytest.fixture
def gateway_mcp_url() -> collections.abc.Iterator[str]:
    upstream_port, gateway_port = _free_port(), _free_port()
    upstream = _serve_in_thread(_upstream(), upstream_port)
    upstream_url = f'http://127.0.0.1:{upstream_port}'
    config = gateway_module.GatewayConfig.model_validate(
        {
            'url': f'http://127.0.0.1:{gateway_port}',
            'servers': [
                {
                    'name': 'api',
                    'spec': f'{upstream_url}/openapi.json',
                    'base_url': upstream_url,
                    'auth': {
                        'type': 'oauth2',
                        'flow': 'authorization_code',
                        'mcp_access_token_ttl': _MCP_TOKEN_TTL_SECONDS,
                        'upstream': {
                            'client_id': 'cid',
                            'client_secret': 'secret',
                            'authorization_url': f'{upstream_url}/authorize',
                            'token_url': f'{upstream_url}/token',
                        },
                    },
                }
            ],
        }
    )
    gateway = gateway_module.Gateway.from_config(config)
    server = _serve_in_thread(gateway._build_app(transport='streamable-http', host='127.0.0.1'), gateway_port)
    try:
        yield f'http://127.0.0.1:{gateway_port}/api/mcp'
    finally:
        server.should_exit = upstream.should_exit = True


async def _run_turn(url: str, credential_store: CredentialStore, bridge: OAuthBridge) -> str:
    toolset = await build_toolset(
        url,
        principal=_PRINCIPAL,
        credential_store=credential_store,
        oauth_bridge=bridge,
        redirect_uri=_REDIRECT_URI,
    )
    agent = Agent(TestModel(call_tools=['list_pets']), toolsets=[toolset])
    result = await asyncio.wait_for(agent.run('pets?'), timeout=30)
    return result.output


async def test_authorize_reuse_and_refresh_through_the_gateway(gateway_mcp_url: str, credential_store: CredentialStore):
    bridge = _ClickingBridge()
    storage = OAuthTokenStorage(credential_store, server=gateway_mcp_url, principal=_PRINCIPAL)

    assert 'mochi' in await _run_turn(gateway_mcp_url, credential_store, bridge)
    assert bridge.calls == 1
    # Gateway 0.8 advertises RFC 9207, so the SDK rejects a redirect without iss; it must reach the bridge result.
    assert bridge.received is not None and bridge.received.iss
    first = await storage.get_tokens()
    assert first is not None

    # A second turn reuses the stored token instead of authorizing again.
    assert 'mochi' in await _run_turn(gateway_mcp_url, credential_store, bridge)
    assert bridge.calls == 1

    # Once the MCP token expires, the hydrated expiry makes the SDK refresh rather than re-authorize.
    await asyncio.sleep(_MCP_TOKEN_TTL_SECONDS + 0.5)
    assert 'mochi' in await _run_turn(gateway_mcp_url, credential_store, bridge)
    assert bridge.calls == 1
    refreshed = await storage.get_tokens()
    assert refreshed is not None
    assert refreshed.access_token != first.access_token
