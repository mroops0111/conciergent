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
    ) -> None:
        self._channel_secret = channel_secret
        self._channel_access_token = channel_access_token
        self._welcome_text = welcome_text
        self._ready_text = ready_text
        self._text_formatting_instruction = text_formatting_instruction

    @typing.override
    def build_routers(self, context: SurfaceContext) -> list[fastapi.APIRouter]:
        overrides = {
            key: value
            for key, value in {
                'welcome_text': self._welcome_text,
                'ready_text': self._ready_text,
                'text_formatting_instruction': self._text_formatting_instruction,
            }.items()
            if value
        }
        return [
            build_router(
                settings=LineWebhookSettings(
                    channel_secret=self._channel_secret,
                    channel_access_token=self._channel_access_token,
                    **overrides,
                ),
                store=context.store,
                agent=context.agent,
                compactor=context.compactor,
            )
        ]
