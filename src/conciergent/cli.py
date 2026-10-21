import importlib.resources
import pathlib

import click

from conciergent.app import App
from conciergent.scheduling import FiringOutcome


def _template(name: str) -> str:
    return importlib.resources.files('conciergent').joinpath('templates', name).read_text(encoding='utf-8')


@click.group()
def main() -> None:
    """Give your MCP tools a chat face."""


@main.command()
@click.option('--config', 'config_path', type=click.Path(exists=True), default='manifest.yml')
def run(config_path: str) -> None:
    """Serve the webhook application."""
    App.from_config(config_path).run()


@main.command()
@click.argument('schedule_name')
@click.option('--config', 'config_path', type=click.Path(exists=True), default='manifest.yml')
def fire(schedule_name: str, config_path: str) -> None:
    """Run one schedule now and push its reply, for cron or a Kubernetes CronJob to call.

    Exits 0 once the reply is out or the agent chose to skip.
    Exits 1 when the firing failed before anything was pushed, the only case a retry cannot repeat a reply.
    Exits 2 for a bad config or schedule name.
    """
    try:
        outcome = App.from_config(config_path).fire(schedule_name)
    except ValueError as error:
        raise click.UsageError(str(error)) from error
    if outcome is FiringOutcome.failed:
        raise SystemExit(1)


@main.command()
@click.option('--path', type=click.Path(), default='.')
def init(path: str) -> None:
    """Scaffold a config file into the target directory."""
    target = pathlib.Path(path)
    target.mkdir(parents=True, exist_ok=True)
    config_file = target / 'manifest.yml'
    if config_file.exists():
        raise click.UsageError(f'{config_file} already exists.')
    config_file.write_text(_template('manifest.yml'))
    click.echo(f'Wrote {config_file}. Fill in the env vars, then: conciergent run')


if __name__ == '__main__':
    main()
