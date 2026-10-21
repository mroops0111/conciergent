import collections.abc
import datetime
import enum
import logging
import typing

from conciergent.agent.compactor import HistorySummarizer
from conciergent.agent.mcp.tool_policy import HideDestructiveToolPolicy, RunDestructiveToolPolicy
from conciergent.agent.runner import ChatRunner
from conciergent.chat_context import ScheduledChatContext
from conciergent.config import ScheduleSettings
from conciergent.defaults import DEFAULTS
from conciergent.i18n.lang import Lang
from conciergent.identity import ChatSurface
from conciergent.reply import Card, ReplySurface
from conciergent.runtime import UnattendedOAuthBridge
from conciergent.store.message import MessageStore
from conciergent.surfaces.base import PushSurface
from conciergent.turn import run_turn


logger = logging.getLogger(__name__)


class FiringOutcome(enum.StrEnum):
    """How a firing ended, judged by whether anything reached the chat, since that decides if a retry is safe."""

    delivered = 'delivered'
    skipped = 'skipped'
    # Nothing but the failure message reached the chat, so running the schedule again cannot repeat a reply.
    failed = 'failed'


class ScheduleExecutor:
    """Run one firing of a schedule as an unattended turn and push the reply into the schedule's target chat.

    When a schedule fires is up to whatever calls this,
    such as cron or a Kubernetes CronJob running ``conciergent fire``.
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

    async def fire(self, schedule: ScheduleSettings, fired_at: datetime.datetime) -> FiringOutcome:
        """Run ``schedule`` as fired at ``fired_at``, pushing the failure message when it fails before replying."""
        surface = self._surfaces[schedule.target.surface]
        try:
            async with surface.push_target(schedule.target.to) as target:
                delivery = _DeliveryRecorder(target.surface)
                try:
                    await self._run_turn(schedule, fired_at, target.conversation, delivery)
                except Exception:
                    logger.exception('Schedule %s failed at %s', schedule.name, fired_at.isoformat())
                    if delivery.delivered:
                        # The reply is out, so only a later step such as saving history failed.
                        return FiringOutcome.delivered
                    if schedule.failure_message:
                        await target.surface.send_text(schedule.failure_message)
                    return FiringOutcome.failed
                return FiringOutcome.delivered if delivery.delivered else FiringOutcome.skipped
        except Exception:
            # Even the failure message could not be pushed, so the log is all that is left.
            logger.exception('Schedule %s could not push to %s', schedule.name, schedule.target.to)
            return FiringOutcome.failed

    async def _run_turn(
        self, schedule: ScheduleSettings, fired_at: datetime.datetime, conversation: str, surface: ReplySurface
    ) -> None:
        # This is where the config meets the domain, so the context never sees the config's shape.
        chat_context = ScheduledChatContext(
            conversation=conversation,
            schedule_name=schedule.name,
            fired_at=fired_at,
            principal=schedule.principal,
            allows_skip=schedule.allow_skip,
            destructive_tool_policy=RunDestructiveToolPolicy()
            if schedule.allow_destructive
            else HideDestructiveToolPolicy(),
        )
        await run_turn(
            schedule.prompt,
            # Without a principal the turn reaches no one's credentials, so this only labels it.
            principal=schedule.principal or f'schedule:{schedule.name}',
            chat_context=chat_context,
            runner=self._runner,
            surface=surface,
            message_store=self._message_store,
            bridge=UnattendedOAuthBridge() if schedule.principal is not None else None,
            compactor=self._compactor,
            history_ttl_seconds=self._history_ttl_seconds,
        )


class _DeliveryRecorder(ReplySurface):
    """Pass every call through to a push surface, noting whether a message reached the chat."""

    def __init__(self, inner: ReplySurface) -> None:
        self._inner = inner
        self.delivered = False

    @property
    @typing.override
    def text_formatting_instruction(self) -> str:
        return self._inner.text_formatting_instruction

    @property
    @typing.override
    def lang(self) -> Lang | None:
        return self._inner.lang

    @typing.override
    async def send_text(self, text: str) -> None:
        await self._inner.send_text(text)
        self.delivered = True

    @typing.override
    async def send_card(self, card: Card, *, destructive: bool = False) -> None:
        await self._inner.send_card(card, destructive=destructive)
        self.delivered = True

    @typing.override
    async def send_carousel(self, cards: list[Card]) -> None:
        await self._inner.send_carousel(cards)
        self.delivered = True

    @typing.override
    async def acknowledge_silently(self) -> None:
        await self._inner.acknowledge_silently()

    @typing.override
    async def show_processing(self) -> None:
        await self._inner.show_processing()
