import base64
import hashlib
import hmac
import json
import typing

import fastapi
import httpx
import pytest

from conciergent import TurnResult
from conciergent.agent.runner import ChatRunner
from conciergent.store.message import MessageStore
from conciergent.surfaces.line import webhook
from conciergent.surfaces.line.webhook import LineWebhookSettings, build_router


_SECRET = 'channel-secret'


class FakeMessenger:
    replies: typing.ClassVar[list[dict[str, typing.Any]]] = []
    pushes: typing.ClassVar[list[dict[str, typing.Any]]] = []

    def __init__(self, channel_access_token: str, *, timeout_seconds: float = 30.0) -> None:
        self.token = channel_access_token

    async def __aenter__(self) -> 'FakeMessenger':
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        return None

    async def reply(self, reply_token: str, message: dict[str, typing.Any]) -> None:
        FakeMessenger.replies.append(message)

    async def push(self, user_id: str, message: dict[str, typing.Any]) -> None:
        FakeMessenger.pushes.append(message)

    async def start_loading(self, user_id: str) -> None:
        return None

    async def get_lang(self, user_id: str) -> None:
        return None


class EchoAgent:
    def __init__(self) -> None:
        self.inputs: list[str] = []
        self.bootstrapped: list[str] = []
        self.bootstrap_result = False

    async def bootstrap(self, principal: str, *, bridge: typing.Any = None) -> bool:
        self.bootstrapped.append(principal)
        return self.bootstrap_result

    async def run(
        self,
        user_input: str,
        *,
        principal: str,
        history: list[typing.Any],
        pending_approval: dict[str, typing.Any] | None,
        bridge: typing.Any = None,
        surface: typing.Any = None,
    ) -> TurnResult:
        self.inputs.append(user_input)
        return TurnResult(output=f'echo {user_input}', history=[])


@pytest.fixture
async def harness(
    monkeypatch: pytest.MonkeyPatch, message_store: MessageStore
) -> typing.AsyncIterator[tuple[httpx.AsyncClient, EchoAgent]]:
    FakeMessenger.replies = []
    FakeMessenger.pushes = []
    monkeypatch.setattr(webhook, 'LineMessenger', FakeMessenger)
    agent = EchoAgent()
    app = fastapi.FastAPI()
    settings = LineWebhookSettings(channel_secret=_SECRET, channel_access_token='token')
    app.include_router(
        build_router(settings=settings, message_store=message_store, runner=typing.cast(ChatRunner, agent))
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url='http://test') as client:
        yield client, agent


def _signed_headers(body: bytes) -> dict[str, str]:
    digest = hmac.new(_SECRET.encode(), body, hashlib.sha256).digest()
    return {'X-Line-Signature': base64.b64encode(digest).decode()}


def _message_body(*, event_id: str = 'ev1', text: str = 'hello') -> bytes:
    event = {
        'type': 'message',
        'webhookEventId': event_id,
        'replyToken': 'rt1',
        'source': {'type': 'user', 'userId': 'U1'},
        'message': {'type': 'text', 'text': text},
    }
    return json.dumps({'destination': 'x', 'events': [event]}).encode()


async def test_bad_signature_is_rejected(harness) -> None:
    client, _ = harness
    body = _message_body()
    response = await client.post('/line/events', content=body, headers={'X-Line-Signature': 'bogus'})
    assert response.status_code == 401


async def test_text_message_runs_a_turn_and_replies_with_the_token(harness) -> None:
    client, agent = harness
    body = _message_body(text='hi there')
    response = await client.post('/line/events', content=body, headers=_signed_headers(body))
    assert response.status_code == 200
    assert agent.inputs == ['hi there']
    assert FakeMessenger.replies and FakeMessenger.replies[0]['text'] == 'echo hi there'


async def test_duplicate_delivery_is_dropped(harness) -> None:
    client, agent = harness
    body = _message_body(event_id='dup')
    await client.post('/line/events', content=body, headers=_signed_headers(body))
    await client.post('/line/events', content=body, headers=_signed_headers(body))
    assert agent.inputs == ['hello']


async def test_non_text_messages_are_ignored(harness) -> None:
    client, agent = harness
    event = {
        'type': 'message',
        'webhookEventId': 'ev-sticker',
        'source': {'type': 'user', 'userId': 'U1'},
        'message': {'type': 'sticker'},
    }
    body = json.dumps({'events': [event]}).encode()
    await client.post('/line/events', content=body, headers=_signed_headers(body))
    assert agent.inputs == []


async def test_event_without_id_still_dispatches(harness) -> None:
    client, agent = harness
    event = {
        'type': 'message',
        'replyToken': 'rt9',
        'source': {'type': 'user', 'userId': 'U1'},
        'message': {'type': 'text', 'text': 'no id'},
    }
    body = json.dumps({'events': [event, dict(event)]}).encode()
    await client.post('/line/events', content=body, headers=_signed_headers(body))
    assert agent.inputs == ['no id', 'no id']


def _follow_body() -> bytes:
    event = {
        'type': 'follow',
        'webhookEventId': 'ev-follow',
        'replyToken': 'rt2',
        'source': {'type': 'user', 'userId': 'U1'},
    }
    return json.dumps({'events': [event]}).encode()


async def test_follow_event_bootstraps_and_sends_the_welcome(harness) -> None:
    client, agent = harness
    body = _follow_body()
    await client.post('/line/events', content=body, headers=_signed_headers(body))
    assert agent.inputs == []
    assert agent.bootstrapped == ['line:U1']
    assert FakeMessenger.replies and 'Welcome back' in FakeMessenger.replies[0]['text']


async def test_follow_greets_ready_after_a_fresh_authorization(harness) -> None:
    client, agent = harness
    agent.bootstrap_result = True
    body = _follow_body()
    await client.post('/line/events', content=body, headers=_signed_headers(body))
    assert FakeMessenger.replies and FakeMessenger.replies[0]['text'].startswith("You're all set")
