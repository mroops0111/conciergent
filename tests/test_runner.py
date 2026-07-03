from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic_ai.models.test import TestModel

from conciergent import Card, Carousel, MemoryStore, PendingApproval, ReplySurface, i18n
from conciergent.lang import Lang
from conciergent.runner import ChatRunner


class RecordingSurfaceBase(ReplySurface):
    async def send_text(self, text: str) -> None:
        return None

    async def send_card(self, card: Card, *, destructive: bool = False) -> None:
        return None

    async def send_carousel(self, cards: list[Card]) -> None:
        return None

    async def show_processing(self) -> None:
        return None


# With no surface the agent resolves no language, so the confirm/cancel prompts fall back to English.
_CONFIRM = i18n.t('approval.confirm', None)
_CANCEL = i18n.t('approval.cancel', None)


def _destructive_server(calls: list[int]) -> FastMCP:
    server = FastMCP('test')

    @server.tool(annotations=ToolAnnotations(destructiveHint=True))
    def delete_it(x: int) -> str:
        calls.append(x)
        return 'deleted'

    return server


def _agent(server: FastMCP) -> ChatRunner:
    return ChatRunner(
        model=TestModel(),
        system_prompt='be helpful',
        mcp_servers=[server],
        store=MemoryStore(),
    )


async def test_reply_passes_through_and_serialises_history():
    agent = ChatRunner(model=TestModel(), system_prompt='be helpful')
    result = await agent.run('hi', principal='p', history=[], pending_approval=None)
    assert not isinstance(result.output, PendingApproval)
    assert isinstance(result.output, (str, Card, Carousel))
    assert isinstance(result.history, list) and result.history


async def test_destructive_tool_defers_before_running():
    calls: list[int] = []
    agent = _agent(_destructive_server(calls))
    result = await agent.run('delete it', principal='p', history=[], pending_approval=None)
    assert isinstance(result.output, PendingApproval)
    assert len(result.output.state['tool_call_ids']) == 1
    assert 'delete_it' in result.output.card.sections[0].text
    assert calls == []  # the tool must not run before approval


async def test_confirm_runs_the_tool():
    calls: list[int] = []
    agent = _agent(_destructive_server(calls))
    parked = await agent.run('delete it', principal='p', history=[], pending_approval=None)
    assert isinstance(parked.output, PendingApproval)
    resumed = await agent.run(_CONFIRM, principal='p', history=[], pending_approval=parked.output.state)
    assert not isinstance(resumed.output, PendingApproval)
    assert len(calls) == 1  # approval let the tool run


async def test_cancel_skips_the_tool():
    calls: list[int] = []
    agent = _agent(_destructive_server(calls))
    parked = await agent.run('delete it', principal='p', history=[], pending_approval=None)
    assert isinstance(parked.output, PendingApproval)
    resumed = await agent.run(_CANCEL, principal='p', history=[], pending_approval=parked.output.state)
    assert not isinstance(resumed.output, PendingApproval)
    assert calls == []  # cancellation kept the tool from running


async def test_unreadable_pending_state_runs_as_a_fresh_turn():
    agent = ChatRunner(model=TestModel(), system_prompt='x')
    result = await agent.run('hi', principal='p', history=[], pending_approval={'wrong': 'shape'})
    assert not isinstance(result.output, PendingApproval)


async def test_undecodable_history_is_dropped_instead_of_raising():
    agent = ChatRunner(model=TestModel(), system_prompt='x')
    result = await agent.run('hi', principal='p', history=[{'not': 'a message'}], pending_approval=None)
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
    parked = await agent.run('do both', principal='p', history=[], pending_approval=None)
    assert isinstance(parked.output, PendingApproval)
    assert len(parked.output.state['tool_call_ids']) == 2
    resumed = await agent.run(_CONFIRM, principal='p', history=[], pending_approval=parked.output.state)
    assert not isinstance(resumed.output, PendingApproval)
    assert sorted(calls) == ['a', 'b']  # one confirm ran every deferred tool


