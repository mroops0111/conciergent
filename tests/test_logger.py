import json
import logging
import typing

import pytest

from conciergent import logger


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
