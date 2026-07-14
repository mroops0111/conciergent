import typing

import fastapi

from conciergent.defaults import DEFAULTS
from conciergent.surfaces.base import Surface, SurfaceContext
from conciergent.surfaces.discord.gateway import DiscordGateway, DiscordGatewaySettings


class Discord(Surface):
    """The Discord platform, a gateway connection that turns direct messages and button clicks into turns."""

    def __init__(
        self,
        *,
        bot_token: str,
        brand_color: str = DEFAULTS.surface.discord.brand_color,
        destructive_color: str = DEFAULTS.surface.discord.destructive_color,
        api_timeout_seconds: float = DEFAULTS.surface.discord.api_timeout_seconds,
    ) -> None:
        self._bot_token = bot_token
        self._brand_color = brand_color
        self._destructive_color = destructive_color
        self._api_timeout_seconds = api_timeout_seconds

    @typing.override
    def build_routers(self, context: SurfaceContext) -> list[fastapi.APIRouter]:
        # Discord streams its events over the gateway, so it contributes no webhook routes.
        return []

    @typing.override
    async def run_connection(self, context: SurfaceContext) -> None:
        gateway = DiscordGateway(
            settings=DiscordGatewaySettings(
                bot_token=self._bot_token,
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
            credential_store=context.credential_store,
        )
        await gateway.run()
