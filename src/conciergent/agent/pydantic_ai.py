import collections.abc
import typing

import pydantic
from pydantic_ai import Agent, ToolOutput
from pydantic_ai.mcp import MCPToolsetClient
from pydantic_ai.messages import ModelMessage, ModelMessagesTypeAdapter, ToolCallPart
from pydantic_ai.models import Model
from pydantic_ai.output import OutputSpec
from pydantic_ai.tools import DeferredToolRequests, DeferredToolResults, ToolDenied

from ..mcp.client import _DEFAULT_CLIENT_NAME, ApprovalPredicate, build_toolset, needs_approval
from ..reply import Card, Carousel, Reply, Section, Suggestion
from ..runtime import AgentResult, ChatAgent, OAuthBridge, PendingApproval
from ..stores.base import Store


_DEFAULT_CONFIRM_LABEL = 'Confirm'
_DEFAULT_CANCEL_LABEL = 'Cancel'
_DEFAULT_CONFIRM_PROMPT = 'Confirm'
_DEFAULT_CANCEL_PROMPT = 'Cancel'
_APPROVAL_TITLE = 'Confirm'
_APPROVAL_BODY = 'I am about to "{tools}". This action may not be undone. Confirm to proceed.'
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
        store: Store | None = None,
        bridge: OAuthBridge | None = None,
        redirect_uri: str | None = None,
        approval_predicate: ApprovalPredicate = needs_approval,
        client_name: str = _DEFAULT_CLIENT_NAME,
        confirm_label: str = _DEFAULT_CONFIRM_LABEL,
        cancel_label: str = _DEFAULT_CANCEL_LABEL,
        confirm_prompt: str = _DEFAULT_CONFIRM_PROMPT,
        cancel_prompt: str = _DEFAULT_CANCEL_PROMPT,
    ) -> None:
        if mcp_servers and store is None:
            raise ValueError('store is required when mcp_servers are configured')
        self._mcp_servers = list(mcp_servers)
        self._store = store
        self._bridge = bridge
        self._redirect_uri = redirect_uri
        self._approval_predicate = approval_predicate
        self._client_name = client_name
        self._confirm_label = confirm_label
        self._cancel_label = cancel_label
        self._confirm_prompt = confirm_prompt
        self._cancel_prompt = cancel_prompt
        output_type: OutputSpec[Reply | DeferredToolRequests] = [
            str,
            ToolOutput(Card, name='reply_card'),
            ToolOutput(Carousel, name='reply_carousel'),
            DeferredToolRequests,
        ]
        self._agent: Agent[None, Reply | DeferredToolRequests] = Agent(
            model,
            output_type=output_type,
            instructions=system_prompt,
            retries=3,
        )

    async def run(
        self,
        user_input: str,
        *,
        principal: str,
        history: list[typing.Any],
        pending: dict[str, typing.Any] | None,
    ) -> AgentResult:
        toolsets = [
            build_toolset(
                server,
                principal=principal,
                store=self._store,
                bridge=self._bridge,
                redirect_uri=self._redirect_uri,
                approval_predicate=self._approval_predicate,
                client_name=self._client_name,
            )
            for server in self._mcp_servers
        ]
        resumption = self._try_resume(pending, user_input=user_input, history=history) if pending is not None else None
        if resumption is not None:
            prompt, messages, deferred, held = resumption
            result = await self._agent.run(
                prompt, message_history=messages, deferred_tool_results=deferred, toolsets=toolsets
            )
        else:
            held = []
            result = await self._agent.run(user_input, message_history=_decode_history(history), toolsets=toolsets)

        output = result.output
        new_messages = [*held, *ModelMessagesTypeAdapter.dump_python(result.new_messages(), mode='json')]
        if isinstance(output, DeferredToolRequests):
            # The in-flight messages ride on the approval, so the tool call and its later result land in
            # one stored turn instead of aging out separately.
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
            title=_APPROVAL_TITLE,
            sections=[Section(text=_APPROVAL_BODY.format(tools=tool_names))],
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
