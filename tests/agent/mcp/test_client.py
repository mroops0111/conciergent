import typing

import pytest
from mcp.server.fastmcp import FastMCP
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

    async def request_authorization(self, authorize_url: str) -> tuple[str, str]:
        self.seen_url = authorize_url
        return self.code, self.state


def _agent_over(server: FastMCP) -> Agent[None, typing.Any]:
    gated = MCPToolset(server).approval_required(needs_approval)
    return Agent(TestModel(), output_type=[str, DeferredToolRequests], toolsets=[gated])


async def test_destructive_tool_is_gated():
    server = FastMCP('test')

    @server.tool(annotations=ToolAnnotations(destructiveHint=True))
    def delete_it(x: int) -> str:
        return 'deleted'

    result = await _agent_over(server).run('go')
    assert isinstance(result.output, DeferredToolRequests)
    assert [call.tool_name for call in result.output.approvals] == ['delete_it']


async def test_benign_tool_is_not_gated():
    server = FastMCP('test')

    @server.tool()
    def read_it(x: int) -> str:
        return 'ok'

    result = await _agent_over(server).run('go')
    assert not isinstance(result.output, DeferredToolRequests)


def test_build_toolset_without_oauth_returns_a_toolset():
    toolset = build_toolset(_SERVER, principal=_PRINCIPAL)

    assert isinstance(toolset, AbstractToolset)


async def test_build_toolset_with_oauth_constructs(credential_store: CredentialStore):
    toolset = build_toolset(
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
    code, state = await adapter.callback_handler()

    assert code == 'the-code'
    assert state == 'abc'
    assert bridge.seen_url == authorize_url


async def test_bridge_handoff_requires_redirect_first():
    adapter = _OAuthBridgeAdapter(_FakeBridge('c'))

    with pytest.raises(RuntimeError):
        await adapter.callback_handler()
