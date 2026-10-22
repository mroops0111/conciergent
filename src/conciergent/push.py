import collections.abc
import datetime
import enum
import logging
import typing

from conciergent.agent.compactor import HistorySummarizer
from conciergent.agent.runner import ChatRunner
from conciergent.chat_context import UnattendedChatContext
from conciergent.config import PushSettings
from conciergent.defaults import DEFAULTS
from conciergent.i18n.lang import Lang
from conciergent.identity import ChatSurface
from conciergent.reply import Card, ReplySurface
from conciergent.store.message import MessageStore
from conciergent.surfaces.base import PushSurface
from conciergent.turn import run_turn


logger = logging.getLogger(__name__)


class UnknownPushError(LookupError):
    """No configured push has the name a run asked for."""


class PushOutcome(enum.StrEnum):
    """How a push ended, judged by whether anything reached the chat, since that decides if a retry is safe."""

    delivered = 'delivered'
    skipped = 'skipped'
    # Nothing but the failure message reached the chat, so running the push again cannot repeat a reply.
    failed = 'failed'


class PushExecutor:
    """Run a push once as an unattended turn and send the reply into the push's target chat.

    When and how often a push runs is up to whatever calls this,
    such as cron or a Kubernetes CronJob running ``conciergent push``.
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

    async def run(self, push: PushSettings, started_at: datetime.datetime) -> PushOutcome:
        """Run ``push`` as started at ``started_at``, sending the failure message when it fails before replying."""
        surface = self._surfaces[push.target.surface]
        delivery: _DeliveryRecorder | None = None
        try:
            async with surface.push_target(push.target.to) as target:
                delivery = _DeliveryRecorder(target.surface)
                try:
                    await self._run_turn(push, started_at, target.conversation, delivery)
                except Exception:
                    logger.exception('Push %s failed at %s', push.name, started_at.isoformat())
                    if delivery.delivered:
                        # The reply is out, so only a later step such as saving history failed.
                        return PushOutcome.delivered
                    if push.failure_message:
                        await target.surface.send_text(push.failure_message)
                    return PushOutcome.failed
                outcome = PushOutcome.delivered if delivery.delivered else PushOutcome.skipped
        except Exception:
            # The push could not open, the failure message could not go out, or closing the push failed.
            logger.exception('Push %s could not reach %s', push.name, push.target.to)
            # A reply already out still counts, or a retry would push it twice.
            return PushOutcome.delivered if delivery is not None and delivery.delivered else PushOutcome.failed
        return outcome

    async def _run_turn(
        self, push: PushSettings, started_at: datetime.datetime, conversation: str, surface: ReplySurface
    ) -> None:
        # This is where the config meets the domain, so the context never sees the config's shape.
        chat_context = UnattendedChatContext(
            conversation=conversation,
            push_name=push.name,
            started_at=started_at,
            allows_skip=push.allow_skip,
            runs_destructive_tools=push.allow_destructive,
        )
        await run_turn(
            push.prompt,
            # The turn reaches no one's credentials, so this only labels it.
            principal=f'push:{push.name}',
            chat_context=chat_context,
            runner=self._runner,
            surface=surface,
            message_store=self._message_store,
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
