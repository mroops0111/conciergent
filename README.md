# Conciergent

[![CI](https://github.com/mroops0111/conciergent/actions/workflows/ci.yml/badge.svg)](https://github.com/mroops0111/conciergent/actions/workflows/ci.yml)
[![Python Version](https://img.shields.io/badge/python-3.12%2B-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Give your [MCP](https://modelcontextprotocol.io/) tools a chat face. Connect Conciergent to any Model Context Protocol server and it becomes a Slack or LINE bot that can actually *do* things, with per-user OAuth handled inside the conversation, an approval gate before destructive tools run, and one structured reply that renders natively on every surface.

Conciergent pairs with its sister project [openapi-mcp-gateway](https://github.com/mroops0111/openapi-mcp-gateway), which turns any REST API into MCP tools. Together they take one or more OpenAPI specs all the way to a chatbot your users can talk to.

<p align="center">
  <img src="architecture.png" alt="Conciergent architecture, layered top to bottom. Chat surfaces (Slack, LINE, and more) sit on top. An incoming message flows down into a surface- and agent-agnostic runtime that produces one structured reply (plain text, a Card, or a Carousel). The runtime hands each turn to an AI agent powered by Pydantic AI, which resolves to a normal reply, an in-chat OAuth authorization, or a human-in-the-loop confirmation. The agent calls MCP tools (an OpenAPI spec via the embedded openapi-mcp-gateway, or any MCP server) and stores messages in Redis and credentials in Postgres. The reply flows back up to each surface." width="820">
</p>

- **Any MCP Server, or an OpenAPI Spec Directly.** Give Conciergent an MCP URL, or set `gateway.enabled` and drop in an OpenAPI spec. It embeds openapi-mcp-gateway in-process and serves the tools itself, no second server to run.
- **In-Chat OAuth.** When a tool needs the user to authorize, Conciergent pushes the authorize URL into the thread, waits for the callback, then stores and refreshes the token. The user never leaves the chat.
- **Human-in-the-Loop.** Any tool the MCP server marks destructive pauses mid-run behind a Confirm / Cancel card.
- **Surface-Agnostic Rich Replies.** The agent emits one semantic reply, and each surface renders it in its own native format.

## Quick Start

Two things are yours to set up once. Register a chat app with a public webhook URL, and have a Redis and a Postgres to run against (the [Docker](#4-run) path below provides both). Everything else is `${ENV_VAR}` in one YAML file.

### 1. Install and Scaffold

```bash
uv add conciergent
uv run conciergent init
```

`uv run conciergent init` writes an annotated `conciergent.yml`. It is deep-merged over the shipped defaults, so you set only what you change, and `${VAR}` / `${VAR:-default}` resolve in any string field.

### 2. Configure Your MCP Tools

Conciergent reaches your tools two ways, and you can use both at once.

#### Connect an MCP Server

List any MCP server URL under `agent.mcp_servers`. The scaffolded `conciergent.yml` already has the surface and store set up around it.

```yaml
# conciergent.yml
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

Conciergent replies in direct messages, and the in-chat OAuth happens there too. Register the app once and set its request URLs, where `{your-public-url}` is your public host.

<details>
<summary><b>Slack</b></summary>

Create the app from [`examples/slack-app-manifest.yml`](examples/slack-app-manifest.yml), which fills these in for you.

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
cp examples/openapi-chat.yml conciergent.yml     # or use your own
docker compose up
```

Secrets stay in the environment, not in the file. `conciergent.yml` pulls the Slack and LINE credentials in through `${...}` so they are never committed, and your model provider's API key is read from the environment too (`OPENAI_API_KEY`, `GOOGLE_API_KEY`, or `ANTHROPIC_API_KEY`). The app serves on port 8000. Put your tunnel in front of that port and set `server.url` to its URL.

## Configuration

Conciergent reads one `conciergent.yml`, merged over the shipped defaults, so you set only what you change. `${VAR}` / `${VAR:-default}` resolve in any string field.

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
| `agent.mcp_read_timeout_seconds` | `120` | Per-call MCP read timeout. |
| `agent.client_name` | `conciergent` | Name shown on the MCP OAuth screen. |
| `surface.slack.enabled` | `false` | Turn the Slack surface on. |
| `surface.slack.signing_secret` | *(required if enabled)* | Verifies inbound Slack signatures. |
| `surface.slack.bot_token` | *(empty)* | Single-workspace bot token. |
| `surface.slack.client_id` · `client_secret` | *(empty)* | Set both for the multi-workspace install flow. |
| `surface.slack.brand_color` · `destructive_color` | `#586af2` · `#DC3545` | Card accent colors. |
| `surface.line.enabled` | `false` | Turn the LINE surface on. |
| `surface.line.channel_secret` · `channel_access_token` | *(required if enabled)* | LINE Messaging API credentials. |
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

Button labels, prompts, and greetings are not config. They live in a locale catalog, picked from each user's Slack or LINE language. Set `locales_dir` to a directory of `{lang}.yml` files to rebrand or translate. [`examples/locales/en.yml`](examples/locales/en.yml) is the full English catalog to start from.

## The Reply Model

The agent never speaks Slack or LINE. It emits one of three shapes, and each surface renders it natively.

- **`str`** for plain text.
- **`Card`** for a header, up to six text sections, an optional hero image, a footnote, up to five link buttons, and up to three suggestion quick-replies.
- **`Carousel`** for one to four option cards the user picks between, plus a fallback card.

A suggestion is the interactive primitive. Tapping one posts its prompt back to the agent as if the user had typed it. The field descriptions on these models are the agent's structured-output schema, so the model fills them in directly.

## Extention

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

Implement `Surface`, a `ReplySurface` to render the reply model, and (if it has per-user auth) an `OAuthBridge`, then pass an instance to the app. Slack and LINE are just the two that ship.

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
