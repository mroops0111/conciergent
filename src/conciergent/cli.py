import pathlib
import shutil
import subprocess

import click

from .app import App


_CONFIG_TEMPLATE = """\
agent:
  model: gemini-3-flash
  system_prompt: |
    You are a helpful assistant. Use your tools to answer the user's requests.
  mcp_servers:
    - http://localhost:8000/mcp

slack:
  signing_secret: ${SLACK_SIGNING_SECRET}
  client_id: ${SLACK_CLIENT_ID:-}
  client_secret: ${SLACK_CLIENT_SECRET:-}
  bot_token: ${SLACK_BOT_TOKEN:-}

server:
  host: 127.0.0.1
  port: 8300
"""

_MANIFEST_TEMPLATE = """\
display_information:
  name: My conciergent bot
features:
  bot_user:
    display_name: my-conciergent-bot
    always_online: true
oauth_config:
  redirect_urls:
    - {base_url}/oauth/slack/callback
  scopes:
    bot:
      - chat:write
      - im:history
      - im:read
      - im:write
      - users:read
settings:
  event_subscriptions:
    request_url: {base_url}/slack/events
    bot_events:
      - message.im
  interactivity:
    is_enabled: true
    request_url: {base_url}/slack/interactions
  org_deploy_enabled: false
  socket_mode_enabled: false
"""


@click.group()
def main() -> None:
    """Give your MCP tools a chat face."""


@main.command()
@click.option('--config', 'config_path', type=click.Path(exists=True), default='conciergent.yml')
def run(config_path: str) -> None:
    """Serve the webhook application."""
    App.from_config(config_path).run()


@main.command()
@click.option('--config', 'config_path', type=click.Path(exists=True), default='conciergent.yml')
def dev(config_path: str) -> None:
    """Serve locally and open a public tunnel so Slack can reach the webhooks."""
    app = App.from_config(config_path)
    tunnel = _start_tunnel(app.port)
    if tunnel is None:
        click.echo('cloudflared not found. Expose the port yourself, for example:')
        click.echo(f'  cloudflared tunnel --url http://localhost:{app.port}')
        click.echo('Then paste <tunnel-url>/slack/events into your Slack app settings.')
    try:
        app.run()
    finally:
        if tunnel is not None:
            tunnel.terminate()


@main.command()
@click.option('--path', type=click.Path(), default='.')
def init(path: str) -> None:
    """Scaffold a config file and a Slack app manifest into the target directory."""
    target = pathlib.Path(path)
    target.mkdir(parents=True, exist_ok=True)
    config_file = target / 'conciergent.yml'
    manifest_file = target / 'slack-app-manifest.yml'
    if config_file.exists() or manifest_file.exists():
        raise click.UsageError(f'{config_file} or {manifest_file} already exists.')
    config_file.write_text(_CONFIG_TEMPLATE)
    manifest_file.write_text(_MANIFEST_TEMPLATE.format(base_url='https://YOUR-PUBLIC-URL'))
    click.echo(f'Wrote {config_file} and {manifest_file}.')
    click.echo('Create a Slack app from the manifest, fill in the env vars, then: conciergent dev')


def _start_tunnel(port: int) -> subprocess.Popen[bytes] | None:
    binary = shutil.which('cloudflared')
    if binary is None:
        return None
    # cloudflared prints the public trycloudflare URL to its own stderr, so the streams stay inherited.
    process = subprocess.Popen([binary, 'tunnel', '--url', f'http://localhost:{port}'])
    click.echo('cloudflared tunnel starting, watch its output for the public URL.')
    click.echo('Paste <tunnel-url>/slack/events and /slack/interactions into your Slack app settings.')
    return process


if __name__ == '__main__':
    main()
