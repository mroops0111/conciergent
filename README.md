# Conciergent

[![CI](https://github.com/mroops0111/conciergent/actions/workflows/ci.yml/badge.svg)](https://github.com/mroops0111/conciergent/actions/workflows/ci.yml)
[![Python Version](https://img.shields.io/badge/python-3.12%2B-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Give your [MCP](https://modelcontextprotocol.io/) tools a chat face. Connect Conciergent to any Model Context Protocol server and it becomes a Slack, LINE, or Discord bot that can actually *do* things, with per-user OAuth handled inside the conversation, an approval gate before destructive tools run, and one structured reply that renders natively on every surface.

Conciergent pairs with its sister project [openapi-mcp-gateway](https://github.com/mroops0111/openapi-mcp-gateway), which turns any REST API into MCP tools. Together they take one or more OpenAPI specs all the way to a chatbot your users can talk to.

<p align="center">
  <img src="architecture.png" alt="Conciergent architecture, layered top to bottom. Chat surfaces (Slack, LINE, Discord, and more) sit on top. An incoming message flows down into a surface- and agent-agnostic runtime that produces one structured reply (plain text, a Card, or a Carousel). The runtime hands each turn to an AI agent powered by Pydantic AI, which resolves to a normal reply, an in-chat OAuth authorization, or a human-in-the-loop confirmation. The agent calls MCP tools (an OpenAPI spec via the embedded openapi-mcp-gateway, or any MCP server) and stores messages in Redis and credentials in Postgres. The reply flows back up to each surface." width="600">
</p>

- **Any MCP Server, or an OpenAPI Spec Directly.** Point Conciergent at an MCP URL, or set `gateway.enabled` and drop in a spec. It embeds openapi-mcp-gateway in-process, no second server to run.
- **In-Chat OAuth.** When a tool needs authorization, Conciergent shows the link in the chat, then stores and refreshes the token. The user never leaves the conversation, and can ask to sign out at any time to revoke it.
- **Human-in-the-Loop.** Any tool the server marks destructive pauses behind a Confirm / Cancel card before it runs.
- **Surface-Agnostic Rich Replies.** The agent emits one structured reply, and each surface renders it natively.

## Quick Start

Two things are yours to set up once. Register a chat app with a public webhook URL, and have a Redis and a Postgres to run against (the [Docker](#4-run) path below provides both). Everything else is `${ENV_VAR}` in one YAML file.

### 1. Install and Scaffold

```bash
uv add conciergent
uv run conciergent init
```

`uv run conciergent init` writes an annotated `manifest.yml`, deep-merged over the shipped defaults so you set only what you change.

### 2. Configure Your MCP Tools

Conciergent reaches your tools two ways, and you can use both at once.

#### Connect an MCP Server

List any MCP server URL under `agent.mcp_servers`. The scaffolded `manifest.yml` already has the surface and store set up around it.

```yaml
# manifest.yml
agent:
  model: openai:gpt-4o-mini
  system_prompt: |
    You are a helpful assistant. Use your tools to answer the user's requests.
  mcp_servers:
    - http://localhost:9000/mcp
    - https://another-server.example.com/mcp
```

If a server uses OAuth, Conciergent runs the in-chat authorization handoff the first time a tool needs it, with no extra config.

#### Or Embed an OpenAPI Spec

Add the `gateway` extra and let Conciergent embed openapi-mcp-gateway in-process, so a spec becomes MCP tools with no second server to run.

```bash
uv add "conciergent[gateway]"
```

```yaml
gateway:
  enabled: true
  specs:
    - name: petstore
      spec: https://petstore3.swagger.io/api/v3/openapi.json
      base_url: https://petstore3.swagger.io/api/v3
    - name: internal
      spec: ./internal-api.json
```

Each spec is served at `/{name}/mcp` and wired into the agent for you, alongside anything already in `agent.mcp_servers`. A complete runnable config lives at [`examples/openapi-chat.yml`](examples/openapi-chat.yml).

A spec entry mirrors openapi-mcp-gateway's per-server config, so you can add `exposure: dynamic` for a large spec (the agent sees three meta-tools instead of one per endpoint), a `policy` filter, or `auth` (`bearer`, `api_key`, or `oauth2`). An `oauth2` spec runs the same in-chat OAuth handoff, so each user authorizes their own account before its tools run.

### 3. Connect Your Chat App

Conciergent replies in direct messages, and the in-chat OAuth happens there too. Group chats are opt-in, see [Group Chats](#group-chats). Register the app once and set its request URLs, where `{your-public-url}` is your public host.

<details>
<summary><b>Slack</b></summary>

Create the app from [`examples/manifest-slack.yml`](examples/manifest-slack.yml), which fills these in for you.

| Setting | URL |
|---|---|
| Event Subscriptions Request URL | `https://{your-public-url}/slack/events` |
| Interactivity Request URL | `https://{your-public-url}/slack/interactions` |
| OAuth Redirect URL *(multi-workspace install only)* | `https://{your-public-url}/oauth/slack/callback` |

</details>

<details>
<summary><b>LINE</b></summary>

In the [LINE Developers console](https://developers.line.biz/console/):

1. Create a **provider**, then a **Messaging API channel** under it.
2. Copy the **Channel secret** (Basic settings) and issue a long-lived **Channel access token** (Messaging API tab) into `LINE_CHANNEL_SECRET` and `LINE_CHANNEL_ACCESS_TOKEN`.
3. Set the webhook URL and turn **Use webhook** on:
   - Messaging API Webhook URL: `https://{your-public-url}/line/events`
4. In the [LINE Official Account Manager](https://manager.line.biz/), turn **auto-reply** and **greeting messages** off, so the bot owns every reply.

Conciergent answers with the event's one-time reply token when it can and falls back to a push message otherwise, so the channel access token needs push messages enabled.

</details>

<details>
<summary><b>Discord</b></summary>

In the [Discord Developer Portal](https://discord.com/developers/applications):

1. Create an **application**, add a **Bot**, and copy its **token** into `DISCORD_BOT_TOKEN`.
2. Enable the bot's **Direct Messages** intent. It is non-privileged, and direct-message content is exempt from the message-content intent.
3. Invite the bot to a server you also belong to, so it can open a direct-message channel with you.

Discord delivers direct messages over a gateway connection the bot holds open, so there is no webhook URL to register. It connects on startup and replies in direct messages.

</details>

The MCP OAuth return (`/oauth/mcp/callback`) is registered with the MCP server automatically, so it is not something you set in a dashboard. For local development, run a tunnel (cloudflared / ngrok) in front of the port and use its URL as `{your-public-url}` and as `server.url`.

### 4. Run

Two ways, depending on whether you already have Redis and Postgres.

Against your own Redis and Postgres:

```bash
createdb conciergent      # the database must exist, and Conciergent will create its tables on first run
uv run conciergent run
```

Or with Docker, which brings up Redis, Postgres, and the app together and needs no uv:

```bash
cp examples/openapi-chat.yml manifest.yml     # or use your own
docker compose up
```

Secrets stay in the environment. `manifest.yml` reads the Slack, LINE, Discord, and provider credentials through `${...}`, so nothing sensitive is committed. The app serves on port 8000, so put your tunnel in front of it and set `server.url` to the tunnel URL.

## Configuration

Conciergent reads one `manifest.yml`, merged over the shipped defaults, so you set only what you change. `${VAR}` / `${VAR:-default}` resolve in any string field.

Three model providers ship in the box. Set `agent.model` to a `provider:model` string and export that provider's API key.

| Provider | `agent.model` | API key |
|---|---|---|
| OpenAI | `openai:<model>` | `OPENAI_API_KEY` |
| Google Gemini | `google:<model>` | `GOOGLE_API_KEY` |
| Anthropic Claude | `anthropic:<model>` | `ANTHROPIC_API_KEY` |

The shipped default is `openai:gpt-4o-mini`. Any model the provider offers works, so pick the current one from its docs.

<details>
<summary><b>Config Reference</b></summary>

| Field | Default | Description |
|---|---|---|
| `server.host` | `127.0.0.1` | Bind address. Use `0.0.0.0` to accept connections from other hosts. |
| `server.port` | `8000` | Bind port. |
| `server.url` | *(from host/port)* | Public URL external services reach. Set this behind a tunnel or proxy. |
| `agent.model` | `openai:gpt-4o-mini` | A `provider:model` string for one of the three providers above. |
| `agent.system_prompt` | *(generic assistant)* | Your assistant's instructions. |
| `agent.mcp_servers` | `[]` | MCP server URLs the agent connects to. |
| `agent.input_token_limit` | `null` | Overrides the context window used for history compaction. Unset auto-detects it per model. |
| `agent.mcp_read_timeout_seconds` | `300` | Per-call MCP read timeout. Must exceed `conversation.oauth_wait_timeout_seconds`, since a missing token runs OAuth inside the connect. |
| `agent.mcp_probe_timeout_seconds` | `10` | Group chats only. How long the no-credential probe of an MCP server waits. |
| `agent.mcp_probe_retry_seconds` | `60` | Group chats only. When an unreachable MCP server is probed again. |
| `agent.client_name` | `conciergent` | Name shown on the MCP OAuth screen. |
| `surface.slack.enabled` | `false` | Turn the Slack surface on. |
| `surface.slack.signing_secret` | *(required if enabled)* | Verifies inbound Slack signatures. |
| `surface.slack.bot_token` | *(empty)* | Single-workspace bot token. |
| `surface.slack.client_id` · `client_secret` | *(empty)* | Set both for the multi-workspace install flow. |
| `surface.slack.brand_color` · `destructive_color` | `#586af2` · `#DC3545` | Card accent colors. |
| `surface.line.enabled` | `false` | Turn the LINE surface on. |
| `surface.line.channel_secret` · `channel_access_token` | *(required if enabled)* | LINE Messaging API credentials. |
| `surface.discord.enabled` | `false` | Turn the Discord surface on. |
| `surface.discord.bot_token` | *(required if enabled)* | Discord bot token. The bot connects over the gateway, so no webhook URL is needed. |
| `surface.<name>.groups.enabled` | `false` | Answer in group chats as well as direct messages. See [Group Chats](#group-chats). |
| `surface.<name>.groups.allowed` | `[]` | Group ids the bot answers in. A LINE groupId or roomId, a Slack channel id, or a Discord channel or server id. |
| `surface.<name>.groups.reply_to` | `mention` | `mention` answers only messages that mention the bot. `all` answers every message in an allowed group. |
| `store.messages_url` | *(required)* | Redis URL. Holds message state that expires (history, approvals, dedupe, OAuth handoff). |
| `store.credentials_url` | *(required)* | Postgres URL (any SQLAlchemy async engine). Holds credentials that survive a restart (MCP and bot tokens). |
| `store.max_turns` | `10` | Recent turns kept in history. |
| `gateway.enabled` | `false` | Embed openapi-mcp-gateway in-process. |
| `gateway.specs` | `[]` | `{name, spec, base_url}` entries, each mounted at `/{name}/mcp`. |
| `conversation.approval_ttl_seconds` | `600` | How long a pending approval waits. |
| `conversation.history_ttl_seconds` | `604800` | History retention (one week). |
| `conversation.oauth_wait_timeout_seconds` | `240` | How long an in-chat OAuth handoff blocks. |
| `logger.level` · `format` · `file` | `INFO` · `text` · *(none)* | Logging. `format` is `text` or `json`. |
| `locales_dir` | `null` | Directory of `{lang}.yml` files overriding shipped UI text. |

</details>

### Localizing Text

Button labels, prompts, and greetings are not config. They live in a locale catalog, picked from each user's Slack, LINE, or Discord language. Set `locales_dir` to a directory of `{lang}.yml` files to rebrand or translate. [`examples/locales/en.yml`](examples/locales/en.yml) is the full English catalog to start from.

## Group Chats

Each surface can also answer in group chats, a LINE group, a Slack channel, or a Discord server channel. It is off by default and only serves the groups you allow.

### Requirement

A group is shared by several people, so it never runs a per-user OAuth. Group chats are served only when every MCP tool works without a user's token.

- **Embedded Gateway Specs**: a spec with no `auth`, a static `bearer` or `api_key`, or an `oauth2` spec with `flow: client_credentials` works in groups. Any other `oauth2` spec fails config validation while groups are on.
- **Other MCP Servers**: Conciergent sends each one an unauthenticated `initialize` at startup. A `401` or `403` means it needs a user's token, which turns group chats off and logs an error. An unreachable server pauses groups, and is probed again after `agent.mcp_probe_retry_seconds`.

### Configuration

Turn groups on per surface and list the group ids to answer in.

```yaml
surface:
  line:
    enabled: true
    channel_secret: ${LINE_CHANNEL_SECRET}
    channel_access_token: ${LINE_CHANNEL_ACCESS_TOKEN}
    groups:
      enabled: true
      allowed:
        - C0123456789abcdef0123456789abcdef
      reply_to: mention # or all, to answer every message in the group
```

### Behavior

A group shares one conversation while every member keeps their own identity.

- **Shared History**: everyone in the group, or in the Slack thread, shares one history. Each message reaches the agent prefixed with its speaker's name, so it can tell the members apart.
- **Mentions**: with `reply_to: mention` a typed message starts a turn only when it mentions the bot, and the mention is removed before the agent sees it. Tapping a suggestion or a Confirm / Cancel button never needs a mention.
- **Approvals**: a confirmation belongs to the member whose request parked it. Only they can confirm or cancel it, and other members' messages leave it waiting. Another member's tap on it is ignored on every surface.
- **Addressed Replies**: a text reply to a typed message shows who it answers, as a quote on LINE, a native reply on Discord, and a mention on Slack. Cards and replies to a button tap are not marked, since LINE cannot quote from a card and a tap has no message to answer.
- **No Authorization**: a group turn never shows an authorize link and hides the sign-out tool, since a sign-out would clear the whole group's history.

### Surface Setup

Each platform needs a little more setup before the bot can read a group.

<details>
<summary><b>LINE</b></summary>

- **Joining Groups**: turn on **Allow bot to join group chats** in the LINE Official Account Manager or the channel's Messaging API settings, then invite the bot.
- **Group Ids**: Conciergent logs the groupId (or roomId) when the bot joins a group, even while groups are off, so you can copy it into `allowed`.
- **Push Quota**: a reply past the free reply token is a push message, and a push to a group counts once per member against the monthly message quota.

</details>

<details>
<summary><b>Slack</b></summary>

- **Mention Mode**: subscribe to the `app_mention` bot event and add the `app_mentions:read` scope.
- **All Mode**: subscribe to `message.channels`, `message.groups`, and `message.mpim`, and add `channels:history`, `groups:history`, and `mpim:history`.
- **Threads**: the bot replies in a thread, and each thread is its own conversation. Invite the bot to the channel, and reinstall the app after adding scopes. The multi-workspace install flow requests these scopes for you.

</details>

<details>
<summary><b>Discord</b></summary>

- **Allowlist**: list a channel id, or a server id to cover every channel and thread in that server.
- **Mention Mode**: needs no extra setup. Discord delivers a server message's content to a bot it mentions, even without the message-content intent.
- **All Mode**: enable the privileged **Message Content Intent** on the bot in the Developer Portal. If Discord refuses it, the bot logs an error and answers mentions only, while direct messages keep working.

</details>

## The Reply Model

The agent never speaks Slack, LINE, or Discord. It emits one of three shapes, and each surface renders it natively.

- **`str`** for plain text.
- **`Card`** for a header, up to six text sections, an optional hero image, a footnote, up to five link buttons, and up to three suggestion quick-replies.
- **`Carousel`** for one to four option cards the user picks between, plus a fallback card.

A suggestion is the interactive primitive. Tapping one posts its prompt back to the agent as if the user had typed it. The field descriptions on these models are the agent's structured-output schema, so the model fills them in directly.

## Extending

The paved road above needs no code. These are for teams who want to go further.

<details>
<summary><b>Add a Surface</b></summary>

A surface is one platform's whole contribution, behind a one-method contract. The app only ever speaks this interface, so a new platform is a new implementation passed to the app and nothing in the core changes.

```python
class Surface(abc.ABC):
    @abc.abstractmethod
    def build_routers(self, context: SurfaceContext) -> list[fastapi.APIRouter]:
        """Return the webhook and auxiliary routes this platform needs."""
```

Implement `Surface`, a `ReplySurface` to render the reply model, and (if it has per-user auth) an `OAuthBridge`, then pass an instance to the app. Slack, LINE, and Discord are the three that ship.

</details>

<details>
<summary><b>Use the Python API</b></summary>

`App.from_config` is the YAML path. For full control, assemble `App` directly, which is the same object the CLI builds. `App.build_asgi()` returns the FastAPI app if you would rather bring your own server.

```python
from conciergent import App, MessageStore, CredentialStore
from conciergent.agent.runner import ChatRunner
from conciergent.surfaces.slack.app import Slack

message_store = MessageStore.from_url('redis://localhost:6379/0')
credential_store = CredentialStore.from_url('postgresql+asyncpg://localhost/conciergent')

app = App(
    runner=ChatRunner(
        model='openai:gpt-4o-mini',
        system_prompt='You are a helpful assistant.',
        mcp_servers=['http://localhost:9000/mcp'],
        credential_store=credential_store,
        redirect_uri='https://your-public-url/oauth/mcp/callback',
    ),
    surfaces=[Slack(signing_secret='...', bot_token='xoxb-...')],
    message_store=message_store,
    credential_store=credential_store,
    base_url='https://your-public-url',
)
app.run()
```

</details>

## License

[MIT](LICENSE)
