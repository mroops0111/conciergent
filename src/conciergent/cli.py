import pathlib
import shutil
import subprocess

import click

from conciergent.app import App


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
  # brand_color: "#586af2"
  # destructive_color: "#DC3545"

# line:
#   channel_secret: ${LINE_CHANNEL_SECRET}
#   channel_access_token: ${LINE_CHANNEL_ACCESS_TOKEN}

# The agent section also accepts:
#   input_token_limit: 1048576     # enables history compaction
#   mcp_read_timeout_seconds: 120

# UI text (buttons, prompts, greetings) is not set here, it lives in a locale catalog.
# To rebrand it or add a language, point at a directory of {lang}.yml files that override the shipped text,
# for example locales/zh-TW.yml with `approval: {header: 請確認}`.
# locales_dir: ./locales

# conversation:
#   approval_ttl_seconds: 600
#   history_ttl_seconds: 604800
#   oauth_wait_timeout_seconds: 240

# logger:
#   level: INFO      # DEBUG, INFO, WARNING, ERROR, CRITICAL
#   format: text     # text or json (one JSON object per line)
#   file: ./conciergent.log   # also write to this file, in addition to stderr

# Message-bearing state (history, approvals, dedupe, OAuth handoff) expires on Redis,
# while long-lived credentials (MCP and bot tokens) live in Postgres and survive restarts.
store:
  messages_url: ${CONCIERGENT_MESSAGES_URL:-redis://localhost:6379/0}
  credentials_url: ${CONCIERGENT_CREDENTIALS_URL:-postgresql+asyncpg://localhost/conciergent}
  # max_turns: 10

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
