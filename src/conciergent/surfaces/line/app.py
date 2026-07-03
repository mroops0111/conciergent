import typing

import fastapi

from conciergent.defaults import DEFAULTS
from conciergent.surfaces.base import Surface, SurfaceContext
from conciergent.surfaces.line.webhook import LineWebhookSettings, build_router


class Line(Surface):
    """The LINE platform, one webhook route serving messages and follow events."""

    def __init__(
        self,
        *,
        channel_secret: str,
        channel_access_token: str,
        brand_color: str = DEFAULTS.surface.brand_color,
        destructive_color: str = DEFAULTS.surface.destructive_color,
        api_timeout_seconds: float = DEFAULTS.surface.api_timeout_seconds,
    ) -> None:
        self._channel_secret = channel_secret
        self._channel_access_token = channel_access_token
        self._brand_color = brand_color
        self._destructive_color = destructive_color
        self._api_timeout_seconds = api_timeout_seconds

    @typing.override
    def build_routers(self, context: SurfaceContext) -> list[fastapi.APIRouter]:
        return [
            build_router(
                settings=LineWebhookSettings(
                    channel_secret=self._channel_secret,
                    channel_access_token=self._channel_access_token,
                    approval_ttl_seconds=context.approval_ttl_seconds,
                    history_ttl_seconds=context.history_ttl_seconds,
                    oauth_wait_timeout_seconds=context.oauth_wait_timeout_seconds,
                    api_timeout_seconds=self._api_timeout_seconds,
                    brand_color=self._brand_color,
                    destructive_color=self._destructive_color,
                ),
                message_store=context.message_store,
                runner=context.runner,
                compactor=context.compactor,
            )
        ]
