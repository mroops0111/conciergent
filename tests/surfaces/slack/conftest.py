import collections.abc
import contextlib
import dataclasses
import hashlib
import hmac
import json
import time
import typing
import urllib.parse

import fastapi
import httpx
import pytest

from conciergent import ChatSurface
from conciergent.agent.runner import ChatRunner
from conciergent.store.credential import CredentialStore
from conciergent.store.message import MessageStore
from conciergent.surfaces.slack import webhook
from conciergent.surfaces.slack.install import SlackInstallSettings, build_install_router
from conciergent.surfaces.slack.webhook import SlackWebhookSettings, build_router
from tests.surfaces.conftest import EchoAgent


SIGNING_SECRET = 'signing-secret'
TEAM = 'T1'
USER = 'U1'
CHANNEL = 'D1'
MESSAGE_TS = '111.222'
BOT_TOKEN = 'xoxb-1'


@dataclasses.dataclass
class SlackHarness:
    client: httpx.AsyncClient
    agent: EchoAgent
    posts: list[tuple[str, dict[str, typing.Any]]]
    patches: list[dict[str, typing.Any]]
    message_store: MessageStore
    credential_store: CredentialStore

    async def install(self, *, team: str = TEAM, bot_token: str = BOT_TOKEN) -> None:
        await self.credential_store.set_bot_token(ChatSurface.slack, team, bot_token)


@pytest.fixture
async def slack_app(
    monkeypatch: pytest.MonkeyPatch, message_store: MessageStore, credential_store: CredentialStore
) -> typing.AsyncIterator[collections.abc.Callable[..., typing.Awaitable[SlackHarness]]]:
    # A factory so a test can serve the webhook under whatever SlackWebhookSettings it needs.
    async with contextlib.AsyncExitStack() as stack:

        async def build(**settings_overrides: typing.Any) -> SlackHarness:
            agent = EchoAgent()
            posts: list[tuple[str, dict[str, typing.Any]]] = []
            patches: list[dict[str, typing.Any]] = []

            class RecordingMessenger:
                def __init__(self, bot_token: str, *, timeout_seconds: float = 30.0) -> None:
                    self.bot_token = bot_token

                async def __aenter__(self) -> 'RecordingMessenger':
                    return self

                async def __aexit__(self, *exc_info: object) -> None:
                    return None

                async def post_message(
                    self, channel: str, payload: dict[str, typing.Any], *, thread_ts: str | None = None
                ) -> None:
                    posts.append((channel, payload))

                async def respond_via_response_url(self, response_url: str, payload: dict[str, typing.Any]) -> None:
                    patches.append(payload)

                async def get_lang(self, user_id: str) -> None:
                    return None

            monkeypatch.setattr(webhook, 'SlackMessenger', RecordingMessenger)
            app = fastapi.FastAPI()
            app.include_router(
                build_router(
                    settings=SlackWebhookSettings(signing_secret=SIGNING_SECRET, **settings_overrides),
                    message_store=message_store,
                    credential_store=credential_store,
                    runner=typing.cast(ChatRunner, agent),
                )
            )
            transport = httpx.ASGITransport(app=app)
            client = await stack.enter_async_context(httpx.AsyncClient(transport=transport, base_url='http://test'))
            return SlackHarness(
                client=client,
                agent=agent,
                posts=posts,
                patches=patches,
                message_store=message_store,
                credential_store=credential_store,
            )

        yield build


@pytest.fixture
async def harness(
    slack_app: collections.abc.Callable[..., typing.Awaitable[SlackHarness]],
) -> SlackHarness:
    return await slack_app()


@pytest.fixture
def sign_headers() -> collections.abc.Callable[[bytes], dict[str, str]]:
    def _sign(body: bytes) -> dict[str, str]:
        timestamp = str(int(time.time()))
        digest = hmac.new(SIGNING_SECRET.encode(), f'v0:{timestamp}:'.encode() + body, hashlib.sha256).hexdigest()
        return {'X-Slack-Request-Timestamp': timestamp, 'X-Slack-Signature': f'v0={digest}'}

    return _sign


@pytest.fixture
def event_body() -> collections.abc.Callable[..., bytes]:
    def _body(*, event_id: str = 'Ev1', text: str = 'hello', **event_overrides: typing.Any) -> bytes:
        event = {
            'type': 'message',
            'channel_type': 'im',
            'user': USER,
            'channel': CHANNEL,
            'ts': MESSAGE_TS,
            'text': text,
            **event_overrides,
        }
        return json.dumps({'type': 'event_callback', 'event_id': event_id, 'team_id': TEAM, 'event': event}).encode()

    return _body


@pytest.fixture
def interaction_body() -> collections.abc.Callable[..., bytes]:
    def _body(
        action_id: str,
        *,
        value: str,
        message_ts: str = MESSAGE_TS,
        text: str | None = None,
        response_url: str | None = None,
    ) -> bytes:
        message: dict[str, typing.Any] = {'ts': message_ts}
        if text is not None:
            message['text'] = text
        payload: dict[str, typing.Any] = {
            'type': 'block_actions',
            'team': {'id': TEAM},
            'user': {'id': USER},
            'channel': {'id': CHANNEL},
            'message': message,
            'actions': [{'action_id': action_id, 'value': value}],
        }
        if response_url is not None:
            payload['response_url'] = response_url
        return urllib.parse.urlencode({'payload': json.dumps(payload)}).encode()

    return _body


INSTALL_SETTINGS = SlackInstallSettings(
    client_id='cid', client_secret='csecret', scopes=('chat:write', 'im:history'), base_url='https://example.com'
)


@dataclasses.dataclass
class InstallHarness:
    client: httpx.AsyncClient
    credential_store: CredentialStore

    async def issued_state(self) -> str:
        response = await self.client.get('/oauth/slack/install')
        location = response.headers['location']
        return urllib.parse.parse_qs(urllib.parse.urlparse(location).query)['state'][0]


@pytest.fixture
async def install_harness(
    message_store: MessageStore, credential_store: CredentialStore
) -> typing.AsyncIterator[InstallHarness]:
    app = fastapi.FastAPI()
    app.include_router(
        build_install_router(settings=INSTALL_SETTINGS, message_store=message_store, credential_store=credential_store)
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url='http://test') as client:
        yield InstallHarness(client=client, credential_store=credential_store)
