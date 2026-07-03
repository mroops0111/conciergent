import asyncio
import hashlib
import hmac
import json
import time
import typing
import urllib.parse

import fastapi
import fastapi.testclient
import pytest

from conciergent import AgentResult, ChatAgent, ChatSurface, MemoryStore
from conciergent.surfaces.slack import webhook
from conciergent.surfaces.slack.webhook import SlackWebhookSettings, build_router


_SECRET = 'signing-secret'


class FakeMessenger:
    posts: typing.ClassVar[list[tuple[str, dict[str, typing.Any]]]] = []
    patches: typing.ClassVar[list[dict[str, typing.Any]]] = []

    def __init__(self, bot_token: str) -> None:
        self.bot_token = bot_token

    async def __aenter__(self) -> 'FakeMessenger':
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        return None

    async def post_message(self, channel: str, payload: dict[str, typing.Any], *, thread_ts: str | None = None) -> None:
        FakeMessenger.posts.append((channel, payload))

    async def respond_via_response_url(self, response_url: str, payload: dict[str, typing.Any]) -> None:
        FakeMessenger.patches.append(payload)


class EchoAgent(ChatAgent):
    def __init__(self) -> None:
        self.inputs: list[str] = []

    async def run(
        self,
        user_input: str,
        *,
        principal: str,
        history: list[typing.Any],
        pending: dict[str, typing.Any] | None,
        bridge: typing.Any = None,
        surface: typing.Any = None,
    ) -> AgentResult:
        self.inputs.append(user_input)
        return AgentResult(output=f'echo {user_input}', history=[{'seen': user_input}])


@pytest.fixture
def harness(monkeypatch: pytest.MonkeyPatch) -> tuple[fastapi.testclient.TestClient, EchoAgent, MemoryStore]:
    FakeMessenger.posts = []
    FakeMessenger.patches = []
    monkeypatch.setattr(webhook, 'SlackMessenger', FakeMessenger)
    store = MemoryStore()
    agent = EchoAgent()
    app = fastapi.FastAPI()
    app.include_router(build_router(settings=SlackWebhookSettings(signing_secret=_SECRET), store=store, agent=agent))
    return fastapi.testclient.TestClient(app), agent, store


def _install(store: MemoryStore) -> None:
    asyncio.run(store.set_bot_token(ChatSurface.slack, 'T1', 'xoxb-1'))


def _signed_headers(body: bytes) -> dict[str, str]:
    timestamp = str(int(time.time()))
    digest = hmac.new(_SECRET.encode(), f'v0:{timestamp}:'.encode() + body, hashlib.sha256).hexdigest()
    return {'X-Slack-Request-Timestamp': timestamp, 'X-Slack-Signature': f'v0={digest}'}


def _event_body(*, event_id: str = 'Ev1', text: str = 'hello', **event_overrides: typing.Any) -> bytes:
    event = {
        'type': 'message',
        'channel_type': 'im',
        'user': 'U1',
        'channel': 'D1',
        'ts': '111.222',
        'text': text,
        **event_overrides,
    }
    return json.dumps({'type': 'event_callback', 'event_id': event_id, 'team_id': 'T1', 'event': event}).encode()


def test_url_verification_answers_challenge(harness) -> None:
    client, _, _ = harness
    body = json.dumps({'type': 'url_verification', 'challenge': 'c123'}).encode()
    response = client.post('/slack/events', content=body, headers=_signed_headers(body))
    assert response.json() == {'challenge': 'c123'}


def test_bad_signature_is_rejected(harness) -> None:
    client, _, _ = harness
    body = _event_body()
    headers = _signed_headers(body)
    headers['X-Slack-Signature'] = 'v0=deadbeef'
    assert client.post('/slack/events', content=body, headers=headers).status_code == 401


def test_message_event_runs_a_turn_and_replies(harness) -> None:
    client, agent, store = harness
    _install(store)
    body = _event_body(text='hi there')
    response = client.post('/slack/events', content=body, headers=_signed_headers(body))
    assert response.status_code == 200
    assert agent.inputs == ['hi there']
    assert FakeMessenger.posts and FakeMessenger.posts[0][0] == 'D1'


def test_duplicate_event_delivery_is_dropped(harness) -> None:
    client, agent, store = harness
    _install(store)
    body = _event_body(event_id='Ev-dup')
    client.post('/slack/events', content=body, headers=_signed_headers(body))
    client.post('/slack/events', content=body, headers=_signed_headers(body))
    assert agent.inputs == ['hello']


