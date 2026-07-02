import pathlib

import click.testing

from conciergent.cli import main


def test_init_scaffolds_config_and_manifest(tmp_path: pathlib.Path):
    runner = click.testing.CliRunner()
    result = runner.invoke(main, ['init', '--path', str(tmp_path)])
    assert result.exit_code == 0
    config = (tmp_path / 'conciergent.yml').read_text()
    manifest = (tmp_path / 'slack-app-manifest.yml').read_text()
    assert 'mcp_servers' in config
    assert '/slack/events' in manifest


def test_init_refuses_to_overwrite(tmp_path: pathlib.Path):
    runner = click.testing.CliRunner()
    assert runner.invoke(main, ['init', '--path', str(tmp_path)]).exit_code == 0
    assert runner.invoke(main, ['init', '--path', str(tmp_path)]).exit_code != 0


def test_help_lists_commands():
    runner = click.testing.CliRunner()
    result = runner.invoke(main, ['--help'])
    assert result.exit_code == 0
    for command in ('run', 'dev', 'init'):
        assert command in result.output
