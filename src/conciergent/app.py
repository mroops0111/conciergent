import collections.abc
import contextlib
import typing

import fastapi
import fastapi.responses
import uvicorn

from .agent import PydanticAIAgent, PydanticAICompactor
from .config import AppConfig, GatewaySettings, StoreSettings, build_app_config, yaml_layer
from .oauth_handoff import WAIT_TIMEOUT_SECONDS
from .runtime import DEFAULT_APPROVAL_TTL_SECONDS, DEFAULT_HISTORY_TTL_SECONDS, ChatAgent, HistoryCompactor
from .stores.base import Store
from .stores.memory import MemoryStore
from .surfaces.base import Surface, SurfaceContext
from .surfaces.line.app import Line
from .surfaces.slack.app import Slack


class App:
    """Assemble one agent, its surfaces, and a store into a runnable webhook application.

    The assembly only speaks the ``Surface`` interface,
    a new platform plugs in as another instance without touching this class.
    """

    def __init__(
        self,
        *,
        agent: ChatAgent,
        store: Store | None = None,
        surfaces: collections.abc.Sequence[Surface] = (),
        compactor: HistoryCompactor | None = None,
        gateway: GatewaySettings | None = None,
        host: str = '127.0.0.1',
        port: int = 8000,
        base_url: str = '',
        approval_ttl_seconds: int = DEFAULT_APPROVAL_TTL_SECONDS,
        history_ttl_seconds: int = DEFAULT_HISTORY_TTL_SECONDS,
        oauth_wait_timeout_seconds: float = WAIT_TIMEOUT_SECONDS,
    ) -> None:
        self.store = store if store is not None else MemoryStore()
        self.agent = agent
        self._surfaces = list(surfaces)
        self._compactor = compactor
        self._gateway = gateway
        self.host = host
        self.port = port
        self.base_url = base_url or f'http://{host}:{port}'
        self._approval_ttl_seconds = approval_ttl_seconds
        self._history_ttl_seconds = history_ttl_seconds
        self._oauth_wait_timeout_seconds = oauth_wait_timeout_seconds

    @classmethod
    def from_config(cls, path: str) -> 'App':
        """Build the whole application from one YAML file, the no-code path."""
        return cls.from_app_config(build_app_config(yaml_layer(path)))

    @classmethod
    def from_app_config(cls, config: AppConfig) -> 'App':
        """Build the application from a validated config, mapping each configured section to its surface."""
        store = _build_store(config.store)
        redirect_uri = f'{config.server.url.rstrip("/")}/oauth/mcp/callback'
        mcp_servers = list(config.agent.mcp_servers)
        if config.gateway is not None:
            # Each embedded spec is served by this same process, so the agent dials back into itself.
            mcp_servers.extend(f'{config.server.url.rstrip("/")}/{spec.name}/mcp' for spec in config.gateway.specs)
        approval = config.agent.approval
        agent = PydanticAIAgent(
            model=config.agent.model,
            system_prompt=config.agent.system_prompt,
            mcp_servers=mcp_servers,
            store=store,
            redirect_uri=redirect_uri,
            mcp_read_timeout_seconds=config.agent.mcp_read_timeout_seconds,
            approval_title=approval.title,
            approval_body=approval.body,
            confirm_label=approval.confirm_label,
            cancel_label=approval.cancel_label,
            confirm_prompt=approval.confirm_prompt,
            cancel_prompt=approval.cancel_prompt,
        )
        compactor = None
        if config.agent.input_token_limit is not None:
            compactor = PydanticAICompactor(config.agent.model, input_token_limit=config.agent.input_token_limit)
        surfaces: list[Surface] = []
        if config.slack is not None:
            surfaces.append(
                Slack(
                    signing_secret=config.slack.signing_secret,
                    client_id=config.slack.client_id,
                    client_secret=config.slack.client_secret,
                    scopes=config.slack.scopes,
                    bot_token=config.slack.bot_token,
                    text_formatting_instruction=config.slack.text_formatting_instruction,
                    processing_text=config.slack.processing_text,
                    authorization_title=config.slack.authorization_title,
                    authorization_link_label=config.slack.authorization_link_label,
                )
            )
        if config.line is not None:
            surfaces.append(
                Line(
                    channel_secret=config.line.channel_secret,
                    channel_access_token=config.line.channel_access_token,
                    welcome_text=config.line.welcome_text,
                    ready_text=config.line.ready_text,
                    text_formatting_instruction=config.line.text_formatting_instruction,
                    authorization_title=config.line.authorization_title,
                    authorization_link_label=config.line.authorization_link_label,
                )
            )
        return cls(
            agent=agent,
            store=store,
            surfaces=surfaces,
            compactor=compactor,
            gateway=config.gateway,
            host=config.server.host,
            port=config.server.port,
            base_url=config.server.url,
            approval_ttl_seconds=config.conversation.approval_ttl_seconds,
            history_ttl_seconds=config.conversation.history_ttl_seconds,
            oauth_wait_timeout_seconds=config.conversation.oauth_wait_timeout_seconds,
        )

    def build_asgi(self) -> fastapi.FastAPI:
        gateway = _build_gateway(self._gateway) if self._gateway is not None else None

        @contextlib.asynccontextmanager
        async def lifespan(_app: fastapi.FastAPI) -> typing.AsyncGenerator[None, None]:
            await self.store.prepare()
            async with contextlib.AsyncExitStack() as stack:
                if gateway is not None:
                    # The mounted MCP sub-apps only serve while their session managers run,
                    # which the gateway enters in its own app factory only.
                    for handle in gateway._servers:
                        await stack.enter_async_context(handle.mcp.session_manager.run())
                yield

        app = fastapi.FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
        if gateway is not None:
            gateway.mount(app)

        @app.get('/healthz')
        async def healthz() -> dict[str, typing.Any]:
            return {'status': 'ok'}

        @app.get('/oauth/mcp/callback')
        async def mcp_oauth_callback(code: str = '', state: str = '') -> fastapi.responses.HTMLResponse:
            if not code or not state:
                return fastapi.responses.HTMLResponse('<h1>Authorization failed</h1>', status_code=400)
            await self.store.deliver_oauth_code(state, code)
            return fastapi.responses.HTMLResponse('<h1>Authorized. You can close this window.</h1>')

        context = SurfaceContext(
            store=self.store,
            agent=self.agent,
            compactor=self._compactor,
            base_url=self.base_url,
            approval_ttl_seconds=self._approval_ttl_seconds,
            history_ttl_seconds=self._history_ttl_seconds,
            oauth_wait_timeout_seconds=self._oauth_wait_timeout_seconds,
        )
        for surface in self._surfaces:
            for router in surface.build_routers(context):
                app.include_router(router)

        return app

    def run(self) -> None:
        """Serve the webhook application."""
        uvicorn.run(self.build_asgi(), host=self.host, port=self.port, log_config=None)


