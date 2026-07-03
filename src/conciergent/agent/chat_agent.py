import collections.abc
import contextlib
import typing

import pydantic
from pydantic_ai import Agent, RunContext, ToolOutput
from pydantic_ai.mcp import MCPToolsetClient
from pydantic_ai.messages import ModelMessage, ModelMessagesTypeAdapter, ToolCallPart
from pydantic_ai.models import Model
from pydantic_ai.output import OutputSpec
from pydantic_ai.tools import DeferredToolRequests, DeferredToolResults, ToolDenied

from ..mcp.client import (
    _DEFAULT_CLIENT_NAME,
    DEFAULT_READ_TIMEOUT_SECONDS,
    ApprovalPredicate,
    build_toolset,
    needs_approval,
)
from ..reply import Card, Carousel, Reply, ReplySurface, Section, Suggestion
from ..runtime import AgentResult, ChatAgent, OAuthBridge, PendingApproval
from ..stores.base import CredentialStore


_DEFAULT_CONFIRM_LABEL = 'Confirm'
_DEFAULT_CANCEL_LABEL = 'Cancel'
_DEFAULT_CONFIRM_PROMPT = 'Confirm'
_DEFAULT_CANCEL_PROMPT = 'Cancel'
_DEFAULT_APPROVAL_TITLE = 'Confirm'
_DEFAULT_APPROVAL_BODY = 'I am about to "{tools}". This action may not be undone. Confirm to proceed.'
_CANCEL_DENIAL = 'User pressed Cancel. Acknowledge briefly in their language; do not retry or imply a permission error.'
_IGNORED_DENIAL = (
    'User skipped the approval and changed topic. Drop the pending action silently and answer their new message.'
)


