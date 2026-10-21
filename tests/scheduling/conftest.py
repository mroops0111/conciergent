import collections.abc
import contextlib
import datetime
import typing
import zoneinfo

import fastapi

from conciergent.config import ScheduleSettings
from conciergent.identity import ChatSurface
from conciergent.scheduling import ScheduleExecutor
from conciergent.store.message import MessageStore
from conciergent.surfaces.base import PushSurface, PushTarget, SurfaceContext
from tests.conftest import RecordingSurface, StubRunner


TAIPEI = zoneinfo.ZoneInfo('Asia/Taipei')
FIRED_AT = datetime.datetime(2026, 10, 21, 21, tzinfo=TAIPEI)


class RecordingPushSurface(PushSurface):
    """A push surface whose pushes land on one recording reply surface, scoped like a LINE group."""

    name = ChatSurface.line

    def __init__(self) -> None:
        super().__init__()
        self.pushed = RecordingSurface()
        self.targets: list[str] = []

    @classmethod
    @typing.override
    def push_conversation(cls, to: str) -> str:
        return f'line:group:{to}'

    @typing.override
    def build_routers(self, context: SurfaceContext) -> list[fastapi.APIRouter]:
        return []

    @typing.override
    @contextlib.asynccontextmanager
    async def push_target(self, to: str) -> collections.abc.AsyncIterator[PushTarget]:
        self.targets.append(to)
        yield PushTarget(conversation=self.push_conversation(to), surface=self.pushed)

    def texts(self) -> list[str]:
        return [payload for kind, payload in self.pushed.calls if kind == 'text']


def schedule(**overrides: typing.Any) -> ScheduleSettings:
    return ScheduleSettings.model_validate(
        {
            'name': 'daily-digest',
            'cron': '0 21 * * *',
            'timezone': 'Asia/Taipei',
            'prompt': 'Summarize today.',
            'target': {'surface': 'line', 'to': 'C1'},
            **overrides,
        }
    )


def executor(
    message_store: MessageStore, *, runner: StubRunner | None = None, surface: PushSurface | None = None
) -> ScheduleExecutor:
    return ScheduleExecutor(
        surfaces={ChatSurface.line: surface or RecordingPushSurface()},
        runner=(runner or StubRunner()).as_runner(),
        message_store=message_store,
    )
