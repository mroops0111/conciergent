import asyncio
import collections.abc
import datetime
import typing
import zoneinfo

import pytest
from mcp.server.mcpserver import MCPServer
from mcp.shared.auth import OAuthToken
from mcp.types import ToolAnnotations
from pydantic_ai.messages import (
    ModelMessage,
    ModelMessagesTypeAdapter,
    ModelResponse,
    RetryPromptPart,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.test import TestModel

from conciergent import Card, Carousel, PendingApproval, i18n
from conciergent.agent import runner as runner_module
from conciergent.agent.mcp.storage import OAuthTokenStorage
from conciergent.agent.runner import REVOKE_TOOL_NAME, ChatRunner
from conciergent.chart import CREATE_CHART_TOOL_NAME, ChartCapability
from conciergent.chat_context import DirectChatContext, GroupChatContext, UnattendedChatContext
from conciergent.i18n.lang import Lang
from conciergent.runtime import SkippedReply, TurnResult
from conciergent.store.credential import CredentialStore
from tests.conftest import RecordingSurface, destructive_server


_PRINCIPAL = 'p'
_SYSTEM_PROMPT = 'be helpful'
_OAUTH_SERVER = 'https://example.com/mcp'
_REDIRECT_URI = 'https://example.com/oauth/mcp/callback'

# With no surface the agent resolves no language, so the confirm/cancel prompts fall back to English.
_CONFIRM = i18n.t('approval.confirm', None)
_CANCEL = i18n.t('approval.cancel', None)
# The chat most turns here run in, one user's direct chat.
_DIRECT = DirectChatContext(conversation=_PRINCIPAL)


def _agent(
    *servers: MCPServer | str, model: TestModel | FunctionModel | None = None, **options: typing.Any
) -> ChatRunner:
    return ChatRunner(model=model or TestModel(), system_prompt=_SYSTEM_PROMPT, mcp_servers=list(servers), **options)


async def test_reply_passes_through_and_serialises_history():
    agent = _agent()

    result = await agent.run('hi', principal=_PRINCIPAL, chat_context=_DIRECT, history=[], pending_approval=None)

    assert not isinstance(result.output, PendingApproval)
    assert isinstance(result.output, str | Card | Carousel)
    assert isinstance(result.history, list) and result.history


async def test_destructive_tool_defers_before_running():
    calls: list[int] = []
    agent = _agent(destructive_server(calls))

    result = await agent.run('delete it', principal=_PRINCIPAL, chat_context=_DIRECT, history=[], pending_approval=None)

    assert isinstance(result.output, PendingApproval)
    assert len(result.output.state['tool_call_ids']) == 1
    assert 'delete_it' in result.output.card.sections[0].text
    assert calls == []  # the tool must not run before approval


async def test_confirm_runs_the_tool():
    calls: list[int] = []
    agent = _agent(destructive_server(calls))

    parked = await agent.run('delete it', principal=_PRINCIPAL, chat_context=_DIRECT, history=[], pending_approval=None)
    assert isinstance(parked.output, PendingApproval)

    resumed = await agent.run(
        _CONFIRM, principal=_PRINCIPAL, chat_context=_DIRECT, history=[], pending_approval=parked.output.state
    )

    assert not isinstance(resumed.output, PendingApproval)
    assert len(calls) == 1  # approval let the tool run


async def test_cancel_skips_the_tool():
    calls: list[int] = []
    agent = _agent(destructive_server(calls))

    parked = await agent.run('delete it', principal=_PRINCIPAL, chat_context=_DIRECT, history=[], pending_approval=None)
    assert isinstance(parked.output, PendingApproval)

    resumed = await agent.run(
        _CANCEL, principal=_PRINCIPAL, chat_context=_DIRECT, history=[], pending_approval=parked.output.state
    )

    assert not isinstance(resumed.output, PendingApproval)
    assert calls == []  # cancellation kept the tool from running


async def test_confirm_matches_the_parked_prompt_after_the_locale_changes():
    # A card parked with no locale offers English buttons, but the tap arrives on an interaction with a locale.
    # Matching must follow the parked prompt, not one re-derived from this turn's language, so the confirm lands.
    calls: list[int] = []
    agent = _agent(destructive_server(calls))

    parked = await agent.run(
        'delete it',
        principal=_PRINCIPAL,
        chat_context=_DIRECT,
        history=[],
        pending_approval=None,
        surface=RecordingSurface(None),
    )
    assert isinstance(parked.output, PendingApproval)

    resumed = await agent.run(
        _CONFIRM,
        principal=_PRINCIPAL,
        chat_context=_DIRECT,
        history=[],
        pending_approval=parked.output.state,
        surface=RecordingSurface(Lang.ZH_TW),
    )

    assert not isinstance(resumed.output, PendingApproval)
    assert len(calls) == 1  # the confirm matched despite the language change, so the tool ran


async def test_confirm_runs_all_deferred_tools():
    calls: list[str] = []
    server = MCPServer('test')

    @server.tool(annotations=ToolAnnotations(destructive_hint=True))
    def delete_a(x: int) -> str:
        calls.append('a')
        return 'a'

    @server.tool(annotations=ToolAnnotations(destructive_hint=True))
    def delete_b(x: int) -> str:
        calls.append('b')
        return 'b'

    agent = _agent(server)

    parked = await agent.run('do both', principal=_PRINCIPAL, chat_context=_DIRECT, history=[], pending_approval=None)
    assert isinstance(parked.output, PendingApproval)
    assert len(parked.output.state['tool_call_ids']) == 2

    resumed = await agent.run(
        _CONFIRM, principal=_PRINCIPAL, chat_context=_DIRECT, history=[], pending_approval=parked.output.state
    )

    assert not isinstance(resumed.output, PendingApproval)
    assert sorted(calls) == ['a', 'b']  # one confirm ran every deferred tool


async def test_bootstrap_without_servers_reports_no_authorization():
    agent = _agent()

    assert await agent.bootstrap(_PRINCIPAL) is False


async def test_bootstrap_opens_mcp_context_without_running_the_agent():
    calls: list[int] = []
    agent = _agent(destructive_server(calls))

    assert await agent.bootstrap(_PRINCIPAL) is False
    assert calls == []


async def test_unreadable_pending_state_runs_as_a_fresh_turn():
    agent = _agent()

    result = await agent.run(
        'hi', principal=_PRINCIPAL, chat_context=_DIRECT, history=[], pending_approval={'wrong': 'shape'}
    )

    assert not isinstance(result.output, PendingApproval)


async def test_undecodable_history_is_dropped_instead_of_raising():
    agent = _agent()

    result = await agent.run(
        'hi', principal=_PRINCIPAL, chat_context=_DIRECT, history=[{'not': 'a message'}], pending_approval=None
    )

    assert not isinstance(result.output, PendingApproval)


def test_public_mcp_server_needs_no_store():
    # No redirect_uri means no OAuth, so a public MCP server needs no credential store.
    _agent(destructive_server([]))


def test_oauth_mcp_server_requires_a_store():
    with pytest.raises(ValueError, match='store is required'):
        _agent(destructive_server([]), redirect_uri=_REDIRECT_URI)


async def test_respond_language_instruction_names_the_user_language():
    model = TestModel()
    agent = _agent(model=model)

    await agent.run(
        'hi',
        principal=_PRINCIPAL,
        chat_context=_DIRECT,
        history=[],
        pending_approval=None,
        surface=RecordingSurface(Lang.ZH_TW),
    )

    params = model.last_model_request_parameters
    assert params is not None and params.instruction_parts is not None
    instructions = ' '.join(part.content for part in params.instruction_parts)
    assert 'Traditional Chinese' in instructions


async def test_respond_language_falls_back_to_mirroring_without_a_language():
    model = TestModel()
    agent = _agent(model=model)

    await agent.run(
        'hi',
        principal=_PRINCIPAL,
        chat_context=_DIRECT,
        history=[],
        pending_approval=None,
        surface=RecordingSurface(None),
    )

    params = model.last_model_request_parameters
    assert params is not None and params.instruction_parts is not None
    instructions = ' '.join(part.content for part in params.instruction_parts)
    assert 'same language as the most recent user message' in instructions


async def test_approval_card_is_localized_to_the_user_language():
    calls: list[int] = []
    agent = _agent(destructive_server(calls))

    result = await agent.run(
        'delete it',
        principal=_PRINCIPAL,
        chat_context=_DIRECT,
        history=[],
        pending_approval=None,
        surface=RecordingSurface(Lang.ZH_TW),
    )

    assert isinstance(result.output, PendingApproval)
    assert result.output.card.header == i18n.t('approval.header', Lang.ZH_TW)
    assert i18n.t('approval.confirm', Lang.ZH_TW) in [s.label for s in result.output.card.suggestions]


async def test_confirm_in_the_user_language_runs_the_tool():
    calls: list[int] = []
    agent = _agent(destructive_server(calls))
    surface = RecordingSurface(Lang.ZH_TW)

    parked = await agent.run(
        'delete it', principal=_PRINCIPAL, chat_context=_DIRECT, history=[], pending_approval=None, surface=surface
    )
    assert isinstance(parked.output, PendingApproval)

    resumed = await agent.run(
        i18n.t('approval.confirm', Lang.ZH_TW),
        principal=_PRINCIPAL,
        chat_context=_DIRECT,
        history=[],
        pending_approval=parked.output.state,
        surface=surface,
    )

    assert not isinstance(resumed.output, PendingApproval)
    assert len(calls) == 1  # the localized confirm matched and ran the tool


def _oauth_agent(
    credential_store: CredentialStore, *, servers: list[str] | None = None, **options: typing.Any
) -> ChatRunner:
    return _agent(
        *(servers if servers is not None else [_OAUTH_SERVER]),
        credential_store=credential_store,
        redirect_uri=_REDIRECT_URI,
        **options,
    )


def test_revoke_tool_is_registered_and_gated_only_when_oauth_is_configured(credential_store: CredentialStore):
    with_oauth = _oauth_agent(credential_store)
    revoke_tool = with_oauth._agent._function_toolset.tools[REVOKE_TOOL_NAME]
    assert revoke_tool.requires_approval is True  # the sign-out only runs after the user confirms

    public = _agent(destructive_server([]))
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
    agent = _agent(model=model)
    formatting_hint = 'MARKER-DIALECT-HINT'

    class MarkerSurface(RecordingSurface):
        @property
        def text_formatting_instruction(self) -> str:
            return formatting_hint

    await agent.run(
        'hi', principal=_PRINCIPAL, chat_context=_DIRECT, history=[], pending_approval=None, surface=MarkerSurface()
    )

    params = model.last_model_request_parameters
    assert params is not None
    assert params.instruction_parts is not None
    instructions = ' '.join(part.content for part in params.instruction_parts)
    assert _SYSTEM_PROMPT in instructions
    assert formatting_hint in instructions


async def test_group_chats_are_supported_without_oauth():
    agent = _agent(destructive_server([]))

    assert await agent.works_without_user_credentials() is True


async def test_group_chats_follow_the_known_authorization_without_probing(
    credential_store: CredentialStore, monkeypatch: pytest.MonkeyPatch
):
    async def no_probe(url: str, **_: object) -> bool | None:
        raise AssertionError('a server with a known verdict is never probed')

    monkeypatch.setattr(runner_module, 'requires_user_authorization', no_probe)
    shared = _oauth_agent(credential_store, known_user_authorization={_OAUTH_SERVER: False})
    per_user = _oauth_agent(credential_store, known_user_authorization={_OAUTH_SERVER: True})

    assert await shared.works_without_user_credentials() is True
    assert await per_user.works_without_user_credentials() is False


async def test_an_unreachable_server_pauses_group_chats_until_a_probe_decides(
    credential_store: CredentialStore, monkeypatch: pytest.MonkeyPatch
):
    verdicts: list[bool | None] = [None, False]
    probed: list[str] = []

    async def probe(url: str, **_: object) -> bool | None:
        probed.append(url)
        return verdicts.pop(0)

    monkeypatch.setattr(runner_module, 'requires_user_authorization', probe)
    agent = _oauth_agent(credential_store)

    assert await agent.works_without_user_credentials() is False
    assert (
        await agent.works_without_user_credentials() is False
    )  # still inside the retry interval, so no second probe yet
    agent._user_credentials_check_retry_at = 0.0  # the retry interval has passed
    assert await agent.works_without_user_credentials() is True
    assert await agent.works_without_user_credentials() is True  # a reached verdict is cached
    assert probed == [_OAUTH_SERVER, _OAUTH_SERVER]


async def test_concurrent_credential_free_checks_share_one_probe_of_each_server(
    credential_store: CredentialStore, monkeypatch: pytest.MonkeyPatch
):
    servers = ['https://a.example/mcp', 'https://b.example/mcp']
    probed: list[str] = []
    both_started = asyncio.Event()

    async def probe(url: str, **_: object) -> bool | None:
        probed.append(url)
        if len(probed) == len(servers):
            both_started.set()
        # The servers are probed side by side, so neither finishes before both have started.
        await both_started.wait()
        return False

    monkeypatch.setattr(runner_module, 'requires_user_authorization', probe)
    agent = _oauth_agent(credential_store, servers=servers)

    verdicts = await asyncio.gather(*(agent.works_without_user_credentials() for _ in range(5)))

    assert verdicts == [True] * 5
    assert sorted(probed) == servers


async def test_a_group_turn_names_its_speaker_and_hides_the_sign_out(credential_store: CredentialStore):
    model = TestModel(call_tools=[])
    agent = _oauth_agent(credential_store, model=model)
    # Keep the sign-out tool registered but connect to nothing, so the turn needs no live MCP server.
    agent._mcp_servers = []

    await agent.run('hi', principal=_PRINCIPAL, chat_context=_DIRECT, history=[], pending_approval=None)
    direct = model.last_model_request_parameters
    result = await agent.run(
        'hi', principal=_PRINCIPAL, history=[], pending_approval=None, chat_context=GroupChatContext('g', 'Amy')
    )
    group = model.last_model_request_parameters

    assert direct is not None and group is not None
    assert REVOKE_TOOL_NAME in {tool.name for tool in direct.function_tools}
    assert REVOKE_TOOL_NAME not in {tool.name for tool in group.function_tools}
    instructions = ' '.join(part.content for part in group.instruction_parts or [])
    assert 'group chat' in instructions
    first_request = ModelMessagesTypeAdapter.validate_python(result.history)[0]
    assert any(getattr(part, 'content', None) == '[Amy] hi' for part in first_request.parts)


def _unattended(**overrides: typing.Any) -> UnattendedChatContext:
    started_at = datetime.datetime(2026, 10, 26, 9, tzinfo=zoneinfo.ZoneInfo('Asia/Taipei'))
    return UnattendedChatContext('line:group:C1', push_name='reminder', started_at=started_at, **overrides)


async def test_an_unattended_turn_may_end_with_skip_reply():
    offered: list[list[str]] = []

    def skip(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        offered.append([tool.name for tool in info.output_tools])
        return ModelResponse(parts=[ToolCallPart('skip_reply', {'reason': 'nothing due'})])

    agent = ChatRunner(model=FunctionModel(skip), system_prompt=_SYSTEM_PROMPT)

    result = await agent.run(
        'remind', principal=_PRINCIPAL, history=[], pending_approval=None, chat_context=_unattended(allows_skip=True)
    )

    assert result.output == SkippedReply(reason='nothing due')
    assert 'skip_reply' in offered[0]


async def test_skip_reply_is_offered_only_where_the_chat_allows_it():
    model = TestModel(call_tools=[])
    agent = _agent(model=model)

    for chat_context in (_DIRECT, _unattended(allows_skip=False)):
        await agent.run('hi', principal=_PRINCIPAL, history=[], pending_approval=None, chat_context=chat_context)
        parameters = model.last_model_request_parameters
        assert parameters is not None
        assert 'skip_reply' not in {tool.name for tool in parameters.output_tools}


async def test_an_unattended_turn_states_its_name_time_and_weekday():
    model = TestModel(call_tools=[])
    agent = _agent(model=model)

    result = await agent.run(
        'remind', principal=_PRINCIPAL, history=[], pending_approval=None, chat_context=_unattended()
    )

    first_request = ModelMessagesTypeAdapter.validate_python(result.history)[0]
    prompt = '[Automated: reminder, 2026-10-26T09:00+08:00 Asia/Taipei, Monday] remind'
    assert any(getattr(part, 'content', None) == prompt for part in first_request.parts)


async def test_an_unattended_turn_hides_destructive_tools_by_default():
    calls: list[int] = []
    model = TestModel()
    agent = _agent(destructive_server(calls), model=model)

    result = await agent.run(
        'delete it', principal=_PRINCIPAL, history=[], pending_approval=None, chat_context=_unattended()
    )

    parameters = model.last_model_request_parameters
    assert parameters is not None
    assert 'delete_it' not in {tool.name for tool in parameters.function_tools}
    assert not isinstance(result.output, PendingApproval)
    assert calls == []


async def test_an_unattended_turn_allowed_destructive_tools_runs_them_unasked():
    calls: list[int] = []
    agent = _agent(destructive_server(calls))

    result = await agent.run(
        'delete it',
        principal=_PRINCIPAL,
        history=[],
        pending_approval=None,
        chat_context=_unattended(runs_destructive_tools=True),
    )

    assert not isinstance(result.output, PendingApproval)
    assert len(calls) == 1


def _chart_spec() -> dict[str, typing.Any]:
    return {
        'data': {'values': [{'day': 'Mon', 'count': 3}, {'day': 'Tue', 'count': 5}]},
        'mark': 'bar',
        'encoding': {'x': {'field': 'day', 'type': 'nominal'}, 'y': {'field': 'count', 'type': 'quantitative'}},
    }


def _charting_model(*specs: typing.Any, hero_image_urls: collections.abc.Sequence[str] = ()) -> FunctionModel:
    """A model that calls create_chart once per spec, then replies with a card showing the last returned URL.

    ``hero_image_urls`` are the URLs its cards show first, one per reply, for a model that mistypes or makes one up.
    """
    remaining = list(specs)
    wrong_urls = list(hero_image_urls)

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        if remaining:
            return ModelResponse(parts=[ToolCallPart(CREATE_CHART_TOOL_NAME, {'spec': remaining.pop(0)})])
        returned = [
            part.content
            for message in messages
            for part in message.parts
            if isinstance(part, ToolReturnPart) and part.tool_name == CREATE_CHART_TOOL_NAME
        ]
        url = wrong_urls.pop(0) if wrong_urls else returned[-1]
        card = {'header': 'Counts', 'hero_image_url': url, 'sections': [{'text': 'Tuesday was higher.'}]}
        return ModelResponse(parts=[ToolCallPart('reply_card', card)])

    return FunctionModel(respond)


def _retries(result: TurnResult) -> list[RetryPromptPart]:
    return [
        part
        for message in ModelMessagesTypeAdapter.validate_python(result.history)
        for part in message.parts
        if isinstance(part, RetryPromptPart)
    ]


async def test_a_created_chart_is_shown_as_the_card_hero_image(chart_publisher, chart_store):
    agent = _agent(model=_charting_model(_chart_spec()), capabilities=[ChartCapability(chart_publisher)])

    result = await agent.run('chart it', principal=_PRINCIPAL, chat_context=_DIRECT, history=[], pending_approval=None)

    card = result.output
    assert isinstance(card, Card)
    assert card.hero_image_url is not None
    prefix = 'https://bot.example.com/charts/'
    assert card.hero_image_url.startswith(prefix) and card.hero_image_url.endswith('.png')
    chart_id = card.hero_image_url.removeprefix(prefix).removesuffix('.png')
    assert (await chart_store.get(chart_id) or b'').startswith(b'\x89PNG')


async def test_a_refused_spec_is_retried_by_the_model(chart_publisher):
    remote = {**_chart_spec(), 'data': {'url': 'https://example.com/data.json'}}
    agent = _agent(model=_charting_model(remote, _chart_spec()), capabilities=[ChartCapability(chart_publisher)])

    result = await agent.run('chart it', principal=_PRINCIPAL, chat_context=_DIRECT, history=[], pending_approval=None)

    assert isinstance(result.output, Card)
    assert result.output.hero_image_url is not None
    retries = _retries(result)
    assert len(retries) == 1 and 'data.values' in str(retries[0].content)


async def test_a_chart_url_that_names_no_chart_sends_the_model_back(chart_publisher, chart_store):
    made_up = 'https://bot.example.com/charts/made-up-chart-id-0123456789.png'
    agent = _agent(
        model=_charting_model(_chart_spec(), hero_image_urls=[made_up]), capabilities=[ChartCapability(chart_publisher)]
    )

    result = await agent.run('chart it', principal=_PRINCIPAL, chat_context=_DIRECT, history=[], pending_approval=None)

    card = result.output
    assert isinstance(card, Card)
    assert card.hero_image_url is not None and card.hero_image_url != made_up
    assert await chart_store.exists(card.hero_image_url.removeprefix('https://bot.example.com/charts/')[:-4])
    retries = _retries(result)
    assert len(retries) == 1 and CREATE_CHART_TOOL_NAME in str(retries[0].content)


async def test_a_hero_image_from_elsewhere_is_left_alone(chart_publisher):
    photo = 'https://images.example.com/template.png'
    agent = _agent(model=_charting_model(hero_image_urls=[photo]), capabilities=[ChartCapability(chart_publisher)])

    result = await agent.run('show it', principal=_PRINCIPAL, chat_context=_DIRECT, history=[], pending_approval=None)

    assert isinstance(result.output, Card)
    assert result.output.hero_image_url == photo
    assert _retries(result) == []


async def test_without_charts_the_tool_is_absent():
    offered: list[list[str]] = []

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        offered.append([tool.name for tool in info.function_tools])
        return ModelResponse(parts=[ToolCallPart('reply_card', {'header': 'Counts', 'sections': [{'text': 'x'}]})])

    agent = _agent(model=FunctionModel(respond))

    await agent.run('chart it', principal=_PRINCIPAL, chat_context=_DIRECT, history=[], pending_approval=None)

    assert offered and all(CREATE_CHART_TOOL_NAME not in names for names in offered)
