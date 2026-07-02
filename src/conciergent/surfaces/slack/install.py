import secrets
import typing
import urllib.parse

import fastapi
import fastapi.responses
import httpx

from ...identity import ChatSurface
from ...stores.base import Store


_AUTHORIZE_URL = 'https://slack.com/oauth/v2/authorize'
_ACCESS_URL = 'https://slack.com/api/oauth.v2.access'
_STATE_TTL_SECONDS = 600
_STATE_KEY_PREFIX = 'slack-install'


class SlackInstallSettings(typing.NamedTuple):
    """The Slack app credentials and public base URL the install flow needs."""

    client_id: str
    client_secret: str
    scopes: tuple[str, ...]
    base_url: str


def build_install_router(*, settings: SlackInstallSettings, store: Store) -> fastapi.APIRouter:
    """Build the workspace install routes, persisting the bot token per team through the store."""
    router = fastapi.APIRouter()
    redirect_uri = f'{settings.base_url.rstrip("/")}/oauth/slack/callback'

    @router.get('/oauth/slack/install')
    async def install() -> fastapi.responses.RedirectResponse:
        state = secrets.token_urlsafe(32)
        # The approval store doubles as the CSRF stash,
        # park and take give the same one-shot set-with-ttl and consume semantics the state check needs.
        await store.park_approval(f'{_STATE_KEY_PREFIX}:{state}', {'issued': True}, ttl_seconds=_STATE_TTL_SECONDS)
        query = urllib.parse.urlencode(
            {
                'client_id': settings.client_id,
                'scope': ','.join(settings.scopes),
                'redirect_uri': redirect_uri,
                'state': state,
            }
        )
        return fastapi.responses.RedirectResponse(f'{_AUTHORIZE_URL}?{query}')

    @router.get('/oauth/slack/callback')
    async def callback(code: str = '', state: str = '') -> fastapi.responses.HTMLResponse:
        issued = await store.take_approval(f'{_STATE_KEY_PREFIX}:{state}')
        if not code or issued is None:
            return fastapi.responses.HTMLResponse('<h1>Installation failed</h1>', status_code=400)
        team_id, bot_token = await _exchange_code(
            code, client_id=settings.client_id, client_secret=settings.client_secret, redirect_uri=redirect_uri
        )
        await store.set_bot_token(ChatSurface.slack, team_id, bot_token)
        return fastapi.responses.HTMLResponse('<h1>Installed. You can close this window.</h1>')

    return router


async def _exchange_code(code: str, *, client_id: str, client_secret: str, redirect_uri: str) -> tuple[str, str]:
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            _ACCESS_URL,
            data={
                'client_id': client_id,
                'client_secret': client_secret,
                'code': code,
                'redirect_uri': redirect_uri,
            },
        )
    response.raise_for_status()
    data = response.json()
    if not data.get('ok'):
        raise RuntimeError(f'Slack oauth.v2.access failed: {data.get("error")}')
    return data['team']['id'], data['access_token']
