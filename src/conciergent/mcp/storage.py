import typing

from mcp.client.auth import TokenStorage
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken

from conciergent.stores.base import CredentialStore


class OAuthTokenStorage(TokenStorage):
    """Persist MCP OAuth client info and per-user tokens behind a conciergent ``Store``.

    Client info is registered once per server and shared across users,
    tokens are keyed by both server and principal.
    """

    def __init__(self, store: CredentialStore, *, server: str, principal: str) -> None:
        self._store = store
        self._server = server
        self._principal = principal

    @typing.override
    async def get_tokens(self) -> OAuthToken | None:
        stored = await self._store.get_mcp_token(self._server, self._principal)
        return OAuthToken.model_validate(stored) if stored is not None else None

    @typing.override
    async def set_tokens(self, tokens: OAuthToken) -> None:
        await self._store.set_mcp_token(self._server, self._principal, tokens.model_dump(mode='json'))

    @typing.override
    async def get_client_info(self) -> OAuthClientInformationFull | None:
        stored = await self._store.get_mcp_client(self._server)
        return OAuthClientInformationFull.model_validate(stored) if stored is not None else None

    @typing.override
    async def set_client_info(self, client_info: OAuthClientInformationFull) -> None:
        await self._store.set_mcp_client(self._server, client_info.model_dump(mode='json'))
