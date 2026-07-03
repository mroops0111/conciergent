import collections.abc
import typing

import pydantic
from mcp.client.auth import OAuthClientProvider
from mcp.shared.auth import OAuthClientMetadata
from pydantic_ai import RunContext
from pydantic_ai.mcp import MCPToolset, MCPToolsetClient
from pydantic_ai.tools import ToolDefinition
from pydantic_ai.toolsets import AbstractToolset

from ..runtime import OAuthBridge
from ..stores.base import CredentialStore
from .storage import OAuthTokenStorage


DEFAULT_READ_TIMEOUT_SECONDS = 120.0
_DEFAULT_CLIENT_NAME = 'conciergent'

ApprovalPredicate = collections.abc.Callable[[RunContext[typing.Any], ToolDefinition, dict[str, typing.Any]], bool]


def needs_approval(ctx: RunContext[typing.Any], tool_def: ToolDefinition, tool_args: dict[str, typing.Any]) -> bool:
    """Gate a tool for human approval when its MCP server annotates it as destructive.

    The signature is pydantic-ai's ``approval_required`` predicate contract,
    so the unused parameters must stay for a custom predicate to swap in cleanly.
    """
    annotations = (tool_def.metadata or {}).get('annotations') or {}
    return bool(annotations.get('destructiveHint'))


def build_toolset(
    server: MCPToolsetClient,
    *,
    principal: str,
    store: CredentialStore | None = None,
    bridge: OAuthBridge | None = None,
    redirect_uri: str | None = None,
    approval_predicate: ApprovalPredicate = needs_approval,
    client_name: str = _DEFAULT_CLIENT_NAME,
    read_timeout_seconds: float = DEFAULT_READ_TIMEOUT_SECONDS,
) -> AbstractToolset[typing.Any]:
    """Build a gated MCP toolset for one MCP server, given as a URL or an already-built client.

    OAuth is attached only for a URL client with both a ``bridge`` and a ``redirect_uri``,
    otherwise the server is reached unauthenticated.
    Every tool the server annotates as destructive is gated for approval before it runs.
    """
    if (bridge is None) != (redirect_uri is None):
        raise ValueError('bridge and redirect_uri must be given together to enable MCP OAuth')
    if isinstance(server, str):
        auth = None
        if bridge is not None and redirect_uri is not None:
            if store is None:
                raise ValueError('store is required to persist MCP OAuth tokens')
            auth = _oauth_provider(
                server,
                store=store,
                principal=principal,
                bridge=bridge,
                redirect_uri=redirect_uri,
                client_name=client_name,
            )
        toolset = MCPToolset(server, auth=auth, read_timeout=read_timeout_seconds)
    else:
        toolset = MCPToolset(server)
    return toolset.approval_required(approval_predicate)


def _oauth_provider(
    url: str, *, store: CredentialStore, principal: str, bridge: OAuthBridge, redirect_uri: str, client_name: str
) -> OAuthClientProvider:
    callbacks = _BridgeCallbacks(bridge)
    return OAuthClientProvider(
        server_url=url,
        client_metadata=OAuthClientMetadata(
            client_name=client_name,
            redirect_uris=[pydantic.AnyUrl(redirect_uri)],
            grant_types=['authorization_code', 'refresh_token'],
            response_types=['code'],
        ),
        storage=OAuthTokenStorage(store, server=url, principal=principal),
        redirect_handler=callbacks.redirect_handler,
        callback_handler=callbacks.callback_handler,
    )


class _BridgeCallbacks:
    """Present one ``OAuthBridge`` as the pair of callbacks the SDK's ``OAuthClientProvider`` takes.

    The SDK calls ``redirect_handler`` with the authorize URL and then ``callback_handler`` for the code,
    this class stores the URL from the first call and delegates the second to the bridge.
    """

    def __init__(self, bridge: OAuthBridge) -> None:
        self._bridge = bridge
        self._authorize_url: str | None = None

    async def redirect_handler(self, authorization_url: str) -> None:
        self._authorize_url = authorization_url

    async def callback_handler(self) -> tuple[str, str | None]:
        if self._authorize_url is None:
            raise RuntimeError('the redirect handler must run before the callback handler')
        code = await self._bridge.request_authorization(self._authorize_url)
        return code, None
