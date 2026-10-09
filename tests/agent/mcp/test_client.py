import typing

import pytest
from mcp.server.mcpserver import MCPServer
from mcp.shared.auth import AuthorizationCodeResult
from mcp.types import ToolAnnotations
from pydantic_ai import Agent
from pydantic_ai.mcp import MCPToolset
from pydantic_ai.models.test import TestModel
from pydantic_ai.tools import DeferredToolRequests
from pydantic_ai.toolsets import AbstractToolset

from conciergent import OAuthBridge
from conciergent.agent.mcp.client import _OAuthBridgeAdapter, build_toolset, needs_approval
from conciergent.store.credential import CredentialStore


_SERVER = 'https://example.com/mcp'
_PRINCIPAL = 'slack:T:U'


class _FakeBridge(OAuthBridge):
    def __init__(self, code: str, *, state: str = 'state') -> None:
        self.code = code
        self.state = state
        self.seen_url: str | None = None

    async def request_authorization(self, authorize_url: str) -> AuthorizationCodeResult:
        self.seen_url = authorize_url
        return AuthorizationCodeResult(code=self.code, state=self.state)


def _agent_over(server: MCPServer) -> Agent[None, typing.Any]:
    gated = MCPToolset(server).approval_required(needs_approval)
    return Agent(TestModel(), output_type=[str, DeferredToolRequests], toolsets=[gated])


async def test_destructive_tool_is_gated():
    server = MCPServer('test')

    @server.tool(annotations=ToolAnnotations(destructive_hint=True))
    def delete_it(x: int) -> str:
        return 'deleted'

    result = await _agent_over(server).run('go')
    assert isinstance(result.output, DeferredToolRequests)
    assert [call.tool_name for call in result.output.approvals] == ['delete_it']


async def test_benign_tool_is_not_gated():
    server = MCPServer('test')

    @server.tool()
    def read_it(x: int) -> str:
        return 'ok'

    result = await _agent_over(server).run('go')
    assert not isinstance(result.output, DeferredToolRequests)


async def test_build_toolset_without_oauth_returns_a_toolset():
    toolset = await build_toolset(_SERVER, principal=_PRINCIPAL)

    assert isinstance(toolset, AbstractToolset)


async def test_build_toolset_with_oauth_constructs(credential_store: CredentialStore):
    toolset = await build_toolset(
        _SERVER,
        credential_store=credential_store,
        principal=_PRINCIPAL,
        oauth_bridge=_FakeBridge('code'),
        redirect_uri='https://example.com/oauth/callback',
    )

    assert isinstance(toolset, AbstractToolset)


async def test_bridge_handoff_delegates_to_bridge():
    bridge = _FakeBridge('the-code', state='abc')
    adapter = _OAuthBridgeAdapter(bridge)
    authorize_url = 'https://example.com/authorize?state=abc'

    await adapter.redirect_handler(authorize_url)
    result = await adapter.callback_handler()

    assert result == AuthorizationCodeResult(code='the-code', state='abc')
    assert bridge.seen_url == authorize_url


async def test_bridge_handoff_requires_redirect_first():
    adapter = _OAuthBridgeAdapter(_FakeBridge('c'))

    with pytest.raises(RuntimeError):
        await adapter.callback_handler()