def test_public_mcp_server_needs_no_store():
    # No redirect_uri means no OAuth, so a public MCP server needs no credential store.
    ChatRunner(model=TestModel(), system_prompt='x', mcp_servers=[_destructive_server([])])


def test_oauth_mcp_server_requires_a_store():
    import pytest

    with pytest.raises(ValueError, match='store is required'):
        ChatRunner(
            model=TestModel(),
            system_prompt='x',
            mcp_servers=[_destructive_server([])],
            redirect_uri='https://example.com/oauth/mcp/callback',
        )


async def test_bootstrap_without_servers_reports_no_authorization():
    agent = ChatRunner(model=TestModel(), system_prompt='x')
    assert await agent.bootstrap('p') is False


async def test_bootstrap_opens_mcp_context_without_running_the_agent():
    calls: list[int] = []
    agent = _agent(_destructive_server(calls))
    assert await agent.bootstrap('p') is False
    assert calls == []


class LangSurface(RecordingSurfaceBase):
    def __init__(self, lang: Lang | None) -> None:
        self._lang = lang

    @property
    def lang(self) -> Lang | None:
        return self._lang


async def test_respond_language_instruction_names_the_user_language():
    model = TestModel()
    agent = ChatRunner(model=model, system_prompt='be helpful')
    await agent.run('hi', principal='p', history=[], pending_approval=None, surface=LangSurface(Lang.ZH_TW))
    params = model.last_model_request_parameters
    assert params is not None and params.instruction_parts is not None
    instructions = ' '.join(part.content for part in params.instruction_parts)
    assert 'Traditional Chinese' in instructions


async def test_respond_language_falls_back_to_mirroring_without_a_language():
    model = TestModel()
    agent = ChatRunner(model=model, system_prompt='be helpful')
    await agent.run('hi', principal='p', history=[], pending_approval=None, surface=LangSurface(None))
    params = model.last_model_request_parameters
    assert params is not None and params.instruction_parts is not None
    instructions = ' '.join(part.content for part in params.instruction_parts)
    assert 'same language as the most recent user message' in instructions


async def test_approval_card_is_localized_to_the_user_language():
    calls: list[int] = []
    agent = _agent(_destructive_server(calls))
    result = await agent.run(
        'delete it', principal='p', history=[], pending_approval=None, surface=LangSurface(Lang.ZH_TW)
    )
    assert isinstance(result.output, PendingApproval)
    assert result.output.card.title == i18n.t('approval.header', Lang.ZH_TW)
    assert i18n.t('approval.confirm', Lang.ZH_TW) in [s.label for s in result.output.card.suggestions]


async def test_confirm_in_the_user_language_runs_the_tool():
    calls: list[int] = []
    agent = _agent(_destructive_server(calls))
    surface = LangSurface(Lang.ZH_TW)
    parked = await agent.run('delete it', principal='p', history=[], pending_approval=None, surface=surface)
    assert isinstance(parked.output, PendingApproval)
    resumed = await agent.run(
        i18n.t('approval.confirm', Lang.ZH_TW),
        principal='p',
        history=[],
        pending_approval=parked.output.state,
        surface=surface,
    )
    assert not isinstance(resumed.output, PendingApproval)
    assert len(calls) == 1  # the localized confirm matched and ran the tool


async def test_surface_formatting_hint_joins_the_instructions():
    model = TestModel()
    agent = ChatRunner(model=model, system_prompt='be helpful')

    class MarkerSurface(RecordingSurfaceBase):
        @property
        def text_formatting_instruction(self) -> str:
            return 'MARKER-DIALECT-HINT'

    await agent.run('hi', principal='p', history=[], pending_approval=None, surface=MarkerSurface())
    params = model.last_model_request_parameters
    assert params is not None
    assert params.instruction_parts is not None
    instructions = ' '.join(part.content for part in params.instruction_parts)
    assert 'be helpful' in instructions
    assert 'MARKER-DIALECT-HINT' in instructions
