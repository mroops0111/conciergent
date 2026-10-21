import pytest
from mcp.server.mcpserver import MCPServer
from pydantic_ai import Agent
from pydantic_ai.mcp import MCPToolset
from pydantic_ai.models.test import TestModel
from pydantic_ai.tools import DeferredToolRequests

from conciergent.agent.mcp.client import needs_approval
from conciergent.agent.mcp.tool_policy import (
    ConfirmDestructiveTools,
    DestructiveToolPolicy,
    HideDestructiveTools,
    RunDestructiveTools,
)
from tests.conftest import destructive_server


async def _run(policy: DestructiveToolPolicy, server: MCPServer) -> tuple[object, set[str]]:
    model = TestModel()
    agent = Agent(
        model, toolsets=[policy.apply(MCPToolset(server), needs_approval)], output_type=[str, DeferredToolRequests]
    )
    result = await agent.run('go')
    parameters = model.last_model_request_parameters
    assert parameters is not None
    return result.output, {tool.name for tool in parameters.function_tools}


@pytest.mark.parametrize(
    ('policy', 'offered', 'deferred', 'ran'),
    [
        (ConfirmDestructiveTools(), True, True, False),
        (HideDestructiveTools(), False, False, False),
        (RunDestructiveTools(), True, False, True),
    ],
)
async def test_each_policy_treats_a_destructive_tool_its_own_way(
    policy: DestructiveToolPolicy, offered: bool, deferred: bool, ran: bool
):
    calls: list[int] = []

    output, tools = await _run(policy, destructive_server(calls))

    assert ('delete_it' in tools) is offered
    assert isinstance(output, DeferredToolRequests) is deferred
    assert bool(calls) is ran
