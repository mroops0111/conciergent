import collections.abc
import contextlib
import pathlib
import typing

import fastapi
import fastapi.responses
import uvicorn

from conciergent import i18n, logger
from conciergent.agent.compactor import HistorySummarizer
from conciergent.agent.runner import ChatRunner
from conciergent.config import AppConfig, GatewaySettings, LoggerSettings, build_app_config, yaml_layer
from conciergent.defaults import DEFAULTS
from conciergent.i18n.lang import Lang, parse_accept_language
from conciergent.store.credential import CredentialStore
from conciergent.store.message import MessageStore
from conciergent.surfaces.base import Surface, SurfaceContext


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
        message_store: MessageStore,
        credential_store: CredentialStore,
        surfaces: collections.abc.Sequence[Surface] = (),
        runner: ChatRunner,
        compactor: HistorySummarizer | None = None,
        gateway_settings: GatewaySettings | None = None,
        logger_settings: LoggerSettings | None = None,
        approval_ttl_seconds: int = DEFAULTS.conversation.approval_ttl_seconds,
        history_ttl_seconds: int = DEFAULTS.conversation.history_ttl_seconds,
        oauth_wait_timeout_seconds: float = DEFAULTS.conversation.oauth_wait_timeout_seconds,
    ) -> None:
        self.host = host
        self.port = port
        self.base_url = base_url or f'http://{host}:{port}'
        self._message_store = message_store
        self._credential_store = credential_store
        self._runner = runner
        self._surfaces = list(surfaces)
        self._compactor = compactor
        self._gateway_settings = gateway_settings
        self._logger_settings = logger_settings
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
        message_store = MessageStore.from_url(config.store.messages_url, max_turns=config.store.max_turns)
        credential_store = CredentialStore.from_url(config.store.credentials_url)
        redirect_uri = f'{config.server.url.rstrip("/")}/oauth/mcp/callback'
        mcp_servers = list(config.agent.mcp_servers)
        if config.gateway.enabled:
            # Each embedded spec is served by this same process, so the agent dials back into itself.
            mcp_servers.extend(f'{config.server.url.rstrip("/")}/{spec.name}/mcp' for spec in config.gateway.specs)
        runner = ChatRunner(
            model=config.agent.model,
            system_prompt=config.agent.system_prompt,
            mcp_servers=mcp_servers,
            credential_store=credential_store,
            redirect_uri=redirect_uri,
            mcp_read_timeout_seconds=config.agent.mcp_read_timeout_seconds,
            client_name=config.agent.client_name,
        )
        compactor: HistorySummarizer | None = None
        if config.agent.input_token_limit is not None:
            compactor = HistorySummarizer(config.agent.model, input_token_limit=config.agent.input_token_limit)
        surfaces = config.surface.enabled_surfaces()
        if not surfaces:
            raise ValueError('enable at least one surface: set surface.slack.enabled or surface.line.enabled')
        return cls(
            runner=runner,
            message_store=message_store,
            credential_store=credential_store,
            surfaces=surfaces,
            compactor=compactor,
            gateway_settings=config.gateway,
            logger_settings=config.logger,
            host=config.server.host,
            port=config.server.port,
            base_url=config.server.url,
            approval_ttl_seconds=config.conversation.approval_ttl_seconds,
            history_ttl_seconds=config.conversation.history_ttl_seconds,
            oauth_wait_timeout_seconds=config.conversation.oauth_wait_timeout_seconds,
        )

    def build_asgi(self) -> fastapi.FastAPI:
        gateway = None
        if self._gateway_settings is not None and self._gateway_settings.enabled:
            gateway = _build_gateway(self._gateway_settings)

        @contextlib.asynccontextmanager
        async def lifespan(_app: fastapi.FastAPI) -> typing.AsyncGenerator[None, None]:
            await self._credential_store.prepare()
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
        async def healthz() -> fastapi.Response:
            return fastapi.Response(status_code=fastapi.status.HTTP_204_NO_CONTENT)

        @app.get('/oauth/mcp/callback')
        async def mcp_oauth_callback(
            code: str = '', state: str = '', accept_language: str = fastapi.Header(default='')
        ) -> fastapi.responses.HTMLResponse:
            lang = parse_accept_language(accept_language)
            if not code or not state:
                return _callback_page(lang, 'callback.failed', status_code=400)
            await self._message_store.deliver_oauth_code(state, code)
            return _callback_page(lang, 'callback.completed')

        context = SurfaceContext(
            message_store=self._message_store,
            credential_store=self._credential_store,
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
        if self._logger_settings is not None:
            # Configure process-wide logging once, before uvicorn brings up its own loggers.
            logger.setup(
                level=self._logger_settings.level,
                format=self._logger_settings.format,
                file=self._logger_settings.file,
            )
        uvicorn.run(self.build_asgi(), host=self.host, port=self.port, log_config=None)


def _callback_page(lang: Lang | None, key: str, *, status_code: int = 200) -> fastapi.responses.HTMLResponse:
    title = i18n.t(f'{key}.title', lang)
    body = i18n.t(f'{key}.body', lang)
    return fastapi.responses.HTMLResponse(f'<h1>{title}</h1><p>{body}</p>', status_code=status_code)


def _build_gateway(settings: GatewaySettings) -> typing.Any:
    try:
        import openapi_mcp_gateway
    except ImportError as error:
        raise RuntimeError('the embedded gateway needs the extra: uv install conciergent[gateway]') from error
    gateway = openapi_mcp_gateway.Gateway()
    for spec in settings.specs:
        gateway.add_server(spec.name, spec.spec, base_url=spec.base_url)
    return gateway
