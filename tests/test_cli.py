import asyncio
import contextlib
import pathlib
import threading
import typing

import click.testing
import pytest
import yaml

from conciergent.app import App
from conciergent.cli import main
from conciergent.control import ControlServer
from conciergent.push.outcome import PushOutcome, UnknownPushError


@pytest.fixture
def runner() -> click.testing.CliRunner:
    return click.testing.CliRunner()


def test_init_scaffolds_the_config(runner: click.testing.CliRunner, tmp_path: pathlib.Path):
    result = runner.invoke(main, ['init', '--path', str(tmp_path)])

    assert result.exit_code == 0
    config = (tmp_path / 'manifest.yml').read_text()
    assert 'mcp_servers' in config


def test_help_lists_commands(runner: click.testing.CliRunner):
    result = runner.invoke(main, ['--help'])

    assert result.exit_code == 0
    for command in ('run', 'push', 'init'):
        assert command in result.output


def test_init_refuses_to_overwrite(runner: click.testing.CliRunner, tmp_path: pathlib.Path):
    assert runner.invoke(main, ['init', '--path', str(tmp_path)]).exit_code == 0

    assert runner.invoke(main, ['init', '--path', str(tmp_path)]).exit_code != 0


class _StubApp:
    def __init__(self, outcome: PushOutcome) -> None:
        self.outcome = outcome
        self.pushed: list[str] = []

    def push(self, name: str) -> PushOutcome:
        if name != 'daily-digest':
            raise UnknownPushError(f'no push named {name!r}')
        self.pushed.append(name)
        return self.outcome


@pytest.mark.parametrize(
    ('outcome', 'exit_code'),
    [(PushOutcome.delivered, 0), (PushOutcome.skipped, 0), (PushOutcome.failed, 1)],
)
def test_fire_exits_by_whether_a_retry_is_safe(
    runner: click.testing.CliRunner,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    outcome: PushOutcome,
    exit_code: int,
):
    app = _StubApp(outcome)
    monkeypatch.setattr(App, 'from_config', lambda path: app)
    config = tmp_path / 'manifest.yml'
    config.write_text('')

    result = runner.invoke(main, ['push', 'daily-digest', '--config', str(config)])

    assert result.exit_code == exit_code
    assert app.pushed == ['daily-digest']


def test_push_rejects_an_unknown_push_as_a_usage_error(
    runner: click.testing.CliRunner, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
):
    monkeypatch.setattr(App, 'from_config', lambda path: _StubApp(PushOutcome.delivered))
    config = tmp_path / 'manifest.yml'
    config.write_text('')

    result = runner.invoke(main, ['push', 'weekly', '--config', str(config)])

    assert result.exit_code == 2
    assert "no push named 'weekly'" in result.output


def test_fire_reports_an_invalid_config_as_a_usage_error(
    runner: click.testing.CliRunner, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
):
    def invalid(path: str) -> App:
        raise ValueError('push names must be unique, repeated: daily-digest')

    monkeypatch.setattr(App, 'from_config', invalid)
    config = tmp_path / 'manifest.yml'
    config.write_text('')

    result = runner.invoke(main, ['push', 'daily-digest', '--config', str(config)])

    assert result.exit_code == 2
    assert 'push names must be unique' in result.output


def test_fire_leaves_a_runtime_value_error_as_a_failure_not_a_usage_error(
    runner: click.testing.CliRunner, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
):
    class BrokenApp(_StubApp):
        def push(self, name: str) -> PushOutcome:
            raise ValueError('something broke at run time')

    monkeypatch.setattr(App, 'from_config', lambda path: BrokenApp(PushOutcome.delivered))
    config = tmp_path / 'manifest.yml'
    config.write_text('')

    result = runner.invoke(main, ['push', 'daily-digest', '--config', str(config)])

    assert result.exit_code == 1
    assert 'Usage' not in result.output


@contextlib.contextmanager
def _serving_control(path: str, run_push: typing.Any) -> typing.Iterator[None]:
    """Serve a control socket on its own event loop thread, standing in for a serving app."""
    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()
    server = ControlServer(path, run_push)
    asyncio.run_coroutine_threadsafe(server.__aenter__(), loop).result(timeout=5)
    try:
        yield
    finally:
        asyncio.run_coroutine_threadsafe(server.__aexit__(None, None, None), loop).result(timeout=5)
        loop.call_soon_threadsafe(loop.stop)
        thread.join(timeout=5)
        loop.close()


def _config_with_control_socket(tmp_path: pathlib.Path, control_socket: str) -> pathlib.Path:
    config = tmp_path / 'manifest.yml'
    config.write_text(yaml.safe_dump({'server': {'control_socket': control_socket}}))
    return config


def _no_app_in_this_process(path: str) -> App:
    raise AssertionError('the push should have run in the serving app')


@pytest.mark.parametrize(
    ('outcome', 'exit_code'),
    [(PushOutcome.delivered, 0), (PushOutcome.skipped, 0), (PushOutcome.failed, 1)],
)
def test_push_hands_over_to_a_serving_app_with_the_same_exit_codes(
    runner: click.testing.CliRunner,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    control_socket: str,
    outcome: PushOutcome,
    exit_code: int,
):
    monkeypatch.setattr(App, 'from_config', _no_app_in_this_process)
    pushed: list[str] = []

    async def run_push(name: str) -> PushOutcome:
        pushed.append(name)
        return outcome

    config = _config_with_control_socket(tmp_path, control_socket)
    with _serving_control(control_socket, run_push):
        result = runner.invoke(main, ['push', 'daily-digest', '--config', str(config)])

    assert result.exit_code == exit_code
    assert pushed == ['daily-digest']


def test_push_reports_an_unknown_push_from_the_serving_app_as_a_usage_error(
    runner: click.testing.CliRunner, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, control_socket: str
):
    monkeypatch.setattr(App, 'from_config', _no_app_in_this_process)

    async def run_push(name: str) -> PushOutcome:
        raise UnknownPushError(f'no push named {name!r}, the configured ones are: daily-digest')

    config = _config_with_control_socket(tmp_path, control_socket)
    with _serving_control(control_socket, run_push):
        result = runner.invoke(main, ['push', 'weekly', '--config', str(config)])

    assert result.exit_code == 2
    assert "no push named 'weekly'" in result.output


def test_push_exits_3_when_the_serving_app_stops_answering(
    runner: click.testing.CliRunner, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, control_socket: str
):
    monkeypatch.setattr(App, 'from_config', _no_app_in_this_process)

    async def run_push(name: str) -> PushOutcome:
        raise RuntimeError('the app went down mid-push')

    config = _config_with_control_socket(tmp_path, control_socket)
    with _serving_control(control_socket, run_push):
        result = runner.invoke(main, ['push', 'daily-digest', '--config', str(config)])

    assert result.exit_code == 3
    assert 'before running the push again' in result.output


def test_push_runs_in_process_when_no_app_serves_the_control_socket(
    runner: click.testing.CliRunner, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, control_socket: str
):
    app = _StubApp(PushOutcome.delivered)
    monkeypatch.setattr(App, 'from_config', lambda path: app)
    config = _config_with_control_socket(tmp_path, control_socket)

    result = runner.invoke(main, ['push', 'daily-digest', '--config', str(config)])

    assert result.exit_code == 0
    assert app.pushed == ['daily-digest']
    assert 'runs in this process' in result.output
