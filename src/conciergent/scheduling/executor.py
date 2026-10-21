import collections.abc
import datetime
import logging

from conciergent.agent.compactor import HistorySummarizer
from conciergent.agent.mcp.tool_policy import HideDestructiveTools, RunDestructiveTools
from conciergent.agent.runner import ChatRunner
from conciergent.chat_context import ScheduledChatContext
from conciergent.config import ScheduleSettings
from conciergent.defaults import DEFAULTS
from conciergent.identity import ChatSurface
from conciergent.runtime import HeadlessOAuthBridge
from conciergent.store.message import MessageStore
from conciergent.surfaces.base import PushSurface, PushTarget
from conciergent.turn import run_turn


logger = logging.getLogger(__name__)

# How long a firing's lock outlives it, long enough that no replica with a lagging clock runs it again.
_FIRING_LOCK_TTL_SECONDS = 86400


class ScheduleExecutor:
    """Run one firing of a schedule as an unattended turn and push the reply into the schedule's target chat.

    What a firing does stays the same whatever decides when it happens, so every trigger hands its firings here.
    A Redis lock per firing lets exactly one replica, or one trigger, run it.
    """

    def __init__(
        self,
        *,
        surfaces: collections.abc.Mapping[ChatSurface, PushSurface],
        runner: ChatRunner,
        message_store: MessageStore,
        compactor: HistorySummarizer | None = None,
        history_ttl_seconds: int = DEFAULTS.conversation.history_ttl_seconds,
    ) -> None:
        self._surfaces = surfaces
        self._runner = runner
        self._message_store = message_store
        self._compactor = compactor
        self._history_ttl_seconds = history_ttl_seconds

    async def fire(self, schedule: ScheduleSettings, fired_at: datetime.datetime) -> None:
        """Run the firing of ``schedule`` due at ``fired_at``, unless it already ran."""
        lock_key = f'schedule:{schedule.name}:{fired_at.isoformat()}'
        if await self._message_store.dedupe(lock_key, ttl_seconds=_FIRING_LOCK_TTL_SECONDS):
            return
        surface = self._surfaces[schedule.target.surface]
        async with surface.push_target(schedule.target.to) as target:
            try:
                await self._run_turn(schedule, fired_at, target)
            except Exception:
                logger.exception('Schedule %s failed at %s', schedule.name, fired_at.isoformat())
                if schedule.failure_message:
                    await target.surface.send_text(schedule.failure_message)

    async def _run_turn(self, schedule: ScheduleSettings, fired_at: datetime.datetime, target: PushTarget) -> None:
        chat_context = ScheduledChatContext(
            conversation=target.conversation,
            schedule=schedule.name,
            fired_at=fired_at,
            has_principal=schedule.principal is not None,
            skip_allowed=schedule.allow_skip,
            destructive_tool_policy=RunDestructiveTools() if schedule.allow_destructive else HideDestructiveTools(),
        )
        await run_turn(
            schedule.prompt,
            # Without a principal the turn reaches no one's credentials, so this only labels it.
            principal=schedule.principal or f'schedule:{schedule.name}',
            chat_context=chat_context,
            runner=self._runner,
            surface=target.surface,
            message_store=self._message_store,
            bridge=HeadlessOAuthBridge() if schedule.principal is not None else None,
            compactor=self._compactor,
            history_ttl_seconds=self._history_ttl_seconds,
        )
