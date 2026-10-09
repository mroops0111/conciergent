import logging

import httpx
from mcp.types import LATEST_PROTOCOL_VERSION

from conciergent.defaults import DEFAULTS


logger = logging.getLogger(__name__)

# How long the unauthenticated probe waits for a server, kept short so an unreachable one cannot stall a turn.
PROBE_TIMEOUT_SECONDS = 10.0


async def requires_user_authorization(
    url: str, *, client_name: str = DEFAULTS.agent.client_name, timeout_seconds: float = PROBE_TIMEOUT_SECONDS
) -> bool | None:
    """Ask an MCP server, without credentials, whether it needs a per-user authorization.

    MCP authorization happens at the transport, so an unauthenticated ``initialize`` answered with 401 or 403 means
    the server needs a user's token, and a success means anyone may call it.
    Returns None when the server cannot be reached or answers anything else, so the caller can ask again later.
    """
    body = {
        'jsonrpc': '2.0',
        'id': 0,
        'method': 'initialize',
        'params': {
            'protocolVersion': LATEST_PROTOCOL_VERSION,
            'capabilities': {},
            'clientInfo': {'name': client_name, 'version': '0'},
        },
    }
    headers = {'Accept': 'application/json, text/event-stream'}
    try:
        async with httpx.AsyncClient(timeout=timeout_seconds) as client:
            # Stream so only the status is read, a server answering with an event stream never holds the probe open.
            async with client.stream('POST', url, json=body, headers=headers) as response:
                status = response.status_code
                session_id = response.headers.get('mcp-session-id')
            if session_id:
                # End the session the probe opened rather than leave it for the server to time out.
                # The verdict is already known, so a failed cleanup must not turn it into an unknown.
                try:
                    await client.delete(url, headers={'mcp-session-id': session_id})
                except httpx.HTTPError:
                    logger.debug('MCP authorization probe could not end its session on %s', url, exc_info=True)
    except httpx.HTTPError as error:
        logger.warning('MCP authorization probe could not reach %s: %s', url, error)
        return None
    if status in (401, 403):
        return True
    if 200 <= status < 300:
        return False
    logger.warning('MCP authorization probe got an unexpected status %s from %s', status, url)
    return None
