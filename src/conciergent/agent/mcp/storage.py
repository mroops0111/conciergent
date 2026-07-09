import time
import typing

from mcp.client.auth import TokenStorage
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken

from conciergent.store.credential import CredentialStore


# Stored beside the token so its absolute expiry survives a reload, which the MCP SDK otherwise drops.
_EXPIRES_AT_KEY = '_expires_at'


class OAuthTokenStorage(TokenStorage):
    """Persist MCP OAuth client info and per-user tokens behind a ``CredentialStore``.

    Client info is registered once per server and shared across users,
    tokens are keyed by both server and principal.
    """

    def __init__(self, credential_store: CredentialStore, *, server: str, principal: str) -> None:
        self._credential_store = credential_store
        self._server = server
        self._principal = principal

    @typing.override
    async def get_tokens(self) -> OAuthToken | None:
        tokens, _ = await self.get_tokens_with_expiry()
        return tokens

    async def get_tokens_with_expiry(self) -> tuple[OAuthToken | None, float | None]:
        """Return the stored token with the absolute expiry the provider restores, or ``(None, None)``."""
        stored = await self._credential_store.get_mcp_token(self._server, self._principal)
        if stored is None:
            return None, None
        expires_at = stored.pop(_EXPIRES_AT_KEY, None)
        return OAuthToken.model_validate(stored), expires_at

    @typing.override
    async def set_tokens(self, tokens: OAuthToken) -> None:
        payload = tokens.model_dump(mode='json')
        if tokens.expires_in is not None:
            # An absolute expiry lets the provider restore token_expiry_time on reload and refresh before a request,
            # rather than sending an expired token, taking a 401, and re-running the whole authorization.
            payload[_EXPIRES_AT_KEY] = time.time() + tokens.expires_in
        await self._credential_store.set_mcp_token(self._server, self._principal, payload)

    async def delete_tokens(self) -> None:
        await self._credential_store.delete_mcp_token(self._server, self._principal)

    @typing.override
    async def get_client_info(self) -> OAuthClientInformationFull | None:
        stored = await self._credential_store.get_mcp_client(self._server)
        return OAuthClientInformationFull.model_validate(stored) if stored is not None else None

    @typing.override
    async def set_client_info(self, client_info: OAuthClientInformationFull) -> None:
        await self._credential_store.set_mcp_client(self._server, client_info.model_dump(mode='json'))
