import collections.abc
import json

import httpx
import pytest
from mcp.types import LATEST_PROTOCOL_VERSION

from conciergent.agent.mcp import probe
from conciergent.agent.mcp.probe import requires_user_authorization


_URL = 'https://mcp.example/mcp'

Handler = collections.abc.Callable[[httpx.Request], httpx.Response]


def _serve(monkeypatch: pytest.MonkeyPatch, handler: Handler) -> None:
    # The probe builds its client through the SDK's factory, so swap in one that answers from the handler.
    def client_factory(**kwargs: object) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(handler))

    monkeypatch.setattr(probe, 'create_mcp_http_client', client_factory)


def _public_server(request: httpx.Request) -> httpx.Response:
    message = json.loads(request.content) if request.content else {}
    if message.get('method') != 'initialize':
        return httpx.Response(202)
    result = {
        'protocolVersion': LATEST_PROTOCOL_VERSION,
        'capabilities': {},
        'serverInfo': {'name': 'public', 'version': '1'},
    }
    return httpx.Response(200, json={'jsonrpc': '2.0', 'id': message['id'], 'result': result})


async def test_a_completed_handshake_needs_no_user(monkeypatch: pytest.MonkeyPatch):
    _serve(monkeypatch, _public_server)

    assert await requires_user_authorization(_URL) is False


@pytest.mark.parametrize('status', [401, 403])
async def test_a_rejected_handshake_needs_a_user(monkeypatch: pytest.MonkeyPatch, status: int):
    _serve(monkeypatch, lambda request: httpx.Response(status))

    assert await requires_user_authorization(_URL) is True


async def test_the_handshake_carries_no_credentials(monkeypatch: pytest.MonkeyPatch):
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(401)

    _serve(monkeypatch, handler)

    await requires_user_authorization(_URL)

    assert b'"initialize"' in requests[0].content
    assert 'authorization' not in requests[0].headers


@pytest.mark.parametrize('status', [500, 400])
async def test_another_error_status_has_no_verdict(monkeypatch: pytest.MonkeyPatch, status: int):
    _serve(monkeypatch, lambda request: httpx.Response(status))

    assert await requires_user_authorization(_URL) is None


async def test_an_unreachable_server_has_no_verdict(monkeypatch: pytest.MonkeyPatch):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError('refused', request=request)

    _serve(monkeypatch, handler)

    assert await requires_user_authorization(_URL) is None
