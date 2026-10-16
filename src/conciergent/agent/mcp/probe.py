import logging

import anyio
import httpx2
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.types import Implementation

from conciergent.defaults import DEFAULTS


logger = logging.getLogger(__name__)


async def requires_user_authorization(
    url: str,
    *,
    client_name: str = DEFAULTS.agent.client_name,
    timeout_seconds: float = DEFAULTS.agent.mcp_probe_timeout_seconds,
) -> bool | None:
    """Ask an MCP server, without credentials, whether it needs a per-user authorization.

    MCP authorization happens at the transport,
    so an ``initialize`` the server rejects with 401 or 403 means it needs a user's token,
    and a completed handshake means anyone may call it.
    Returns None when the server cannot be reached or fails otherwise, so the caller can ask again later.
    """
    statuses: list[int] = []

    async def record_status(response: httpx2.Response) -> None:
        statuses.append(response.status_code)

    client_info = Implementation(name=client_name, version='0')
    try:
        with anyio.fail_after(timeout_seconds):
            async with (
                # The SDK turns a rejected request into a generic JSON-RPC error, so the client records each status.
                httpx2.AsyncClient(timeout=timeout_seconds, event_hooks={'response': [record_status]}) as http_client,
                streamable_http_client(url, http_client=http_client) as (read_stream, write_stream),
                ClientSession(read_stream, write_stream, client_info=client_info) as session,
            ):
                await session.initialize()
    except Exception as error:
        if any(status in (401, 403) for status in statuses):
            return True
        logger.warning('MCP authorization probe could not check %s: %r', url, error)
        return None
    return False
