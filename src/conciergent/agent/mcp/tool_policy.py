import abc
import collections.abc
import typing

from pydantic_ai import RunContext
from pydantic_ai.tools import ToolDefinition
from pydantic_ai.toolsets import AbstractToolset


type ApprovalPredicate = collections.abc.Callable[[RunContext[typing.Any], ToolDefinition, dict[str, typing.Any]], bool]


class DestructiveToolPolicy(abc.ABC):
    """How a turn treats the tools an approval predicate marks destructive.

    A chat with someone to ask confirms them, while an unattended one cannot,
    so each kind of chat picks a policy and the runner applies it without knowing which.
    """

    @abc.abstractmethod
    def apply(
        self, toolset: AbstractToolset[typing.Any], is_destructive: ApprovalPredicate
    ) -> AbstractToolset[typing.Any]:
        """Wrap ``toolset`` so its destructive tools behave as this policy says."""


class ConfirmDestructiveToolPolicy(DestructiveToolPolicy):
    """Pause before a destructive tool runs, until someone in the chat confirms it."""

    @typing.override
    def apply(
        self, toolset: AbstractToolset[typing.Any], is_destructive: ApprovalPredicate
    ) -> AbstractToolset[typing.Any]:
        return toolset.approval_required(is_destructive)


class HideDestructiveToolPolicy(DestructiveToolPolicy):
    """Leave destructive tools out of the turn, for a chat where nobody could confirm one."""

    @typing.override
    def apply(
        self, toolset: AbstractToolset[typing.Any], is_destructive: ApprovalPredicate
    ) -> AbstractToolset[typing.Any]:
        # The tool list is built before any call, so the predicate decides by the tool alone, with no arguments yet.
        return toolset.filtered(lambda ctx, tool_def: not is_destructive(ctx, tool_def, {}))


class RunDestructiveToolPolicy(DestructiveToolPolicy):
    """Run destructive tools without asking, which an operator must opt into knowingly."""

    @typing.override
    def apply(
        self, toolset: AbstractToolset[typing.Any], is_destructive: ApprovalPredicate
    ) -> AbstractToolset[typing.Any]:
        return toolset
