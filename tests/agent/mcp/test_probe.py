import functools

import httpx
import pytest

from conciergent.agent.mcp import probe
from conciergent.agent.mcp.probe import requires_user_authorization


_URL = 'https://mcp.example/mcp'


def _serve(monkeypatch: pytest.MonkeyPatch, handler: httpx.MockTransport) -> None:
    monkeypatch.setattr(probe.httpx, 'AsyncClient', functools.partial(httpx.AsyncClient, transport=handler))


@pytest.mark.parametrize(
    ('status', 'verdict'), [(401, True), (403, True), (200, False), (202, False), (500, None), (404, None)]
)
async def test_the_unauthenticated_status_decides_the_verdict(
    monkeypatch: pytest.MonkeyPatch, status: int, verdict: bool | None
):
    _serve(monkeypatch, httpx.MockTransport(lambda request: httpx.Response(status)))

    assert await requires_user_authorization(_URL) is verdict


async def test_the_probe_sends_an_initialize_without_credentials(monkeypatch: pytest.MonkeyPatch):
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(401)

    _serve(monkeypatch, httpx.MockTransport(handler))

    await requires_user_authorization(_URL)

    assert b'"initialize"' in requests[0].content
    assert 'authorization' not in requests[0].headers


async def test_an_opened_session_is_ended(monkeypatch: pytest.MonkeyPatch):
    methods: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        methods.append(request.method)
        if request.method == 'DELETE':
            raise httpx.ConnectError('gone', request=request)
        return httpx.Response(200, headers={'mcp-session-id': 's1'})

    _serve(monkeypatch, httpx.MockTransport(handler))

    # A failed cleanup keeps the verdict the initialize already gave.
    assert await requires_user_authorization(_URL) is False
    assert methods == ['POST', 'DELETE']


async def test_an_unreachable_server_has_no_verdict(monkeypatch: pytest.MonkeyPatch):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError('refused', request=request)

    _serve(monkeypatch, httpx.MockTransport(handler))

    assert await requires_user_authorization(_URL) is None
