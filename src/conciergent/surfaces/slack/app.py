import collections.abc
import typing

import fastapi

from conciergent.defaults import DEFAULTS
from conciergent.groups import GroupPolicy
from conciergent.surfaces.base import Surface, SurfaceContext
from conciergent.surfaces.slack.install import SlackInstallSettings, build_install_router
from conciergent.surfaces.slack.webhook import SlackWebhookSettings, build_router


class Slack(Surface):
    """The Slack platform, webhook routes plus the optional multi-workspace install flow."""

    # The scopes the bot needs to function, so they are fixed by the surface rather than configured.
    DEFAULT_SCOPES = ('chat:write', 'im:history', 'im:read', 'im:write', 'users:read')
    # Added when group chats are on, to receive mentions, or with reply_to=all every message, in a channel.
    MENTION_SCOPES = ('app_mentions:read',)
    CHANNEL_HISTORY_SCOPES = ('channels:history', 'groups:history', 'mpim:history')

    def __init__(
        self,
        *,
        signing_secret: str,
        client_id: str = '',
        client_secret: str = '',
        scopes: collections.abc.Sequence[str] | None = None,
        bot_token: str = '',
        brand_color: str = DEFAULTS.surface.slack.brand_color,
        destructive_color: str = DEFAULTS.surface.slack.destructive_color,
        api_timeout_seconds: float = DEFAULTS.surface.slack.api_timeout_seconds,
        groups: GroupPolicy = GroupPolicy(),
    ) -> None:
        super().__init__(groups=groups)
        self._signing_secret = signing_secret
        self._client_id = client_id
        self._client_secret = client_secret
        self._scopes = tuple(scopes) if scopes is not None else self.default_scopes(groups)
        self._bot_token = bot_token
        self._brand_color = brand_color
        self._destructive_color = destructive_color
        self._api_timeout_seconds = api_timeout_seconds

    @classmethod
    def default_scopes(cls, groups: GroupPolicy = GroupPolicy()) -> tuple[str, ...]:
        """The bot scopes the install flow requests, widened for channels when group chats are on."""
        if not groups.enabled:
            return cls.DEFAULT_SCOPES
        channel_scopes = cls.MENTION_SCOPES if groups.reply_to == 'mention' else cls.CHANNEL_HISTORY_SCOPES
        return (*cls.DEFAULT_SCOPES, *channel_scopes)

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
                    groups=self.groups,
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
