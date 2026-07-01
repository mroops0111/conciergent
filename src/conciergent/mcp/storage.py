"""MCP OAuth token storage, backed by a conciergent ``Store``.

Adapts the MCP client SDK's ``TokenStorage`` interface onto the pluggable
``Store`` so a user's per-server OAuth credentials survive across turns and
restarts. Each instance is scoped to a single ``(server, principal)`` pair.
"""

from __future__ import annotations

import time

from mcp.client.auth import TokenStorage
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken

from ..stores.base import Store


_EXPIRES_AT_KEY = '_expires_at'


class MCPTokenStorage(TokenStorage):
    """Persist one user's OAuth client registration and tokens for one MCP server."""

    def __init__(self, store: Store, *, server: str, principal: str) -> None:
        self._store = store
        self._server = server
        self._principal = principal

    async def get_client_info(self) -> OAuthClientInformationFull | None:
        info = await self._store.get_mcp_client_info(self._server, self._principal)
        return OAuthClientInformationFull.model_validate(info) if info is not None else None

    async def set_client_info(self, client_info: OAuthClientInformationFull) -> None:
        await self._store.set_mcp_client_info(self._server, self._principal, client_info.model_dump(mode='json'))

    async def get_tokens(self) -> OAuthToken | None:
        tokens, _ = await self.get_tokens_with_expiry()
        return tokens

    async def set_tokens(self, tokens: OAuthToken) -> None:
        # Dynamic client registration must persist client info before tokens.
        if await self._store.get_mcp_client_info(self._server, self._principal) is None:
            raise RuntimeError('client info must be stored before tokens (dynamic client registration order)')
        payload = tokens.model_dump(mode='json')
        if tokens.expires_in is not None:
            # OAuthToken carries only a relative lifetime; stamp an absolute expiry so a
            # refresh can be scheduled correctly even after a process restart.
            payload[_EXPIRES_AT_KEY] = time.time() + tokens.expires_in
        await self._store.set_mcp_token(self._server, self._principal, payload)

    async def get_tokens_with_expiry(self) -> tuple[OAuthToken | None, float | None]:
        """Return the stored token together with its absolute expiry (epoch seconds),
        if known. The expiry is needed to hydrate a client provider after a restart."""
        payload = await self._store.get_mcp_token(self._server, self._principal)
        if payload is None:
            return None, None
        payload = dict(payload)
        expires_at = payload.pop(_EXPIRES_AT_KEY, None)
        return OAuthToken.model_validate(payload), expires_at
