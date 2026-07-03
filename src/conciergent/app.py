import collections.abc
import contextlib
import pathlib
import typing

import fastapi
import fastapi.responses
import uvicorn

from conciergent import i18n
from conciergent.compactor import HistorySummarizer
from conciergent.config import AppConfig, GatewaySettings, StoreSettings, build_app_config, yaml_layer
from conciergent.defaults import DEFAULTS
from conciergent.lang import Lang, parse_accept_language
from conciergent.runner import ChatRunner
from conciergent.stores.base import Store
from conciergent.stores.memory import MemoryStore
from conciergent.surfaces.base import Surface, SurfaceContext
from conciergent.surfaces.line.app import Line
from conciergent.surfaces.slack.app import Slack


class App:
    """Assemble one agent, its surfaces, and a store into a runnable webhook application.

    The assembly only speaks the ``Surface`` interface,
    a new platform plugs in as another instance without touching this class.
    """

    def __init__(
        self,
        *,
        host: str = '127.0.0.1',
        port: int = 8000,
        base_url: str = '',
        store: Store | None = None,
        surfaces: collections.abc.Sequence[Surface] = (),
        runner: ChatRunner,
        compactor: HistorySummarizer | None = None,
        gateway: GatewaySettings | None = None,
        approval_ttl_seconds: int = DEFAULTS.conversation.approval_ttl_seconds,
        history_ttl_seconds: int = DEFAULTS.conversation.history_ttl_seconds,
        oauth_wait_timeout_seconds: float = DEFAULTS.conversation.oauth_wait_timeout_seconds,
    ) -> None:
        self.host = host
        self.port = port
        self.base_url = base_url or f'http://{host}:{port}'
        self._store = store if store is not None else MemoryStore()
        self._runner = runner
        self._surfaces = list(surfaces)
        self._compactor = compactor
        self._gateway = gateway
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
        if config.locales_dir is not None:
            # Layer the app-builder's translations over the shipped catalog before any surface renders text.
            i18n.load_overrides([pathlib.Path(config.locales_dir).expanduser()])
        store = _build_store(config.store)
        redirect_uri = f'{config.server.url.rstrip("/")}/oauth/mcp/callback'
        mcp_servers = list(config.agent.mcp_servers)
        if config.gateway is not None:
            # Each embedded spec is served by this same process, so the agent dials back into itself.
            mcp_servers.extend(f'{config.server.url.rstrip("/")}/{spec.name}/mcp' for spec in config.gateway.specs)
        runner = ChatRunner(
            model=config.agent.model,
            system_prompt=config.agent.system_prompt,
            mcp_servers=mcp_servers,
            store=store,
            redirect_uri=redirect_uri,
            mcp_read_timeout_seconds=config.agent.mcp_read_timeout_seconds,
            client_name=config.agent.client_name,
        )
        compactor: HistorySummarizer | None = None
        if config.agent.input_token_limit is not None:
            compactor = HistorySummarizer(config.agent.model, input_token_limit=config.agent.input_token_limit)
        surfaces: list[Surface] = []
        if config.slack is not None:
            surfaces.append(
                Slack(
                    signing_secret=config.slack.signing_secret,
                    client_id=config.slack.client_id,
                    client_secret=config.slack.client_secret,
                    bot_token=config.slack.bot_token,
                    brand_color=config.slack.brand_color,
                    destructive_color=config.slack.destructive_color,
                    api_timeout_seconds=config.slack.api_timeout_seconds,
                )
            )
        if config.line is not None:
            surfaces.append(
                Line(
                    channel_secret=config.line.channel_secret,
                    channel_access_token=config.line.channel_access_token,
                    brand_color=config.line.brand_color,
                    destructive_color=config.line.destructive_color,
                    api_timeout_seconds=config.line.api_timeout_seconds,
                )
            )
        return cls(
            runner=runner,
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
            await self._store.prepare()
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
        async def mcp_oauth_callback(
            code: str = '', state: str = '', accept_language: str = fastapi.Header(default='')
        ) -> fastapi.responses.HTMLResponse:
            lang = parse_accept_language(accept_language)
            if not code or not state:
                return _callback_page(lang, 'callback.failed', status_code=400)
            await self._store.deliver_oauth_code(state, code)
            return _callback_page(lang, 'callback.completed')

        context = SurfaceContext(
            store=self._store,
            runner=self._runner,
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


def _callback_page(lang: Lang | None, key: str, *, status_code: int = 200) -> fastapi.responses.HTMLResponse:
    title = i18n.t(f'{key}_title', lang)
    body = i18n.t(f'{key}_body', lang)
    return fastapi.responses.HTMLResponse(f'<h1>{title}</h1><p>{body}</p>', status_code=status_code)


def _build_store(settings: StoreSettings) -> Store:
    if settings.type == 'redis':
        return _store_from_url(settings.url, max_turns=settings.max_turns)
    if settings.type == 'postgres':
        return _store_from_url(settings.url, max_turns=settings.max_turns)
    if settings.type == 'composite':
        from conciergent.stores.composite import CompositeStore

        return CompositeStore(
            messages=_store_from_url(settings.messages, max_turns=settings.max_turns),
            credentials=_store_from_url(settings.credentials, max_turns=settings.max_turns),
        )
    return MemoryStore(max_turns=settings.max_turns)


def _store_from_url(url: str, *, max_turns: int) -> Store:
    # The networked backends import lazily, so the core works without their optional extras installed.
    if url.startswith('redis'):
        try:
            from conciergent.stores.redis import RedisStore
        except ImportError as error:
            raise RuntimeError('the redis backend needs the extra: pip install conciergent[redis]') from error
        return RedisStore.from_url(url, max_turns=max_turns)
    try:
        from conciergent.stores.postgres import PostgresStore
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
