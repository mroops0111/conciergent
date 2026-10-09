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
from tests.conftest import StubRunner


CHANNEL_SECRET = 'channel-secret'
ACCESS_TOKEN = 'token'
USER = 'U1'
REPLY_TOKEN = 'rt1'


@dataclasses.dataclass
class LineHarness:
    client: httpx.AsyncClient
    agent: StubRunner
    replies: list[dict[str, typing.Any]]
    pushes: list[dict[str, typing.Any]]
    message_store: MessageStore
    loadings: list[str]
    display_names: dict[str, str]
    lang_lookups: list[str]

    async def post(self, *events: dict[str, typing.Any], signature: str | None = None) -> httpx.Response:
        """Deliver events to the webhook, signed with the channel secret unless a signature is given."""
        body = json.dumps({'events': list(events)}).encode()
        return await self.client.post(
            '/line/events', content=body, headers={'X-Line-Signature': signature or _signature(body)}
        )


# A factory fixture's type, so a test can build a harness under its own settings.
BuildHarness = collections.abc.Callable[..., typing.Awaitable[LineHarness]]


@pytest.fixture
async def line_app(monkeypatch: pytest.MonkeyPatch, message_store: MessageStore) -> typing.AsyncIterator[BuildHarness]:
    # A factory so a test can serve the webhook under whatever LineWebhookSettings it needs.
    async with contextlib.AsyncExitStack() as stack:

        async def build(*, runner: ChatRunner | None = None, **settings_overrides: typing.Any) -> LineHarness:
            # A test can pass a real ChatRunner to drive the whole turn, the stub serves the rest.
            agent = StubRunner()
            replies: list[dict[str, typing.Any]] = []
            pushes: list[dict[str, typing.Any]] = []
            loadings: list[str] = []
            display_names: dict[str, str] = {}
            lang_lookups: list[str] = []

            class RecordingMessenger:
                def __init__(self, channel_access_token: str, *, timeout_seconds: float = 30.0) -> None:
                    self.token = channel_access_token

                async def __aenter__(self) -> 'RecordingMessenger':
                    return self

                async def __aexit__(self, *exc_info: object) -> None:
                    return None

                async def reply(self, reply_token: str, message: dict[str, typing.Any]) -> None:
                    replies.append(message)

                async def push(self, to: str, message: dict[str, typing.Any]) -> None:
                    pushes.append({**message, 'to': to})

                async def start_loading(self, user_id: str) -> None:
                    loadings.append(user_id)

                async def get_display_name(self, user_id: str, *, chat: typing.Any = None) -> str | None:
                    return display_names.get(user_id)

                async def get_lang(self, user_id: str) -> None:
                    lang_lookups.append(user_id)
                    return None

            monkeypatch.setattr(webhook, 'LineMessenger', RecordingMessenger)
            app = fastapi.FastAPI()
            settings = LineWebhookSettings(
                channel_secret=CHANNEL_SECRET, channel_access_token=ACCESS_TOKEN, **settings_overrides
            )
            app.include_router(
                build_router(settings=settings, message_store=message_store, runner=runner or agent.as_runner())
            )
            transport = httpx.ASGITransport(app=app)
            client = await stack.enter_async_context(httpx.AsyncClient(transport=transport, base_url='http://test'))
            return LineHarness(
                client=client,
                agent=agent,
                replies=replies,
                pushes=pushes,
                message_store=message_store,
                loadings=loadings,
                display_names=display_names,
                lang_lookups=lang_lookups,
            )

        yield build


@pytest.fixture
async def harness(line_app: BuildHarness) -> LineHarness:
    return await line_app()


def _signature(body: bytes) -> str:
    digest = hmac.new(CHANNEL_SECRET.encode(), body, hashlib.sha256).digest()
    return base64.b64encode(digest).decode()


# Webhook event builders, each the shape LINE delivers, with a direct chat as the default source.


def user_source(user: str = USER) -> dict[str, typing.Any]:
    return {'type': 'user', 'userId': user}


def group_source(group_id: str, *, user: str | None = USER) -> dict[str, typing.Any]:
    source: dict[str, typing.Any] = {'type': 'group', 'groupId': group_id}
    if user is not None:
        source['userId'] = user
    return source


def room_source(room_id: str, *, user: str = USER) -> dict[str, typing.Any]:
    return {'type': 'room', 'roomId': room_id, 'userId': user}


def self_mention(index: int, length: int) -> dict[str, typing.Any]:
    """A mention of the bot itself, at a UTF-16 offset into the message text."""
    return {'index': index, 'length': length, 'type': 'user', 'userId': 'Ubot', 'isSelf': True}


def message_event(
    text: str = 'hello',
    *,
    event_id: str | None = 'ev1',
    source: dict[str, typing.Any] | None = None,
    message: dict[str, typing.Any] | None = None,
    **message_fields: typing.Any,
) -> dict[str, typing.Any]:
    """A message event, a text message unless ``message`` replaces it, with extra fields like ``mention``."""
    event: dict[str, typing.Any] = {
        'type': 'message',
        'replyToken': REPLY_TOKEN,
        'source': source or user_source(),
        'message': message if message is not None else {'type': 'text', 'text': text, **message_fields},
    }
    if event_id is not None:
        event['webhookEventId'] = event_id
    return event


def postback_event(
    data: str, *, event_id: str = 'ev-postback', source: dict[str, typing.Any] | None = None
) -> dict[str, typing.Any]:
    return {
        'type': 'postback',
        'webhookEventId': event_id,
        'replyToken': REPLY_TOKEN,
        'source': source or user_source(),
        'postback': {'data': data},
    }


def follow_event(*, event_id: str = 'ev-follow') -> dict[str, typing.Any]:
    return {'type': 'follow', 'webhookEventId': event_id, 'replyToken': 'rt2', 'source': user_source()}


def join_event(group_id: str, *, event_id: str = 'ev-join') -> dict[str, typing.Any]:
    return {
        'type': 'join',
        'webhookEventId': event_id,
        'replyToken': 'rt3',
        'source': group_source(group_id, user=None),
    }
