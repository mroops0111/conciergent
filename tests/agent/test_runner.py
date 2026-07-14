import pytest
from mcp.server.fastmcp import FastMCP
from mcp.shared.auth import OAuthToken
from mcp.types import ToolAnnotations
from pydantic_ai.models.test import TestModel

from conciergent import Card, Carousel, PendingApproval, ReplySurface, i18n
from conciergent.agent.mcp.storage import OAuthTokenStorage
from conciergent.agent.runner import REVOKE_TOOL_NAME, ChatRunner
from conciergent.i18n.lang import Lang
from conciergent.store.credential import CredentialStore


_PRINCIPAL = 'p'
_SYSTEM_PROMPT = 'be helpful'
_OAUTH_SERVER = 'https://example.com/mcp'
_REDIRECT_URI = 'https://example.com/oauth/mcp/callback'

# With no surface the agent resolves no language, so the confirm/cancel prompts fall back to English.
_CONFIRM = i18n.t('approval.confirm', None)
_CANCEL = i18n.t('approval.cancel', None)


class RecordingSurfaceBase(ReplySurface):
    async def send_text(self, text: str) -> None:
        return None

    async def send_card(self, card: Card, *, destructive: bool = False) -> None:
        return None

    async def send_carousel(self, cards: list[Card]) -> None:
        return None

    async def show_processing(self) -> None:
        return None


class LangSurface(RecordingSurfaceBase):
    def __init__(self, lang: Lang | None) -> None:
        self._lang = lang

    @property
    def lang(self) -> Lang | None:
        return self._lang


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
        system_prompt=_SYSTEM_PROMPT,
        mcp_servers=[server],
    )


async def test_reply_passes_through_and_serialises_history():
    agent = ChatRunner(model=TestModel(), system_prompt=_SYSTEM_PROMPT)

    result = await agent.run('hi', principal=_PRINCIPAL, history=[], pending_approval=None)

    assert not isinstance(result.output, PendingApproval)
    assert isinstance(result.output, str | Card | Carousel)
    assert isinstance(result.history, list) and result.history


async def test_destructive_tool_defers_before_running():
    calls: list[int] = []
    agent = _agent(_destructive_server(calls))

    result = await agent.run('delete it', principal=_PRINCIPAL, history=[], pending_approval=None)

    assert isinstance(result.output, PendingApproval)
    assert len(result.output.state['tool_call_ids']) == 1
    assert 'delete_it' in result.output.card.sections[0].text
    assert calls == []  # the tool must not run before approval


async def test_confirm_runs_the_tool():
    calls: list[int] = []
    agent = _agent(_destructive_server(calls))

    parked = await agent.run('delete it', principal=_PRINCIPAL, history=[], pending_approval=None)
    assert isinstance(parked.output, PendingApproval)

    resumed = await agent.run(_CONFIRM, principal=_PRINCIPAL, history=[], pending_approval=parked.output.state)

    assert not isinstance(resumed.output, PendingApproval)
    assert len(calls) == 1  # approval let the tool run


async def test_cancel_skips_the_tool():
    calls: list[int] = []
    agent = _agent(_destructive_server(calls))

    parked = await agent.run('delete it', principal=_PRINCIPAL, history=[], pending_approval=None)
    assert isinstance(parked.output, PendingApproval)

    resumed = await agent.run(_CANCEL, principal=_PRINCIPAL, history=[], pending_approval=parked.output.state)

    assert not isinstance(resumed.output, PendingApproval)
    assert calls == []  # cancellation kept the tool from running


async def test_confirm_matches_the_parked_prompt_after_the_locale_changes():
    # A card parked with no locale offers English buttons, but the tap arrives on an interaction with a locale.
    # Matching must follow the parked prompt, not one re-derived from this turn's language, so the confirm lands.
    calls: list[int] = []
    agent = _agent(_destructive_server(calls))

    parked = await agent.run(
        'delete it', principal=_PRINCIPAL, history=[], pending_approval=None, surface=LangSurface(None)
    )
    assert isinstance(parked.output, PendingApproval)

    resumed = await agent.run(
        _CONFIRM,
        principal=_PRINCIPAL,
        history=[],
        pending_approval=parked.output.state,
        surface=LangSurface(Lang.ZH_TW),
    )

    assert not isinstance(resumed.output, PendingApproval)
    assert len(calls) == 1  # the confirm matched despite the language change, so the tool ran


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

    parked = await agent.run('do both', principal=_PRINCIPAL, history=[], pending_approval=None)
    assert isinstance(parked.output, PendingApproval)
    assert len(parked.output.state['tool_call_ids']) == 2

    resumed = await agent.run(_CONFIRM, principal=_PRINCIPAL, history=[], pending_approval=parked.output.state)

    assert not isinstance(resumed.output, PendingApproval)
    assert sorted(calls) == ['a', 'b']  # one confirm ran every deferred tool


async def test_bootstrap_without_servers_reports_no_authorization():
    agent = ChatRunner(model=TestModel(), system_prompt=_SYSTEM_PROMPT)

    assert await agent.bootstrap(_PRINCIPAL) is False


async def test_bootstrap_opens_mcp_context_without_running_the_agent():
    calls: list[int] = []
    agent = _agent(_destructive_server(calls))

    assert await agent.bootstrap(_PRINCIPAL) is False
    assert calls == []


async def test_unreadable_pending_state_runs_as_a_fresh_turn():
    agent = ChatRunner(model=TestModel(), system_prompt=_SYSTEM_PROMPT)

    result = await agent.run('hi', principal=_PRINCIPAL, history=[], pending_approval={'wrong': 'shape'})

    assert not isinstance(result.output, PendingApproval)


