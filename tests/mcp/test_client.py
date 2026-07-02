import typing

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic_ai import Agent
from pydantic_ai.mcp import MCPToolset
from pydantic_ai.models.test import TestModel
from pydantic_ai.tools import DeferredToolRequests
from pydantic_ai.toolsets import AbstractToolset

from conciergent import MemoryStore, OAuthBridge
from conciergent.mcp.client import _BridgeCallbacks, build_toolset, needs_approval


class _FakeBridge(OAuthBridge):
    def __init__(self, code: str) -> None:
        self.code = code
        self.seen_url: str | None = None

    async def request_authorization(self, authorize_url: str) -> str:
        self.seen_url = authorize_url
        return self.code


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
    toolset = build_toolset('https://example.com/mcp', store=MemoryStore(), principal='slack:T:U')
    assert isinstance(toolset, AbstractToolset)


def test_build_toolset_with_oauth_constructs():
    toolset = build_toolset(
        'https://example.com/mcp',
        store=MemoryStore(),
        principal='slack:T:U',
        bridge=_FakeBridge('code'),
        redirect_uri='https://example.com/oauth/callback',
    )
    assert isinstance(toolset, AbstractToolset)


async def test_bridge_handoff_delegates_to_bridge():
    bridge = _FakeBridge('the-code')
    callbacks = _BridgeCallbacks(bridge)
    await callbacks.redirect_handler('https://example.com/authorize?state=abc')
    code, state = await callbacks.callback_handler()
    assert code == 'the-code'
    assert state is None
    assert bridge.seen_url == 'https://example.com/authorize?state=abc'


async def test_bridge_handoff_requires_redirect_first():
    callbacks = _BridgeCallbacks(_FakeBridge('c'))
    try:
        await callbacks.callback_handler()
    except RuntimeError:
        return
    raise AssertionError('callback before redirect should raise')
