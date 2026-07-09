import importlib.resources
import pathlib

import click

from conciergent.app import App


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
