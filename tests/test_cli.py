import pathlib

import click.testing
import pytest

from conciergent.app import App
from conciergent.cli import main
from conciergent.scheduling import FiringOutcome, UnknownScheduleError


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
    for command in ('run', 'fire', 'init'):
        assert command in result.output


def test_init_refuses_to_overwrite(runner: click.testing.CliRunner, tmp_path: pathlib.Path):
    assert runner.invoke(main, ['init', '--path', str(tmp_path)]).exit_code == 0

    assert runner.invoke(main, ['init', '--path', str(tmp_path)]).exit_code != 0


class _FiringApp:
    def __init__(self, outcome: FiringOutcome) -> None:
        self.outcome = outcome
        self.fired: list[str] = []

    def fire(self, name: str) -> FiringOutcome:
        if name != 'daily-digest':
            raise UnknownScheduleError(f'no schedule named {name!r}')
        self.fired.append(name)
        return self.outcome


@pytest.mark.parametrize(
    ('outcome', 'exit_code'),
    [(FiringOutcome.delivered, 0), (FiringOutcome.skipped, 0), (FiringOutcome.failed, 1)],
)
def test_fire_exits_by_whether_a_retry_is_safe(
    runner: click.testing.CliRunner,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    outcome: FiringOutcome,
    exit_code: int,
):
    app = _FiringApp(outcome)
    monkeypatch.setattr(App, 'from_config', lambda path: app)
    config = tmp_path / 'manifest.yml'
    config.write_text('')

    result = runner.invoke(main, ['fire', 'daily-digest', '--config', str(config)])

    assert result.exit_code == exit_code
    assert app.fired == ['daily-digest']


def test_fire_rejects_an_unknown_schedule_as_a_usage_error(
    runner: click.testing.CliRunner, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
):
    monkeypatch.setattr(App, 'from_config', lambda path: _FiringApp(FiringOutcome.delivered))
    config = tmp_path / 'manifest.yml'
    config.write_text('')

    result = runner.invoke(main, ['fire', 'weekly', '--config', str(config)])

    assert result.exit_code == 2
    assert "no schedule named 'weekly'" in result.output


def test_fire_reports_an_invalid_config_as_a_usage_error(
    runner: click.testing.CliRunner, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
):
    def invalid(path: str) -> App:
        raise ValueError('schedule names must be unique, repeated: daily-digest')

    monkeypatch.setattr(App, 'from_config', invalid)
    config = tmp_path / 'manifest.yml'
    config.write_text('')

    result = runner.invoke(main, ['fire', 'daily-digest', '--config', str(config)])

    assert result.exit_code == 2
    assert 'schedule names must be unique' in result.output


def test_fire_leaves_a_runtime_value_error_as_a_failure_not_a_usage_error(
    runner: click.testing.CliRunner, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
):
    class BrokenApp(_FiringApp):
        def fire(self, name: str) -> FiringOutcome:
            raise ValueError('something broke at run time')

    monkeypatch.setattr(App, 'from_config', lambda path: BrokenApp(FiringOutcome.delivered))
    config = tmp_path / 'manifest.yml'
    config.write_text('')

    result = runner.invoke(main, ['fire', 'daily-digest', '--config', str(config)])

    assert result.exit_code == 1
    assert 'Usage' not in result.output
