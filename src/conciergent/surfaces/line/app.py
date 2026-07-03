import typing

import fastapi

from ..base import Surface, SurfaceContext
from .webhook import LineWebhookSettings, build_router


class Line(Surface):
    """The LINE platform, one webhook route serving messages and follow events."""

    def __init__(
        self,
        *,
        channel_secret: str,
        channel_access_token: str,
        welcome_text: str = '',
        ready_text: str = '',
        text_formatting_instruction: str = '',
        authorization_title: str = '',
        authorization_link_label: str = '',
    ) -> None:
        self._channel_secret = channel_secret
        self._channel_access_token = channel_access_token
        self._text_overrides = {
            key: value
            for key, value in {
                'welcome_text': welcome_text,
                'ready_text': ready_text,
                'text_formatting_instruction': text_formatting_instruction,
                'authorization_title': authorization_title,
                'authorization_link_label': authorization_link_label,
            }.items()
            if value
        }

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
                    **self._text_overrides,
                ),
                store=context.store,
                agent=context.agent,
                compactor=context.compactor,
            )
        ]