async def test_undecodable_history_is_dropped_instead_of_raising():
    agent = ChatRunner(model=TestModel(), system_prompt=_SYSTEM_PROMPT)

    result = await agent.run('hi', principal=_PRINCIPAL, history=[{'not': 'a message'}], pending_approval=None)

    assert not isinstance(result.output, PendingApproval)


def test_public_mcp_server_needs_no_store():
    # No redirect_uri means no OAuth, so a public MCP server needs no credential store.
    ChatRunner(model=TestModel(), system_prompt=_SYSTEM_PROMPT, mcp_servers=[_destructive_server([])])


def test_oauth_mcp_server_requires_a_store():
    with pytest.raises(ValueError, match='store is required'):
        ChatRunner(
            model=TestModel(),
            system_prompt=_SYSTEM_PROMPT,
            mcp_servers=[_destructive_server([])],
            redirect_uri='https://example.com/oauth/mcp/callback',
        )


async def test_respond_language_instruction_names_the_user_language():
    model = TestModel()
    agent = ChatRunner(model=model, system_prompt=_SYSTEM_PROMPT)

    await agent.run('hi', principal=_PRINCIPAL, history=[], pending_approval=None, surface=LangSurface(Lang.ZH_TW))

    params = model.last_model_request_parameters
    assert params is not None and params.instruction_parts is not None
    instructions = ' '.join(part.content for part in params.instruction_parts)
    assert 'Traditional Chinese' in instructions


async def test_respond_language_falls_back_to_mirroring_without_a_language():
    model = TestModel()
    agent = ChatRunner(model=model, system_prompt=_SYSTEM_PROMPT)

    await agent.run('hi', principal=_PRINCIPAL, history=[], pending_approval=None, surface=LangSurface(None))

    params = model.last_model_request_parameters
    assert params is not None and params.instruction_parts is not None
    instructions = ' '.join(part.content for part in params.instruction_parts)
    assert 'same language as the most recent user message' in instructions


async def test_approval_card_is_localized_to_the_user_language():
    calls: list[int] = []
    agent = _agent(_destructive_server(calls))

    result = await agent.run(
        'delete it', principal=_PRINCIPAL, history=[], pending_approval=None, surface=LangSurface(Lang.ZH_TW)
    )

    assert isinstance(result.output, PendingApproval)
    assert result.output.card.header == i18n.t('approval.header', Lang.ZH_TW)
    assert i18n.t('approval.confirm', Lang.ZH_TW) in [s.label for s in result.output.card.suggestions]


async def test_confirm_in_the_user_language_runs_the_tool():
    calls: list[int] = []
    agent = _agent(_destructive_server(calls))
    surface = LangSurface(Lang.ZH_TW)

    parked = await agent.run('delete it', principal=_PRINCIPAL, history=[], pending_approval=None, surface=surface)
    assert isinstance(parked.output, PendingApproval)

    resumed = await agent.run(
        i18n.t('approval.confirm', Lang.ZH_TW),
        principal=_PRINCIPAL,
        history=[],
        pending_approval=parked.output.state,
        surface=surface,
    )

    assert not isinstance(resumed.output, PendingApproval)
    assert len(calls) == 1  # the localized confirm matched and ran the tool


def _oauth_agent(credential_store: CredentialStore, *, servers: list[str] | None = None) -> ChatRunner:
    return ChatRunner(
        model=TestModel(),
        system_prompt=_SYSTEM_PROMPT,
        mcp_servers=servers if servers is not None else [_OAUTH_SERVER],
        credential_store=credential_store,
        redirect_uri=_REDIRECT_URI,
    )


def test_revoke_tool_is_registered_and_gated_only_when_oauth_is_configured(credential_store: CredentialStore):
    with_oauth = _oauth_agent(credential_store)
    revoke_tool = with_oauth._agent._function_toolset.tools[REVOKE_TOOL_NAME]
    assert revoke_tool.requires_approval is True  # the sign-out only runs after the user confirms

    public = ChatRunner(model=TestModel(), system_prompt=_SYSTEM_PROMPT, mcp_servers=[_destructive_server([])])
    assert REVOKE_TOOL_NAME not in public._agent._function_toolset.tools


async def test_revoke_deletes_tokens_for_every_oauth_server(credential_store: CredentialStore):
    servers = ['https://a.example/mcp', 'https://b.example/mcp']
    for server in servers:
        await OAuthTokenStorage(credential_store, server=server, principal=_PRINCIPAL).set_tokens(
            OAuthToken(access_token='t')
        )
    other_user = OAuthTokenStorage(credential_store, server=servers[0], principal='someone-else')
    await other_user.set_tokens(OAuthToken(access_token='keep'))

    await _oauth_agent(credential_store, servers=servers)._revoke_authorization(_PRINCIPAL)

    for server in servers:
        assert await OAuthTokenStorage(credential_store, server=server, principal=_PRINCIPAL).get_tokens() is None
    assert await other_user.get_tokens() is not None  # another user's authorization is untouched


async def test_surface_formatting_hint_joins_the_instructions():
    model = TestModel()
    agent = ChatRunner(model=model, system_prompt=_SYSTEM_PROMPT)
    formatting_hint = 'MARKER-DIALECT-HINT'

    class MarkerSurface(RecordingSurfaceBase):
        @property
        def text_formatting_instruction(self) -> str:
            return formatting_hint

    await agent.run('hi', principal=_PRINCIPAL, history=[], pending_approval=None, surface=MarkerSurface())

    params = model.last_model_request_parameters
    assert params is not None
    assert params.instruction_parts is not None
    instructions = ' '.join(part.content for part in params.instruction_parts)
    assert _SYSTEM_PROMPT in instructions
    assert formatting_hint in instructions
