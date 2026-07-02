from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic_ai.models.test import TestModel

from conciergent import Card, Carousel, MemoryStore, PendingApproval
from conciergent.agent import PydanticAIAgent


_CONFIRM = 'CONFIRM'
_CANCEL = 'CANCEL'


def _destructive_server(calls: list[int]) -> FastMCP:
    server = FastMCP('test')

    @server.tool(annotations=ToolAnnotations(destructiveHint=True))
    def delete_it(x: int) -> str:
        calls.append(x)
        return 'deleted'

    return server


def _agent(server: FastMCP) -> PydanticAIAgent:
    return PydanticAIAgent(
        model=TestModel(),
        system_prompt='be helpful',
        mcp_servers=[server],
        store=MemoryStore(),
        confirm_prompt=_CONFIRM,
        cancel_prompt=_CANCEL,
    )


async def test_reply_passes_through_and_serialises_history():
    agent = PydanticAIAgent(model=TestModel(), system_prompt='be helpful')
    result = await agent.run('hi', principal='p', history=[], pending=None)
    assert not isinstance(result.output, PendingApproval)
    assert isinstance(result.output, (str, Card, Carousel))
    assert isinstance(result.history, list) and result.history


async def test_destructive_tool_defers_before_running():
    calls: list[int] = []
    agent = _agent(_destructive_server(calls))
    result = await agent.run('delete it', principal='p', history=[], pending=None)
    assert isinstance(result.output, PendingApproval)
    assert len(result.output.state['tool_call_ids']) == 1
    assert 'delete_it' in result.output.card.sections[0].text
    assert calls == []  # the tool must not run before approval


async def test_confirm_runs_the_tool():
    calls: list[int] = []
    agent = _agent(_destructive_server(calls))
    parked = await agent.run('delete it', principal='p', history=[], pending=None)
    assert isinstance(parked.output, PendingApproval)
    resumed = await agent.run(_CONFIRM, principal='p', history=[], pending=parked.output.state)
    assert not isinstance(resumed.output, PendingApproval)
    assert len(calls) == 1  # approval let the tool run


async def test_cancel_skips_the_tool():
    calls: list[int] = []
    agent = _agent(_destructive_server(calls))
    parked = await agent.run('delete it', principal='p', history=[], pending=None)
    assert isinstance(parked.output, PendingApproval)
    resumed = await agent.run(_CANCEL, principal='p', history=[], pending=parked.output.state)
    assert not isinstance(resumed.output, PendingApproval)
    assert calls == []  # cancellation kept the tool from running


async def test_unreadable_pending_state_runs_as_a_fresh_turn():
    agent = PydanticAIAgent(model=TestModel(), system_prompt='x')
    result = await agent.run('hi', principal='p', history=[], pending={'wrong': 'shape'})
    assert not isinstance(result.output, PendingApproval)


async def test_undecodable_history_is_dropped_instead_of_raising():
    agent = PydanticAIAgent(model=TestModel(), system_prompt='x')
    result = await agent.run('hi', principal='p', history=[{'not': 'a message'}], pending=None)
    assert not isinstance(result.output, PendingApproval)


async def test_confirm_runs_all_deferred_tools():
    calls: list[str] = []
    server = FastMCP('test')

    @server.tool(annotations=ToolAnnotations(destructiveHint=True))
    def delete_a(x: int) -> str:
        calls.append('a')
        return 'a'

    @server.tool(annotations=ToolAnnotations(destructiveHint=True))
    def delete_b(x: int) -> str:
        calls.append('b')
        return 'b'

    agent = _agent(server)
    parked = await agent.run('do both', principal='p', history=[], pending=None)
    assert isinstance(parked.output, PendingApproval)
    assert len(parked.output.state['tool_call_ids']) == 2
    resumed = await agent.run(_CONFIRM, principal='p', history=[], pending=parked.output.state)
    assert not isinstance(resumed.output, PendingApproval)
    assert sorted(calls) == ['a', 'b']  # one confirm ran every deferred tool