def test_bot_echo_is_ignored(harness) -> None:
    client, agent, store = harness
    _install(store)
    body = _event_body(bot_id='B99')
    client.post('/slack/events', content=body, headers=_signed_headers(body))
    assert agent.inputs == []


def test_uninstalled_team_is_ignored(harness) -> None:
    client, agent, _ = harness
    body = _event_body()
    client.post('/slack/events', content=body, headers=_signed_headers(body))
    assert agent.inputs == []


def test_fallback_bot_token_serves_single_workspace(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeMessenger.posts = []
    monkeypatch.setattr(webhook, 'SlackMessenger', FakeMessenger)
    agent = EchoAgent()
    app = fastapi.FastAPI()
    settings = SlackWebhookSettings(signing_secret=_SECRET, fallback_bot_token='xoxb-static')
    app.include_router(build_router(settings=settings, store=MemoryStore(), agent=agent))
    client = fastapi.testclient.TestClient(app)
    body = _event_body()
    client.post('/slack/events', content=body, headers=_signed_headers(body))
    assert agent.inputs == ['hello']


def test_suggestion_interaction_runs_the_prompt(harness) -> None:
    client, agent, store = harness
    _install(store)
    payload = {
        'type': 'block_actions',
        'team': {'id': 'T1'},
        'user': {'id': 'U1'},
        'channel': {'id': 'D1'},
        'message': {'ts': '111.222', 'text': 'Tasks'},
        'response_url': 'https://example.com/response',
        'actions': [{'action_id': 'suggestion:open:0:0', 'value': 'List more tasks'}],
    }
    body = urllib.parse.urlencode({'payload': json.dumps(payload)}).encode()
    client.post('/slack/interactions', content=body, headers=_signed_headers(body))
    assert agent.inputs == ['List more tasks']
    assert FakeMessenger.patches, 'the interacted message is patched to show processing'


def test_open_interaction_allows_a_fresh_click(harness) -> None:
    client, agent, store = harness
    _install(store)

    def body_for(action_ts: str) -> bytes:
        payload = {
            'type': 'block_actions',
            'team': {'id': 'T1'},
            'user': {'id': 'U1'},
            'channel': {'id': 'D1'},
            'message': {'ts': '111.222'},
            'actions': [{'action_id': 'suggestion:open:0:0', 'value': 'refresh', 'action_ts': action_ts}],
        }
        return urllib.parse.urlencode({'payload': json.dumps(payload)}).encode()

    first = body_for('1000.1')
    redelivery = body_for('1000.1')
    fresh_click = body_for('2000.2')
    client.post('/slack/interactions', content=first, headers=_signed_headers(first))
    client.post('/slack/interactions', content=redelivery, headers=_signed_headers(redelivery))
    client.post('/slack/interactions', content=fresh_click, headers=_signed_headers(fresh_click))
    assert agent.inputs == ['refresh', 'refresh']


def test_exclusive_interaction_consumes_the_whole_message(harness) -> None:
    client, agent, store = harness
    _install(store)

    def body_for(button: int) -> bytes:
        payload = {
            'type': 'block_actions',
            'team': {'id': 'T1'},
            'user': {'id': 'U1'},
            'channel': {'id': 'D1'},
            'message': {'ts': '111.222'},
            'actions': [{'action_id': f'suggestion:exclusive:0:{button}', 'value': f'pick {button}'}],
        }
        return urllib.parse.urlencode({'payload': json.dumps(payload)}).encode()

    first = body_for(0)
    second = body_for(1)
    client.post('/slack/interactions', content=first, headers=_signed_headers(first))
    client.post('/slack/interactions', content=second, headers=_signed_headers(second))
    assert agent.inputs == ['pick 0']


def test_threads_are_separate_conversations(harness) -> None:
    client, _, store = harness
    _install(store)
    first = _event_body(event_id='EvT1', text='in thread one', thread_ts='100.1')
    second = _event_body(event_id='EvT2', text='in thread two', thread_ts='200.2')
    client.post('/slack/events', content=first, headers=_signed_headers(first))
    client.post('/slack/events', content=second, headers=_signed_headers(second))
    assert asyncio.run(store.load_history('slack:T1:U1:100.1')) == [{'seen': 'in thread one'}]
    assert asyncio.run(store.load_history('slack:T1:U1:200.2')) == [{'seen': 'in thread two'}]
