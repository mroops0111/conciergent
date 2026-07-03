import logging
import secrets
import typing
import urllib.parse

import fastapi
import fastapi.responses
import httpx

from conciergent import i18n
from conciergent.i18n.lang import Lang, parse_accept_language
from conciergent.identity import ChatSurface, make_principal
from conciergent.store.credential import CredentialStore
from conciergent.store.message import MessageStore


_AUTHORIZE_URL = 'https://slack.com/oauth/v2/authorize'
_ACCESS_URL = 'https://slack.com/api/oauth.v2.access'
_STATE_TTL_SECONDS = 600
_STATE_KEY_PREFIX = 'slack-install'

logger = logging.getLogger(__name__)


class SlackInstallSettings(typing.NamedTuple):
    """The Slack app credentials and public base URL the install flow needs."""

    client_id: str
    client_secret: str
    scopes: tuple[str, ...]
    base_url: str


def build_install_router(
    *, settings: SlackInstallSettings, message_store: MessageStore, credential_store: CredentialStore
) -> fastapi.APIRouter:
    """Build the workspace install routes, stashing the CSRF state in Redis and persisting the bot token in SQL."""
    router = fastapi.APIRouter()
    redirect_uri = f'{settings.base_url.rstrip("/")}/oauth/slack/callback'

    @router.get('/oauth/slack/install')
    async def install() -> fastapi.responses.RedirectResponse:
        state = secrets.token_urlsafe(32)
        # The approval parking lot doubles as the CSRF stash,
        # park and take give the same one-shot set-with-ttl and consume semantics the state check needs.
        await message_store.park_approval(
            f'{_STATE_KEY_PREFIX}:{state}', {'issued': True}, ttl_seconds=_STATE_TTL_SECONDS
        )
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
    async def callback(
        code: str = '', state: str = '', error: str = '', accept_language: str = fastapi.Header(default='')
    ) -> fastapi.responses.HTMLResponse:
        lang = parse_accept_language(accept_language)
        # Slack sends ``error`` when the user declines the install; treat it like any other failed return.
        if error or not code or not state:
            return _page(lang, 'install.failed', status_code=400)
        if await message_store.take_approval(f'{_STATE_KEY_PREFIX}:{state}') is None:
            return _page(lang, 'install.failed', status_code=400)
        try:
            team_id, bot_token, installed_principal = await _exchange_code(
                code, client_id=settings.client_id, client_secret=settings.client_secret, redirect_uri=redirect_uri
            )
        except (RuntimeError, httpx.HTTPError):
            # A stale or already-consumed code is a routine OAuth ending, not a server error.
            logger.warning('Slack install code exchange failed', exc_info=True)
            return _page(lang, 'install.failed', status_code=400)
        if not team_id or not bot_token:
            return _page(lang, 'install.failed', status_code=400)
        await credential_store.set_bot_token(
            ChatSurface.slack, team_id, bot_token, installed_principal=installed_principal
        )
        return _page(lang, 'install.completed')

    return router


def _page(lang: Lang | None, key: str, *, status_code: int = 200) -> fastapi.responses.HTMLResponse:
    title = i18n.t(f'{key}_title', lang)
    body = i18n.t(f'{key}_body', lang)
    return fastapi.responses.HTMLResponse(f'<h1>{title}</h1><p>{body}</p>', status_code=status_code)


async def _exchange_code(
    code: str, *, client_id: str, client_secret: str, redirect_uri: str
) -> tuple[str, str, str | None]:
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
    team_id = (data.get('team') or {}).get('id') or ''
    bot_token = data.get('access_token') or ''
    authed_user_id = (data.get('authed_user') or {}).get('id') or ''
    installed_principal = make_principal(ChatSurface.slack, team_id, authed_user_id) if team_id and authed_user_id else None
    return team_id, bot_token, installed_principal
