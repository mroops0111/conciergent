# conciergent

> Give your MCP tools a chat face — Slack, LINE and more — with in-chat OAuth, human-in-the-loop approval, and surface-agnostic rich replies.

**conciergent** (concierge + agent) is the front-of-house layer for LLM agents. Point it at any [Model Context Protocol](https://modelcontextprotocol.io) server and it becomes a chatbot your users can talk to on the messengers they already use — handling the unglamorous parts for you: in-chat OAuth handoff, approval gates for destructive tools, multi-tenant install, and a structured reply model that renders natively on each surface.

> **Status: Planning / pre-alpha.** The public API does not exist yet. This repository is being bootstrapped from an existing internal implementation. Not usable yet — watch this space.

## Why

Building a chatbot that drives real tools means re-solving the same problems every time: receiving and verifying webhooks, the OAuth dance *inside* a chat thread, asking the user before doing something destructive, rendering the same reply as Slack Block Kit vs LINE Flex, surviving across restarts. Agent frameworks (Pydantic AI, LangChain) don't do the chat-surface side; chat SDKs (Bolt, Botkit) don't do MCP, OAuth-per-user, or HITL. conciergent is the missing middle.

## The ecosystem

conciergent is the **front desk**. Its sister project, [**openapi-mcp-gateway**](https://github.com/mroops0111/openapi-mcp-gateway), is the **loading dock**:

```
any REST API ──▶ openapi-mcp-gateway ──▶ MCP tools ──▶ conciergent ──▶ Slack / LINE / …
                 (turn APIs into tools)              (give tools a chat face)
```

Bring an OpenAPI spec and chat credentials, get a chatbot that can actually *do* things.

## Design at a glance

- **Surface-agnostic reply model** — your agent emits a semantic `Card` / `Carousel` / `Suggestion`; each surface renders it natively. Custom layouts via a renderer override.
- **Batteries-included agent** — Pydantic AI baked in, type-safe, zero agent-internals knowledge required. Bring a system prompt and an MCP server.
- **In-chat OAuth** — the per-user authorization URL is pushed into the conversation; the code is awaited and stored, with refresh.
- **Human-in-the-loop** — tools flagged destructive gate behind an in-chat confirm/cancel before they run.
- **Pluggable state** — in-memory by default; Redis / Postgres backends optional.

## License

MIT © mroops0111
