import collections.abc
import typing

import fastapi

from ..base import Surface, SurfaceContext
from .install import SlackInstallSettings, build_install_router
from .webhook import SlackWebhookSettings, build_router


def _webhook_settings(
    *, signing_secret: str, fallback_bot_token: str, text_formatting_instruction: str
) -> SlackWebhookSettings:
    """Build settings where an empty override falls back to the platform default."""
    if text_formatting_instruction:
        return SlackWebhookSettings(
            signing_secret=signing_secret,
            fallback_bot_token=fallback_bot_token,
            text_formatting_instruction=text_formatting_instruction,
        )
    return SlackWebhookSettings(signing_secret=signing_secret, fallback_bot_token=fallback_bot_token)


_DEFAULT_SCOPES = ('chat:write', 'im:history', 'im:read', 'im:write', 'users:read')


class Slack(Surface):
    """The Slack platform, webhook routes plus the optional multi-workspace install flow."""

    def __init__(
        self,
        *,
        signing_secret: str,
        client_id: str = '',
        client_secret: str = '',
        scopes: collections.abc.Sequence[str] = _DEFAULT_SCOPES,
        bot_token: str = '',
        text_formatting_instruction: str = '',
        processing_text: str = '',
        authorization_title: str = '',
        authorization_link_label: str = '',
    ) -> None:
        self._signing_secret = signing_secret
        self._client_id = client_id
        self._client_secret = client_secret
        self._scopes = tuple(scopes)
        self._bot_token = bot_token
        self._text_overrides = {
            key: value
            for key, value in {
                'text_formatting_instruction': text_formatting_instruction,
                'processing_text': processing_text,
                'authorization_title': authorization_title,
                'authorization_link_label': authorization_link_label,
            }.items()
            if value
        }

    @typing.override
    def build_routers(self, context: SurfaceContext) -> list[fastapi.APIRouter]:
        routers = [
            build_router(
                settings=SlackWebhookSettings(signing_secret=self._signing_secret, fallback_bot_token=self._bot_token),
                store=context.store,
                agent=context.agent,
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
                    store=context.store,
                )
            )
        return routers
