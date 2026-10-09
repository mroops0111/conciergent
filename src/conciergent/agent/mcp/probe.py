import logging

import anyio
import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.shared._httpx_utils import create_mcp_http_client
from mcp.types import Implementation

from conciergent.defaults import DEFAULTS


logger = logging.getLogger(__name__)

# How long the unauthenticated probe waits for a server, kept short so an unreachable one cannot stall a turn.
PROBE_TIMEOUT_SECONDS = 10.0


async def requires_user_authorization(
    url: str, *, client_name: str = DEFAULTS.agent.client_name, timeout_seconds: float = PROBE_TIMEOUT_SECONDS
) -> bool | None:
    """Ask an MCP server, without credentials, whether it needs a per-user authorization.

    MCP authorization happens at the transport, so an ``initialize`` the server rejects with 401 or 403 means
    it needs a user's token, and a completed handshake means anyone may call it.
    Returns None when the server cannot be reached or fails otherwise, so the caller can ask again later.
    """
    client_info = Implementation(name=client_name, version='0')
    try:
        with anyio.fail_after(timeout_seconds):
            async with (
                create_mcp_http_client(timeout=httpx.Timeout(timeout_seconds)) as http_client,
                streamable_http_client(url, http_client=http_client) as (read_stream, write_stream, _),
                ClientSession(read_stream, write_stream, client_info=client_info) as session,
            ):
                await session.initialize()
    except Exception as error:
        status = _http_status(error)
        if status in (401, 403):
            return True
        logger.warning('MCP authorization probe could not check %s: %r', url, error)
        return None
    return False


def _http_status(error: BaseException) -> int | None:
    # The SDK raises the transport's HTTP error from inside its task group, so it arrives wrapped in a group.
    if isinstance(error, httpx.HTTPStatusError):
        return error.response.status_code
    if isinstance(error, BaseExceptionGroup):
        for inner in error.exceptions:
            status = _http_status(inner)
            if status is not None:
                return status
    return None
