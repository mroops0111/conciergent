import collections.abc
import typing

import fastapi
import fastapi.responses
import uvicorn

from .agent import PydanticAIAgent, PydanticAICompactor
from .config import AppConfig, build_app_config, yaml_layer
from .runtime import ChatAgent, HistoryCompactor
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
        host: str = '127.0.0.1',
        port: int = 8000,
        base_url: str = '',
    ) -> None:
        self.store = store if store is not None else MemoryStore()
        self.agent = agent
        self._surfaces = list(surfaces)
        self._compactor = compactor
        self.host = host
        self.port = port
        self.base_url = base_url or f'http://{host}:{port}'

    @classmethod
    def from_config(cls, path: str) -> 'App':
        """Build the whole application from one YAML file, the no-code path."""
        return cls.from_app_config(build_app_config(yaml_layer(path)))

    @classmethod
    def from_app_config(cls, config: AppConfig) -> 'App':
        """Build the application from a validated config, mapping each configured section to its surface."""
        store = MemoryStore()
        redirect_uri = f'{config.server.url.rstrip("/")}/oauth/mcp/callback'
        agent = PydanticAIAgent(
            model=config.agent.model,
            system_prompt=config.agent.system_prompt,
            mcp_servers=list(config.agent.mcp_servers),
            store=store,
            redirect_uri=redirect_uri,
        )
        compactor = None
        if config.agent.token_limit is not None:
            compactor = PydanticAICompactor(config.agent.model, token_limit=config.agent.token_limit)
        surfaces: list[Surface] = []
        if config.slack is not None:
            surfaces.append(
                Slack(
                    signing_secret=config.slack.signing_secret,
                    client_id=config.slack.client_id,
                    client_secret=config.slack.client_secret,
                    scopes=config.slack.scopes,
                    bot_token=config.slack.bot_token,
                )
            )
        if config.line is not None:
            surfaces.append(
                Line(
                    channel_secret=config.line.channel_secret,
                    channel_access_token=config.line.channel_access_token,
                )
            )
        return cls(
            agent=agent,
            store=store,
            surfaces=surfaces,
            compactor=compactor,
            host=config.server.host,
            port=config.server.port,
            base_url=config.server.url,
        )

    def build_asgi(self) -> fastapi.FastAPI:
        app = fastapi.FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

        @app.get('/healthz')
        async def healthz() -> dict[str, typing.Any]:
            return {'status': 'ok'}

        @app.get('/oauth/mcp/callback')
        async def mcp_oauth_callback(code: str = '', state: str = '') -> fastapi.responses.HTMLResponse:
            if not code or not state:
                return fastapi.responses.HTMLResponse('<h1>Authorization failed</h1>', status_code=400)
            await self.store.deliver_oauth_code(state, code)
            return fastapi.responses.HTMLResponse('<h1>Authorized. You can close this window.</h1>')

        context = SurfaceContext(store=self.store, agent=self.agent, compactor=self._compactor, base_url=self.base_url)
        for surface in self._surfaces:
            for router in surface.build_routers(context):
                app.include_router(router)

        return app

    def run(self) -> None:
        """Serve the webhook application."""
        uvicorn.run(self.build_asgi(), host=self.host, port=self.port, log_config=None)
