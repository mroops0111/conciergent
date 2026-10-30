import json
import logging
import sys
import typing

import anyio
import pytest

from conciergent import logger


def _record(name: str, exception: BaseException) -> logging.LogRecord:
    return logging.LogRecord(name, logging.ERROR, __file__, 1, 'msg', None, (type(exception), exception, None))


def test_sse_teardown_filter_drops_only_the_benign_teardown_record() -> None:
    sse_filter = logger._SseTeardownFilter()

    assert not sse_filter.filter(_record(logger._SSE_LOGGER_NAME, anyio.ClosedResourceError()))
    assert sse_filter.filter(_record(logger._SSE_LOGGER_NAME, ValueError('a real parse error')))
    assert sse_filter.filter(_record('conciergent.x', anyio.ClosedResourceError()))


@pytest.fixture
def restore_root_logging() -> typing.Iterator[None]:
    root = logging.getLogger()
    handlers, level = root.handlers[:], root.level
    try:
        yield
    finally:
        root.handlers[:] = handlers
        root.setLevel(level)


def test_setup_installs_a_single_root_handler(restore_root_logging: None) -> None:
    logger.setup(level='DEBUG', format='text')

    root = logging.getLogger()
    assert len(root.handlers) == 1
    assert root.level == logging.DEBUG


def test_setup_clears_managed_logger_handlers(restore_root_logging: None) -> None:
    noisy = logging.getLogger('uvicorn.error')
    noisy.addHandler(logging.NullHandler())

    logger.setup()

    assert noisy.handlers == []
    assert noisy.propagate is True


def test_json_formatter_emits_one_object_per_line() -> None:
    record = logging.LogRecord('conciergent.x', logging.INFO, __file__, 1, 'hello %s', ('world',), None)

    payload = json.loads(logger.JsonFormatter().format(record))

    assert payload['level'] == 'INFO'
    assert payload['logger'] == 'conciergent.x'
    assert payload['message'] == 'hello world'


def test_json_formatter_keeps_the_traceback_and_its_cause() -> None:
    try:
        try:
            raise ValueError('totals.facts.value_1 Field required')
        except ValueError as cause:
            raise RuntimeError('Exceeded maximum output retries') from cause
    except RuntimeError:
        record = logging.LogRecord('conciergent.push', logging.ERROR, __file__, 1, 'Push failed', None, sys.exc_info())

    payload = json.loads(logger.JsonFormatter().format(record))

    assert payload['exception'].startswith('Traceback')
    assert 'RuntimeError: Exceeded maximum output retries' in payload['exception']
    assert 'ValueError: totals.facts.value_1 Field required' in payload['exception']
    assert 'stack' not in payload


def test_json_formatter_keeps_the_stack_and_leaves_out_an_empty_exception() -> None:
    record = logging.LogRecord(
        'conciergent.x', logging.INFO, __file__, 1, 'msg', None, None, sinfo='Stack (most recent call last):'
    )

    payload = json.loads(logger.JsonFormatter().format(record))

    assert payload['stack'] == 'Stack (most recent call last):'
    assert 'exception' not in payload
