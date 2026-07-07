import datetime
import json
import logging
import os
import sys
import typing

import anyio
from mcp.client import streamable_http


# The filter matches the mcp SDK's own logger, whose name is this module's __name__, so it is derived not a literal.
# A hard-coded string would silently go stale if the SDK ever renamed the module.
_SSE_LOGGER_NAME = streamable_http.__name__


LEVELS = ('DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL')
FORMATS = ('text', 'json')
TEXT_FORMAT = '%(asctime)s [%(levelname)s] %(name)s: %(message)s'

# Loggers whose own handlers are cleared so their records propagate through this configuration,
# namely conciergent's own tree, the embedded gateway, and the servers it runs under.
MANAGED_PREFIXES = ('conciergent', 'openapi_mcp_gateway', 'uvicorn', 'mcp')

LEVEL_COLORS = {
    'DEBUG': '\033[36m',
    'INFO': '\033[32m',
    'WARNING': '\033[33m',
    'ERROR': '\033[31m',
    'CRITICAL': '\033[1;31m',
}
LOGGER_COLOR = '\033[34m'
RESET = '\033[0m'


def iso_time(record: logging.LogRecord) -> str:
    """Format ``record.created`` as ISO-8601 with millisecond precision."""
    return datetime.datetime.fromtimestamp(record.created).isoformat(timespec='milliseconds')


class JsonFormatter(logging.Formatter):
    """Emit one JSON object per log line (time, level, logger, message)."""

    @typing.override
    def format(self, record: logging.LogRecord) -> str:
        return json.dumps(
            {
                'time': iso_time(record),
                'level': record.levelname,
                'logger': record.name,
                'message': record.getMessage(),
            },
            ensure_ascii=False,
        )


class TextFormatter(logging.Formatter):
    """Human-readable log lines, with optional ANSI color on the level and logger names."""

    default_time_format = '%Y-%m-%dT%H:%M:%S'
    default_msec_format = '%s.%03d'

    def __init__(self, use_color: bool = False) -> None:
        super().__init__(TEXT_FORMAT)
        self.use_color = use_color

    @typing.override
    def format(self, record: logging.LogRecord) -> str:
        if not self.use_color:
            return super().format(record)
        color = LEVEL_COLORS.get(record.levelname, '')
        original_level, original_name = record.levelname, record.name
        record.levelname = f'{color}{original_level}{RESET}'
        record.name = f'{LOGGER_COLOR}{original_name}{RESET}'
        try:
            return super().format(record)
        finally:
            record.levelname, record.name = original_level, original_name


class _SseTeardownFilter(logging.Filter):
    @typing.override
    def filter(self, record: logging.LogRecord) -> bool:
        # The streamable HTTP client races its background SSE task against the per-turn session teardown,
        # so it logs a closed or broken stream error after the turn already replied.
        # Drop that one record while keeping any real streamable-client error.
        if record.name == _SSE_LOGGER_NAME and record.exc_info is not None:
            return not isinstance(record.exc_info[1], anyio.ClosedResourceError | anyio.BrokenResourceError)
        return True


def stderr_supports_color() -> bool:
    """Return False when ``NO_COLOR`` is set or stderr is not a TTY."""
    if os.environ.get('NO_COLOR'):
        return False
    return sys.stderr.isatty()


def setup(level: str = 'INFO', format: str = 'text', file: str | None = None) -> None:
    """Attach one JSON or text handler to the root logger for the whole process.

    Clears existing root handlers on each call, so it is safe to call once at startup.
    Managed loggers (``conciergent`` / the embedded gateway / ``uvicorn`` / ``mcp``) have their own
    handlers stripped so their records flow through this configuration instead of a duplicate format.
    stderr uses color only when it is a TTY and ``NO_COLOR`` is unset.
    """
    is_json = format == 'json'

    stderr_handler = logging.StreamHandler(sys.stderr)
    stderr_handler.setFormatter(JsonFormatter() if is_json else TextFormatter(use_color=stderr_supports_color()))
    handlers: list[logging.Handler] = [stderr_handler]

    if file:
        file_handler = logging.FileHandler(file, encoding='utf-8')
        file_handler.setFormatter(JsonFormatter() if is_json else TextFormatter())
        handlers.append(file_handler)

    teardown_filter = _SseTeardownFilter()
    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(level.upper())
    for handler in handlers:
        handler.addFilter(teardown_filter)
        root.addHandler(handler)

    for name in list(logging.Logger.manager.loggerDict):
        if name.startswith(MANAGED_PREFIXES):
            managed = logging.getLogger(name)
            managed.handlers.clear()
            managed.propagate = True
            managed.setLevel(logging.NOTSET)
