import importlib.resources
import pathlib

import click

from conciergent.config_layer import yaml_layer
from conciergent.control import ControlConnectionLostError, ControlUnavailableError, request_push
from conciergent.push_outcome import PushOutcome, UnknownPushError


# The app is imported only where a command builds it, so a push handed to a serving app loads no agent code.


def _template(name: str) -> str:
    return importlib.resources.files('conciergent').joinpath('templates', name).read_text(encoding='utf-8')


@click.group()
def main() -> None:
    """Give your MCP tools a chat face."""


@main.command()
@click.option('--config', 'config_path', type=click.Path(exists=True), default='manifest.yml')
def run(config_path: str) -> None:
    """Serve the webhook application."""
    from conciergent.app import App

    App.from_config(config_path).run()


@main.command()
@click.argument('push_name')
@click.option('--config', 'config_path', type=click.Path(exists=True), default='manifest.yml')
def push(push_name: str, config_path: str) -> None:
    """Run one push now and send its reply, for cron or a Kubernetes CronJob to call.

    With ``server.control_socket`` set and an app serving there, the push runs inside that app.
    Otherwise it runs in this process.
    Exits 0 once the reply is out or the agent chose to skip.
    Exits 1 when the push failed before any reply was pushed, the only case a retry cannot repeat a reply.
    Exits 2 for a bad config or push name.
    Exits 3 when the serving app took the push but stopped answering before it said how the push ended.
    """
    try:
        outcome = _push_through_control_socket(push_name, config_path)
        if outcome is None:
            outcome = _push_in_process(push_name, config_path)
    except UnknownPushError as error:
        raise click.UsageError(str(error)) from error
    except ControlConnectionLostError as error:
        click.echo(f"{error}. Check the app's logs before running the push again.", err=True)
        raise SystemExit(3) from error
    if outcome is PushOutcome.failed:
        raise SystemExit(1)


def _push_through_control_socket(push_name: str, config_path: str) -> PushOutcome | None:
    """Hand the push to the serving app, or return None when no app takes pushes on a control socket."""
    try:
        server = yaml_layer(config_path).get('server') or {}
    except Exception as error:
        raise click.UsageError(f'cannot read {config_path}: {error}') from error
    control_socket = server.get('control_socket') or ''
    if not control_socket:
        return None
    try:
        return request_push(control_socket, push_name)
    except ControlUnavailableError as error:
        click.echo(f'{error}, so the push runs in this process.', err=True)
        return None


def _push_in_process(push_name: str, config_path: str) -> PushOutcome:
    from conciergent.app import App

    try:
        app = App.from_config(config_path)
    except ValueError as error:
        raise click.UsageError(str(error)) from error
    return app.push(push_name)


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
