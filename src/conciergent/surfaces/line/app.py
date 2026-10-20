import collections.abc
import contextlib
import typing

import fastapi

from conciergent.defaults import DEFAULTS
from conciergent.group_chat import GroupChatPolicy
from conciergent.identity import ChatSurface
from conciergent.surfaces.base import PushTarget, Surface, SurfaceContext
from conciergent.surfaces.line.chat import line_push_conversation
from conciergent.surfaces.line.surface import LineMessenger, LineReplySurface, ReplyTokenSlot
from conciergent.surfaces.line.webhook import LineWebhookSettings, build_router


class Line(Surface):
    """The LINE platform, one webhook route serving direct messages, follow events, and opt-in group chats."""

    name = ChatSurface.line

    def __init__(
        self,
        *,
        channel_secret: str,
        channel_access_token: str,
        brand_color: str = DEFAULTS.surface.line.brand_color,
        destructive_color: str = DEFAULTS.surface.line.destructive_color,
        api_timeout_seconds: float = DEFAULTS.surface.line.api_timeout_seconds,
        group_chat_policy: GroupChatPolicy = GroupChatPolicy(),
    ) -> None:
        super().__init__(group_chat_policy=group_chat_policy)
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
                    group_chat_policy=self.group_chat_policy,
                ),
                message_store=context.message_store,
                runner=context.runner,
                compactor=context.compactor,
            )
        ]

    @typing.override
    @contextlib.asynccontextmanager
    async def push_target(self, to: str) -> collections.abc.AsyncIterator[PushTarget]:
        conversation = line_push_conversation(to)
        async with LineMessenger(self._channel_access_token, timeout_seconds=self._api_timeout_seconds) as messenger:
            # With no reply token every message goes out through the push API, and nobody waits on a loading animation.
            slot = ReplyTokenSlot(messenger, to=to, reply_token=None, show_loading=False)
            surface = LineReplySurface(slot, brand_color=self._brand_color, destructive_color=self._destructive_color)
            yield PushTarget(conversation=conversation, surface=surface)
