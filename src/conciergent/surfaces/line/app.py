import fastapi

from ..base import Surface, SurfaceContext
from .webhook import LineWebhookSettings, build_router


class Line(Surface):
    """The LINE platform, one webhook route serving messages and follow events."""

    def __init__(self, *, channel_secret: str, channel_access_token: str) -> None:
        self._channel_secret = channel_secret
        self._channel_access_token = channel_access_token

    def build_routers(self, context: SurfaceContext) -> list[fastapi.APIRouter]:
        return [
            build_router(
                settings=LineWebhookSettings(
                    channel_secret=self._channel_secret, channel_access_token=self._channel_access_token
                ),
                store=context.store,
                agent=context.agent,
                compactor=context.compactor,
            )
        ]
