import base64
import collections.abc
import contextlib
import dataclasses
import hashlib
import hmac
import json
import typing

import fastapi
import httpx
import pytest

from conciergent.agent.runner import ChatRunner
from conciergent.store.message import MessageStore
from conciergent.surfaces.line import webhook
from conciergent.surfaces.line.webhook import LineWebhookSettings, build_router
from tests.surfaces.conftest import EchoAgent


CHANNEL_SECRET = 'channel-secret'
ACCESS_TOKEN = 'token'
USER = 'U1'
REPLY_TOKEN = 'rt1'


@dataclasses.dataclass
class LineHarness:
    client: httpx.AsyncClient
    agent: EchoAgent
    replies: list[dict[str, typing.Any]]
    pushes: list[dict[str, typing.Any]]
    message_store: MessageStore


@pytest.fixture
async def line_app(
    monkeypatch: pytest.MonkeyPatch, message_store: MessageStore
) -> typing.AsyncIterator[collections.abc.Callable[..., typing.Awaitable[LineHarness]]]:
    # A factory so a test can serve the webhook under whatever LineWebhookSettings it needs.
    async with contextlib.AsyncExitStack() as stack:

        async def build(**settings_overrides: typing.Any) -> LineHarness:
            agent = EchoAgent()
            replies: list[dict[str, typing.Any]] = []
            pushes: list[dict[str, typing.Any]] = []

            class RecordingMessenger:
                def __init__(self, channel_access_token: str, *, timeout_seconds: float = 30.0) -> None:
                    self.token = channel_access_token

                async def __aenter__(self) -> 'RecordingMessenger':
                    return self

                async def __aexit__(self, *exc_info: object) -> None:
                    return None

                async def reply(self, reply_token: str, message: dict[str, typing.Any]) -> None:
                    replies.append(message)

                async def push(self, user_id: str, message: dict[str, typing.Any]) -> None:
                    pushes.append(message)

                async def start_loading(self, user_id: str) -> None:
                    return None

                async def get_lang(self, user_id: str) -> None:
                    return None

            monkeypatch.setattr(webhook, 'LineMessenger', RecordingMessenger)
            app = fastapi.FastAPI()
            settings = LineWebhookSettings(
                channel_secret=CHANNEL_SECRET, channel_access_token=ACCESS_TOKEN, **settings_overrides
            )
            app.include_router(
                build_router(settings=settings, message_store=message_store, runner=typing.cast(ChatRunner, agent))
            )
            transport = httpx.ASGITransport(app=app)
            client = await stack.enter_async_context(httpx.AsyncClient(transport=transport, base_url='http://test'))
            return LineHarness(client=client, agent=agent, replies=replies, pushes=pushes, message_store=message_store)

        yield build


@pytest.fixture
async def harness(line_app: collections.abc.Callable[..., typing.Awaitable[LineHarness]]) -> LineHarness:
    return await line_app()


@pytest.fixture
def sign_headers() -> collections.abc.Callable[[bytes], dict[str, str]]:
    def _sign(body: bytes) -> dict[str, str]:
        digest = hmac.new(CHANNEL_SECRET.encode(), body, hashlib.sha256).digest()
        return {'X-Line-Signature': base64.b64encode(digest).decode()}

    return _sign


@pytest.fixture
def message_event() -> collections.abc.Callable[..., dict[str, typing.Any]]:
    def _event(
        *, event_id: str | None = 'ev1', text: str = 'hello', message: dict[str, typing.Any] | None = None
    ) -> dict[str, typing.Any]:
        event: dict[str, typing.Any] = {
            'type': 'message',
            'replyToken': REPLY_TOKEN,
            'source': {'type': 'user', 'userId': USER},
            'message': message if message is not None else {'type': 'text', 'text': text},
        }
        if event_id is not None:
            event['webhookEventId'] = event_id
        return event

    return _event


@pytest.fixture
def follow_event() -> collections.abc.Callable[..., dict[str, typing.Any]]:
    def _event(*, event_id: str = 'ev-follow') -> dict[str, typing.Any]:
        return {
            'type': 'follow',
            'webhookEventId': event_id,
            'replyToken': 'rt2',
            'source': {'type': 'user', 'userId': USER},
        }

    return _event


@pytest.fixture
def line_body() -> collections.abc.Callable[..., bytes]:
    def _body(*events: dict[str, typing.Any]) -> bytes:
        return json.dumps({'events': list(events)}).encode()

    return _body
