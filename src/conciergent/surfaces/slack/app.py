import collections.abc
import typing

import fastapi

from conciergent.defaults import DEFAULTS
from conciergent.surfaces.base import Surface, SurfaceContext
from conciergent.surfaces.slack.install import SlackInstallSettings, build_install_router
from conciergent.surfaces.slack.webhook import SlackWebhookSettings, build_router


class Slack(Surface):
    """The Slack platform, webhook routes plus the optional multi-workspace install flow."""

    # The scopes the bot needs to function, so they are fixed by the surface rather than configured.
    DEFAULT_SCOPES = ('chat:write', 'im:history', 'im:read', 'im:write', 'users:read')

    def __init__(
        self,
        *,
        signing_secret: str,
        client_id: str = '',
        client_secret: str = '',
        scopes: collections.abc.Sequence[str] = DEFAULT_SCOPES,
        bot_token: str = '',
        brand_color: str = DEFAULTS.surface.brand_color,
        destructive_color: str = DEFAULTS.surface.destructive_color,
        api_timeout_seconds: float = DEFAULTS.surface.api_timeout_seconds,
    ) -> None:
        self._signing_secret = signing_secret
        self._client_id = client_id
        self._client_secret = client_secret
        self._scopes = tuple(scopes)
        self._bot_token = bot_token
        self._brand_color = brand_color
        self._destructive_color = destructive_color
        self._api_timeout_seconds = api_timeout_seconds

    @typing.override
    def build_routers(self, context: SurfaceContext) -> list[fastapi.APIRouter]:
        routers = [
            build_router(
                settings=SlackWebhookSettings(
                    signing_secret=self._signing_secret,
                    fallback_bot_token=self._bot_token,
                    approval_ttl_seconds=context.approval_ttl_seconds,
                    history_ttl_seconds=context.history_ttl_seconds,
                    oauth_wait_timeout_seconds=context.oauth_wait_timeout_seconds,
                    api_timeout_seconds=self._api_timeout_seconds,
                    brand_color=self._brand_color,
                    destructive_color=self._destructive_color,
                ),
                message_store=context.message_store,
                credential_store=context.credential_store,
                runner=context.runner,
                compactor=context.compactor,
            )
        ]
        if self._client_id:
            routers.append(
                build_install_router(
                    settings=SlackInstallSettings(
                        client_id=self._client_id,
                        client_secret=self._client_secret,
                        scopes=self._scopes,
                        base_url=context.base_url,
                    ),
                    message_store=context.message_store,
                    credential_store=context.credential_store,
                )
            )
        return routers
