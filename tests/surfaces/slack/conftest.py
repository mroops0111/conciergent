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
from conciergent.store.credential import CredentialStore
from conciergent.store.message import MessageStore
from conciergent.surfaces.slack import webhook
from conciergent.surfaces.slack.install import SlackInstallSettings, build_install_router
from conciergent.surfaces.slack.surface import SlackUser
from conciergent.surfaces.slack.webhook import SlackWebhookSettings, build_router
from tests.conftest import StubRunner


SIGNING_SECRET = 'signing-secret'
TEAM = 'T1'
USER = 'U1'
CHANNEL = 'D1'
MESSAGE_TS = '111.222'
BOT_USER = 'UBOT'
BOT_TOKEN = 'xoxb-1'


@dataclasses.dataclass
class SlackHarness:
    client: httpx.AsyncClient
    agent: StubRunner
    posts: list[tuple[str, dict[str, typing.Any]]]
    patches: list[dict[str, typing.Any]]
    message_store: MessageStore
    credential_store: CredentialStore

    async def install(self, *, team: str = TEAM, bot_token: str = BOT_TOKEN) -> None:
        await self.credential_store.set_bot_token(ChatSurface.slack, team, bot_token)

    async def post_event(self, payload: dict[str, typing.Any], *, signature: str | None = None) -> httpx.Response:
        """Deliver an Events API payload, signed with the signing secret unless a signature is given."""
        return await self._post('/slack/events', json.dumps(payload).encode(), signature=signature)

    async def post_interaction(self, payload: dict[str, typing.Any]) -> httpx.Response:
        """Deliver an interactivity payload, form-encoded the way Slack sends it."""
        body = urllib.parse.urlencode({'payload': json.dumps(payload)}).encode()
        return await self._post('/slack/interactions', body)

    async def _post(self, path: str, body: bytes, *, signature: str | None = None) -> httpx.Response:
        headers = _signed_headers(body)
        if signature is not None:
            headers['X-Slack-Signature'] = signature
        return await self.client.post(path, content=body, headers=headers)


# A factory fixture's type, so a test can build a harness under its own settings.
BuildHarness = collections.abc.Callable[..., typing.Awaitable[SlackHarness]]


@pytest.fixture
async def slack_app(
    monkeypatch: pytest.MonkeyPatch, message_store: MessageStore, credential_store: CredentialStore
) -> typing.AsyncIterator[BuildHarness]:
    # A factory so a test can serve the webhook under whatever SlackWebhookSettings it needs.
    async with contextlib.AsyncExitStack() as stack:

        async def build(**settings_overrides: typing.Any) -> SlackHarness:
            agent = StubRunner()
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
                    posts.append((channel, {**payload, 'thread_ts': thread_ts}))

                async def respond_via_response_url(self, response_url: str, payload: dict[str, typing.Any]) -> None:
                    patches.append(payload)

                async def get_user(self, user_id: str) -> SlackUser:
                    return SlackUser(display_name=f'name-{user_id}')

            monkeypatch.setattr(webhook, 'SlackMessenger', RecordingMessenger)
            app = fastapi.FastAPI()
            app.include_router(
                build_router(
                    settings=SlackWebhookSettings(signing_secret=SIGNING_SECRET, **settings_overrides),
                    message_store=message_store,
                    credential_store=credential_store,
                    runner=agent.as_runner(),
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
    slack_app: BuildHarness,
) -> SlackHarness:
    return await slack_app()


def _signed_headers(body: bytes) -> dict[str, str]:
    timestamp = str(int(time.time()))
    digest = hmac.new(SIGNING_SECRET.encode(), f'v0:{timestamp}:'.encode() + body, hashlib.sha256).hexdigest()
    return {'X-Slack-Request-Timestamp': timestamp, 'X-Slack-Signature': f'v0={digest}'}


# Payload builders, each the shape Slack delivers, with a direct message as the default.


def event(*, event_id: str = 'Ev1', text: str = 'hello', **event_overrides: typing.Any) -> dict[str, typing.Any]:
    """An Events API callback, a direct message unless overridden, naming the bot as its installation."""
    message = {
        'type': 'message',
        'channel_type': 'im',
        'user': USER,
        'channel': CHANNEL,
        'ts': MESSAGE_TS,
        'text': text,
        **event_overrides,
    }
    return {
        'type': 'event_callback',
        'event_id': event_id,
        'team_id': TEAM,
        'authorizations': [{'user_id': BOT_USER, 'is_bot': True}],
        'event': message,
    }


def interaction(
    action_id: str,
    *,
    value: str,
    user: str = USER,
    channel: str = CHANNEL,
    message: dict[str, typing.Any] | None = None,
    response_url: str | None = None,
) -> dict[str, typing.Any]:
    """A block_actions payload for a click on the bot's message, by default in the direct-message channel."""
    payload: dict[str, typing.Any] = {
        'type': 'block_actions',
        'team': {'id': TEAM},
        'user': {'id': user},
        'channel': {'id': channel},
        'message': message if message is not None else {'ts': MESSAGE_TS},
        'actions': [{'action_id': action_id, 'value': value}],
    }
    if response_url is not None:
        payload['response_url'] = response_url
    return payload


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
