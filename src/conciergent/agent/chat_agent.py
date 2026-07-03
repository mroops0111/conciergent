import collections.abc
import contextlib
import dataclasses
import typing

import pydantic
from pydantic_ai import Agent, RunContext, ToolOutput
from pydantic_ai.mcp import MCPToolsetClient
from pydantic_ai.messages import ModelMessage, ModelMessagesTypeAdapter, ToolCallPart
from pydantic_ai.models import Model
from pydantic_ai.output import OutputSpec
from pydantic_ai.tools import DeferredToolRequests, DeferredToolResults, ToolDenied

from conciergent import i18n
from conciergent.defaults import DEFAULTS
from conciergent.lang import Lang
from conciergent.mcp.client import _DEFAULT_CLIENT_NAME, ApprovalPredicate, build_toolset, needs_approval
from conciergent.reply import Card, Carousel, Reply, ReplySurface, Section, Suggestion
from conciergent.runtime import AgentResult, AuthorizationProbe, ChatAgent, OAuthBridge, PendingApproval
from conciergent.stores.base import CredentialStore


_CANCEL_DENIAL = 'User pressed Cancel. Acknowledge briefly in their language; do not retry or imply a permission error.'
_IGNORED_DENIAL = (
    'User skipped the approval and changed topic. Drop the pending action silently and answer their new message.'
)


@dataclasses.dataclass
class AgentDeps:
    """Per-turn context the agent's instructions and rendering read, carried through pydantic-ai deps."""

    surface: ReplySurface | None
    lang: Lang | None


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
        mcp_read_timeout_seconds: float = DEFAULTS.agent.mcp_read_timeout_seconds,
    ) -> None:
        if mcp_servers and store is None:
            raise ValueError('store is required when mcp_servers are configured')
        self._mcp_servers = list(mcp_servers)
        self._store = store
        self._redirect_uri = redirect_uri
        self._approval_predicate = approval_predicate
        self._client_name = client_name
        self._mcp_read_timeout_seconds = mcp_read_timeout_seconds
        output_type: OutputSpec[Reply | DeferredToolRequests] = [
            str,
            ToolOutput(Card, name='reply_card'),
            ToolOutput(Carousel, name='reply_carousel'),
            DeferredToolRequests,
        ]
        self._agent: Agent[AgentDeps, Reply | DeferredToolRequests] = Agent(
            model,
            deps_type=AgentDeps,
            output_type=output_type,
            instructions=system_prompt,
            retries=3,
        )

        @self._agent.instructions
        def surface_formatting(ctx: RunContext[AgentDeps]) -> str:
            # Each surface renders a different dialect, so its hint joins the system prompt per turn.
            surface = ctx.deps.surface
            return surface.text_formatting_instruction if surface is not None else ''

        @self._agent.instructions
        def respond_language(ctx: RunContext[AgentDeps]) -> str:
            # Answer in the user's own language; with no resolved language, mirror their latest message.
            lang = ctx.deps.lang
            if lang is None:
                return 'Respond in the same language as the most recent user message.'
            return f'Respond in {lang.display_name}.'

    @property
    def mcp_servers(self) -> tuple[MCPToolsetClient, ...]:
        """The MCP servers this agent connects to, exposed for assembly-time introspection."""
        return tuple(self._mcp_servers)

    @typing.override
    async def bootstrap(self, principal: str, *, bridge: OAuthBridge | None = None) -> bool:
        """Open every MCP connection without running the agent, firing any pending OAuth flow now."""
        if not self._mcp_servers:
            return False
        probe = AuthorizationProbe(bridge) if bridge is not None else None
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
        lang = surface.lang if surface is not None else None
        deps = AgentDeps(surface=surface, lang=lang)
        resumption = (
            self._try_resume(pending, user_input=user_input, history=history, lang=lang)
            if pending is not None
            else None
        )
        if resumption is not None:
            prompt, messages, deferred, held = resumption
            result = await self._agent.run(
                prompt, message_history=messages, deferred_tool_results=deferred, toolsets=toolsets, deps=deps
            )
        else:
            held = []
            result = await self._agent.run(
                user_input, message_history=_decode_history(history), toolsets=toolsets, deps=deps
            )

        output = result.output
        new_messages = [*held, *ModelMessagesTypeAdapter.dump_python(result.new_messages(), mode='json')]
        if isinstance(output, DeferredToolRequests):
            # The in-flight messages ride on the approval,
            # so the tool call and its later result land in one stored turn instead of aging out separately.
            return AgentResult(output=self._park(output.approvals, held_messages=new_messages, lang=lang))
        return AgentResult(output=output, history=new_messages)

    def _try_resume(
        self, pending: dict[str, typing.Any], *, user_input: str, history: list[typing.Any], lang: Lang | None
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
        # The parked card offered these exact prompts in the user's language, so a tap comes back matching them.
        confirm_prompt = i18n.t('approval.confirm', lang)
        cancel_prompt = i18n.t('approval.cancel', lang)
        # One confirm or cancel decides every tool deferred in the parked turn,
        # so the resumed run has a result for each pending call and never rejects the batch as unsatisfied.
        decision: bool | ToolDenied
        if user_input == confirm_prompt:
            return None, messages, DeferredToolResults(approvals=dict.fromkeys(tool_call_ids, True)), held
        if user_input == cancel_prompt:
            decision = ToolDenied(_CANCEL_DENIAL)
            return None, messages, DeferredToolResults(approvals=dict.fromkeys(tool_call_ids, decision)), held
        decision = ToolDenied(_IGNORED_DENIAL)
        return user_input, messages, DeferredToolResults(approvals=dict.fromkeys(tool_call_ids, decision)), held

    def _park(
        self, approvals: list[ToolCallPart], *, held_messages: list[typing.Any], lang: Lang | None
    ) -> PendingApproval:
        tool_names = ', '.join(call.tool_name for call in approvals)
        card = Card(
            title=i18n.t('approval.header', lang),
            sections=[Section(text=i18n.t('approval.body', lang, tools=tool_names))],
            suggestions=[
                Suggestion(label=i18n.t('approval.confirm', lang), prompt=i18n.t('approval.confirm', lang)),
                Suggestion(label=i18n.t('approval.cancel', lang), prompt=i18n.t('approval.cancel', lang)),
            ],
        )
        state = {
            'tool_call_ids': [call.tool_call_id for call in approvals],
            'held_messages': held_messages,
        }
        return PendingApproval(card=card, state=state)


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