def _build_store(settings: StoreSettings) -> Store:
    if settings.type == 'redis':
        return _store_from_url(settings.url, max_turns=settings.max_turns)
    if settings.type == 'postgres':
        return _store_from_url(settings.url, max_turns=settings.max_turns)
    if settings.type == 'composite':
        from .stores.composite import CompositeStore

        return CompositeStore(
            messages=_store_from_url(settings.messages, max_turns=settings.max_turns),
            credentials=_store_from_url(settings.credentials, max_turns=settings.max_turns),
        )
    return MemoryStore(max_turns=settings.max_turns)


def _store_from_url(url: str, *, max_turns: int) -> Store:
    # The networked backends import lazily, so the core works without their optional extras installed.
    if url.startswith('redis'):
        try:
            from .stores.redis import RedisStore
        except ImportError as error:
            raise RuntimeError('the redis backend needs the extra: pip install conciergent[redis]') from error
        return RedisStore.from_url(url, max_turns=max_turns)
    try:
        from .stores.postgres import PostgresStore
    except ImportError as error:
        raise RuntimeError('the postgres backend needs the extra: pip install conciergent[postgres]') from error
    return PostgresStore.from_url(url, max_turns=max_turns)


def _build_gateway(settings: GatewaySettings) -> typing.Any:
    # Same lazy-import rule as the store backends, the gateway is an optional extra.
    try:
        import openapi_mcp_gateway
    except ImportError as error:
        raise RuntimeError('the embedded gateway needs the extra: pip install conciergent[gateway]') from error
    gateway = openapi_mcp_gateway.Gateway()
    for spec in settings.specs:
        gateway.add_server(spec.name, spec.spec, base_url=spec.base_url)
    return gateway
