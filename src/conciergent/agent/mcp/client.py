import collections.abc
import logging
import typing

import httpx
import pydantic
from mcp.client.auth import OAuthClientProvider
from mcp.client.auth.utils import (
    build_oauth_authorization_server_metadata_discovery_urls,
    build_protected_resource_metadata_discovery_urls,
    handle_auth_metadata_response,
    handle_protected_resource_response,
)
from mcp.shared.auth import OAuthClientMetadata, OAuthMetadata
from pydantic_ai import RunContext
from pydantic_ai.mcp import MCPToolset, MCPToolsetClient
from pydantic_ai.tools import ToolDefinition
from pydantic_ai.toolsets import AbstractToolset

from conciergent.agent.mcp.storage import OAuthTokenStorage
from conciergent.defaults import DEFAULTS
from conciergent.runtime import OAuthBridge
from conciergent.store.credential import CredentialStore


logger = logging.getLogger(__name__)

# The metadata does not change per server, so discovery runs once per process rather than on every connect.
_OAUTH_METADATA_CACHE: dict[str, OAuthMetadata] = {}


type ApprovalPredicate = collections.abc.Callable[[RunContext[typing.Any], ToolDefinition, dict[str, typing.Any]], bool]


def needs_approval(ctx: RunContext[typing.Any], tool_def: ToolDefinition, tool_args: dict[str, typing.Any]) -> bool:
    """Gate a tool for human approval when its MCP server annotates it as destructive.

    The signature is pydantic-ai's ``approval_required`` predicate contract,
    so the unused parameters must stay for a custom predicate to swap in cleanly.
    """
    annotations = (tool_def.metadata or {}).get('annotations') or {}
    return bool(annotations.get('destructiveHint'))


async def build_toolset(
    server: MCPToolsetClient,
    *,
    principal: str,
    credential_store: CredentialStore | None = None,
    oauth_bridge: OAuthBridge | None = None,
    redirect_uri: str | None = None,
    approval_predicate: ApprovalPredicate = needs_approval,
    client_name: str = DEFAULTS.agent.client_name,
    read_timeout_seconds: float = DEFAULTS.agent.mcp_read_timeout_seconds,
) -> AbstractToolset[typing.Any]:
    """Build a gated MCP toolset for one MCP server, given as a URL or an already-built client.

    OAuth is attached only for a URL client with both a ``bridge`` and a ``redirect_uri``,
    otherwise the server is reached unauthenticated.
    Every tool the server annotates as destructive is gated for approval before it runs.
    """
    if (oauth_bridge is None) != (redirect_uri is None):
        raise ValueError('bridge and redirect_uri must be given together to enable MCP OAuth')
    if isinstance(server, str):
        if oauth_bridge is not None and redirect_uri is not None:
            if credential_store is None:
                raise ValueError('a credential store is required to persist MCP OAuth tokens')
            auth = await _oauth_provider(
                server,
                credential_store=credential_store,
                principal=principal,
                oauth_bridge=oauth_bridge,
                redirect_uri=redirect_uri,
                client_name=client_name,
            )
            # During OAuth the connect blocks in the callback until the user authorizes, so the init timeout is
            # disabled with 0, the SDK's explicit off switch, leaving the bridge's own wait as the only limit.
            toolset = MCPToolset(server, auth=auth, read_timeout=read_timeout_seconds, init_timeout=0)
        else:
            toolset = MCPToolset(server, read_timeout=read_timeout_seconds)
    else:
        toolset = MCPToolset(server)
    return toolset.approval_required(approval_predicate)


async def _oauth_provider(
    url: str,
    *,
    credential_store: CredentialStore,
    principal: str,
    oauth_bridge: OAuthBridge,
    redirect_uri: str,
    client_name: str,
) -> OAuthClientProvider:
    oauth_bridge_adapter = _OAuthBridgeAdapter(oauth_bridge)
    storage = OAuthTokenStorage(credential_store, server=url, principal=principal)
    provider = OAuthClientProvider(
        server_url=url,
        client_metadata=OAuthClientMetadata(
            client_name=client_name,
            redirect_uris=[pydantic.AnyUrl(redirect_uri)],
            grant_types=['authorization_code', 'refresh_token'],
            response_types=['code'],
        ),
        storage=storage,
        redirect_handler=oauth_bridge_adapter.redirect_handler,
        callback_handler=oauth_bridge_adapter.callback_handler,
    )
    # The SDK loads tokens on connect but restores neither token_expiry_time nor oauth_metadata, so hydrate both.
    # Without them is_token_valid stays true and no refresh fires, so an expired token takes a 401 and re-authorizes.
    # Upstream python-sdk#1784, #2492.
    stored_tokens, stored_expires_at = await storage.get_tokens_with_expiry()
    if stored_tokens is not None:
        provider.context.oauth_metadata = await _discover_oauth_metadata(url)
        provider.context.current_tokens = stored_tokens
        provider.context.token_expiry_time = stored_expires_at
        provider.context.client_info = await storage.get_client_info()
        provider._initialized = True
    return provider


async def _discover_oauth_metadata(server_url: str) -> OAuthMetadata | None:
    # The SDK refreshes a stored token before it discovers metadata, so its token URL falls back to origin plus /token,
    # which is wrong when the server sits under a path prefix like the embedded gateway.
    # Prefetch the metadata with the SDK's own discovery so the refresh reaches the real token endpoint.
    if server_url in _OAUTH_METADATA_CACHE:
        return _OAUTH_METADATA_CACHE[server_url]
    try:
        async with httpx.AsyncClient() as client:
            auth_server_url: str | None = None
            for url in build_protected_resource_metadata_discovery_urls(None, server_url):
                resource_metadata = await handle_protected_resource_response(await client.get(url))
                if resource_metadata is not None and resource_metadata.authorization_servers:
                    auth_server_url = str(resource_metadata.authorization_servers[0])
                    break
            for url in build_oauth_authorization_server_metadata_discovery_urls(auth_server_url, server_url):
                found, metadata = await handle_auth_metadata_response(await client.get(url))
                if found and metadata is not None:
                    _OAUTH_METADATA_CACHE[server_url] = metadata
                    return metadata
    except Exception:
        logger.debug('OAuth metadata discovery failed, the connect will discover it on demand', exc_info=True)
    return None


class _OAuthBridgeAdapter:
    """Present one ``OAuthBridge`` as the pair of callbacks the SDK's ``OAuthClientProvider`` takes.

    The SDK calls ``redirect_handler`` with the authorize URL and then ``callback_handler`` for the code and state,
    this class stores the URL from the first call and delegates the second to the oauth_bridge.
    """

    def __init__(self, oauth_bridge: OAuthBridge) -> None:
        self._oauth_bridge = oauth_bridge
        self._authorize_url: str | None = None

    async def redirect_handler(self, authorization_url: str) -> None:
        self._authorize_url = authorization_url

    async def callback_handler(self) -> tuple[str, str | None]:
        if self._authorize_url is None:
            raise RuntimeError('the redirect handler must run before the callback handler')
        # The bridge returns the code with the state the callback received,
        # which the SDK checks against the state it put in the authorize URL.
        return await self._oauth_bridge.request_authorization(self._authorize_url)