class PydanticAIAgent(ChatAgent):
    """The batteries-included agent on Pydantic AI, the only marketed agent path.

    The reply model is its structured-output schema, MCP tools connect over Streamable HTTP,
    and any tool the server marks destructive pauses for an in-chat confirmation before it runs.
    """

    def __init__(
        self,
        *,
        model: Model | str,
        system_prompt: str,
        mcp_servers: collections.abc.Sequence[MCPToolsetClient] = (),
        store: CredentialStore | None = None,
        redirect_uri: str | None = None,
        approval_predicate: ApprovalPredicate = needs_approval,
        client_name: str = _DEFAULT_CLIENT_NAME,
        mcp_read_timeout_seconds: float = DEFAULT_READ_TIMEOUT_SECONDS,
        confirm_label: str = '',
        cancel_label: str = '',
        confirm_prompt: str = '',
        cancel_prompt: str = '',
        approval_title: str = '',
        approval_body: str = '',
    ) -> None:
        if mcp_servers and store is None:
            raise ValueError('store is required when mcp_servers are configured')
        self._mcp_servers = list(mcp_servers)
        self._store = store
        self._redirect_uri = redirect_uri
        self._approval_predicate = approval_predicate
        self._client_name = client_name
        self._mcp_read_timeout_seconds = mcp_read_timeout_seconds
        # An empty text selects the English default, which lets config pass fields through unconditionally.
        self._confirm_label = confirm_label or _DEFAULT_CONFIRM_LABEL
        self._cancel_label = cancel_label or _DEFAULT_CANCEL_LABEL
        self._confirm_prompt = confirm_prompt or _DEFAULT_CONFIRM_PROMPT
        self._cancel_prompt = cancel_prompt or _DEFAULT_CANCEL_PROMPT
        self._approval_title = approval_title or _DEFAULT_APPROVAL_TITLE
        self._approval_body = approval_body or _DEFAULT_APPROVAL_BODY
        output_type: OutputSpec[Reply | DeferredToolRequests] = [
            str,
            ToolOutput(Card, name='reply_card'),
            ToolOutput(Carousel, name='reply_carousel'),
            DeferredToolRequests,
        ]
        self._agent: Agent[ReplySurface | None, Reply | DeferredToolRequests] = Agent(
            model,
            deps_type=ReplySurface | None,
            output_type=output_type,
            instructions=system_prompt,
            retries=3,
        )

        @self._agent.instructions
        def surface_formatting(ctx: RunContext[ReplySurface | None]) -> str:
            # Each surface renders a different dialect, so its hint joins the system prompt per turn.
            return ctx.deps.text_formatting_instruction if ctx.deps is not None else ''

    @property
    def mcp_servers(self) -> tuple[MCPToolsetClient, ...]:
        """The MCP servers this agent connects to, exposed for assembly-time introspection."""
        return tuple(self._mcp_servers)

    @typing.override
    async def bootstrap(self, principal: str, *, bridge: OAuthBridge | None = None) -> bool:
        """Open every MCP connection without running the agent, firing any pending OAuth flow now."""
        if not self._mcp_servers:
            return False
        probe = _AuthorizationProbe(bridge) if bridge is not None else None
        toolsets = [
            build_toolset(
                server,
                principal=principal,
                store=self._store,
                bridge=probe,
                redirect_uri=self._redirect_uri,
                approval_predicate=self._approval_predicate,
                client_name=self._client_name,
                read_timeout_seconds=self._mcp_read_timeout_seconds,
            )
            for server in self._mcp_servers
        ]
        async with contextlib.AsyncExitStack() as stack:
            for toolset in toolsets:
                await stack.enter_async_context(toolset)
        return probe.authorized if probe is not None else False

    @typing.override
    async def run(
        self,
        user_input: str,
        *,
        principal: str,
        history: list[typing.Any],
        pending: dict[str, typing.Any] | None,
        bridge: OAuthBridge | None = None,
        surface: ReplySurface | None = None,
    ) -> AgentResult:
        toolsets = [
            build_toolset(
                server,
                principal=principal,
                store=self._store,
                bridge=bridge,
                redirect_uri=self._redirect_uri,
                approval_predicate=self._approval_predicate,
                client_name=self._client_name,
                read_timeout_seconds=self._mcp_read_timeout_seconds,
            )
            for server in self._mcp_servers
        ]
        resumption = self._try_resume(pending, user_input=user_input, history=history) if pending is not None else None
        if resumption is not None:
            prompt, messages, deferred, held = resumption
            result = await self._agent.run(
                prompt, message_history=messages, deferred_tool_results=deferred, toolsets=toolsets, deps=surface
            )
        else:
            held = []
            result = await self._agent.run(
                user_input, message_history=_decode_history(history), toolsets=toolsets, deps=surface
            )

        output = result.output
        new_messages = [*held, *ModelMessagesTypeAdapter.dump_python(result.new_messages(), mode='json')]
        if isinstance(output, DeferredToolRequests):
            # The in-flight messages ride on the approval,
            # so the tool call and its later result land in one stored turn instead of aging out separately.
            return AgentResult(output=self._park(output.approvals, held_messages=new_messages))
        return AgentResult(output=output, history=new_messages)

    def _try_resume(
        self, pending: dict[str, typing.Any], *, user_input: str, history: list[typing.Any]
    ) -> tuple[str | None, list[ModelMessage], DeferredToolResults, list[typing.Any]] | None:
        """Rebuild the deferred run from parked state, or None when the state is unreadable.

        Parked state is a disposable cache in the agent library's own format.
        An unreadable one is treated as an expired approval and the message runs as a fresh turn.
        """
        try:
            tool_call_ids: list[str] = pending['tool_call_ids']
            held: list[typing.Any] = pending['held_messages']
            messages = ModelMessagesTypeAdapter.validate_python([*history, *held])
        except (KeyError, pydantic.ValidationError):
            return None
        # One confirm or cancel decides every tool deferred in the parked turn,
        # so the resumed run has a result for each pending call and never rejects the batch as unsatisfied.
        decision: bool | ToolDenied
        if user_input == self._confirm_prompt:
            return None, messages, DeferredToolResults(approvals=dict.fromkeys(tool_call_ids, True)), held
        if user_input == self._cancel_prompt:
            decision = ToolDenied(_CANCEL_DENIAL)
            return None, messages, DeferredToolResults(approvals=dict.fromkeys(tool_call_ids, decision)), held
        decision = ToolDenied(_IGNORED_DENIAL)
        return user_input, messages, DeferredToolResults(approvals=dict.fromkeys(tool_call_ids, decision)), held

    def _park(self, approvals: list[ToolCallPart], *, held_messages: list[typing.Any]) -> PendingApproval:
        tool_names = ', '.join(call.tool_name for call in approvals)
        card = Card(
            title=self._approval_title,
            sections=[Section(text=self._approval_body.format(tools=tool_names))],
            suggestions=[
                Suggestion(label=self._confirm_label, prompt=self._confirm_prompt),
                Suggestion(label=self._cancel_label, prompt=self._cancel_prompt),
            ],
        )
        state = {
            'tool_call_ids': [call.tool_call_id for call in approvals],
            'held_messages': held_messages,
        }
        return PendingApproval(card=card, state=state)


class _AuthorizationProbe(OAuthBridge):
    """Delegate to the real bridge while recording whether an authorization actually ran.

    The SDK only invokes the redirect and callback pair when a real OAuth flow is needed,
    so a completed delegation is exactly the just-authorized signal bootstrap reports.
    """

    def __init__(self, inner: OAuthBridge) -> None:
        self._inner = inner
        self.authorized = False

    @typing.override
    async def request_authorization(self, authorize_url: str) -> str:
        code = await self._inner.request_authorization(authorize_url)
        self.authorized = True
        return code


def _decode_history(history: list[typing.Any]) -> list[ModelMessage] | None:
    """Decode stored history, or start the conversation fresh when it does not decode.

    Stored history is a disposable cache in the agent library's own format,
    so an undecodable one must never wedge the conversation.
    """
    if not history:
        return None
    try:
        return ModelMessagesTypeAdapter.validate_python(history)
    except pydantic.ValidationError:
        return None
