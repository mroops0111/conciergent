import pathlib

import click.testing
import pytest

from conciergent.cli import main


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
    for command in ('run', 'init'):
        assert command in result.output


def test_init_refuses_to_overwrite(runner: click.testing.CliRunner, tmp_path: pathlib.Path):
    assert runner.invoke(main, ['init', '--path', str(tmp_path)]).exit_code == 0

    assert runner.invoke(main, ['init', '--path', str(tmp_path)]).exit_code != 0
