import collections.abc
import typing

import typing_extensions

from .base import Store


class CompositeStore(Store):
    """Route message-bearing state and credentials to different backends.

    Conversation content (history, parked approvals, dedupe keys, OAuth handoff) carries user
    messages and belongs on an expiring backend, while credentials (MCP tokens and clients, bot
    tokens) must survive restarts. This split keeps message privacy on TTL storage without giving
    up durable credentials.
    """

    def __init__(self, *, messages: Store, credentials: Store) -> None:
        self._messages = messages
        self._credentials = credentials

    @typing_extensions.override
    async def prepare(self) -> None:
        await self._messages.prepare()
        await self._credentials.prepare()

    @typing_extensions.override
    async def load_history(self, conversation: str) -> list[typing.Any]:
        return await self._messages.load_history(conversation)

    @typing_extensions.override
    async def append_history(self, conversation: str, messages: list[typing.Any], *, ttl_seconds: int) -> None:
        await self._messages.append_history(conversation, messages, ttl_seconds=ttl_seconds)

    @typing_extensions.override
    async def replace_history(self, conversation: str, messages: list[typing.Any], *, ttl_seconds: int) -> None:
        await self._messages.replace_history(conversation, messages, ttl_seconds=ttl_seconds)

    @typing_extensions.override
    async def dedupe(self, key: str, *, ttl_seconds: int) -> bool:
        return await self._messages.dedupe(key, ttl_seconds=ttl_seconds)

    @typing_extensions.override
    async def park_approval(
        self, conversation: str, state: collections.abc.Mapping[str, typing.Any], *, ttl_seconds: int
    ) -> None:
        await self._messages.park_approval(conversation, state, ttl_seconds=ttl_seconds)

    @typing_extensions.override
    async def take_approval(self, conversation: str) -> dict[str, typing.Any] | None:
        return await self._messages.take_approval(conversation)

    @typing_extensions.override
    async def deliver_oauth_code(self, state: str, code: str) -> None:
        await self._messages.deliver_oauth_code(state, code)

    @typing_extensions.override
    async def await_oauth_code(self, state: str, *, timeout_seconds: float) -> str | None:
        return await self._messages.await_oauth_code(state, timeout_seconds=timeout_seconds)

    @typing_extensions.override
    async def get_mcp_token(self, server: str, principal: str) -> dict[str, typing.Any] | None:
        return await self._credentials.get_mcp_token(server, principal)

    @typing_extensions.override
    async def set_mcp_token(self, server: str, principal: str, token: collections.abc.Mapping[str, typing.Any]) -> None:
        await self._credentials.set_mcp_token(server, principal, token)

    @typing_extensions.override
    async def get_mcp_client(self, server: str) -> dict[str, typing.Any] | None:
        return await self._credentials.get_mcp_client(server)

    @typing_extensions.override
    async def set_mcp_client(self, server: str, client: collections.abc.Mapping[str, typing.Any]) -> None:
        await self._credentials.set_mcp_client(server, client)

    @typing_extensions.override
    async def resolve_bot_token(self, surface: str, tenant: str) -> str | None:
        return await self._credentials.resolve_bot_token(surface, tenant)

    @typing_extensions.override
    async def set_bot_token(self, surface: str, tenant: str, token: str) -> None:
        await self._credentials.set_bot_token(surface, tenant, token)
