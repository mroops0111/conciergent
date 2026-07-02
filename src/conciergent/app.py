import typing

import fastapi
import fastapi.responses
import uvicorn

from .agent import PydanticAIAgent, PydanticAICompactor
from .config import AppConfig, SlackSettings, build_app_config, yaml_layer
from .runtime import ChatAgent, HistoryCompactor
from .stores.base import Store
from .stores.memory import MemoryStore
from .surfaces.slack.install import SlackInstallSettings, build_install_router
from .surfaces.slack.webhook import SlackWebhookSettings
from .surfaces.slack.webhook import build_router as build_slack_router


class App:
    """Assemble one agent, its surfaces, and a store into a runnable webhook application."""

    def __init__(
        self,
        *,
        agent: ChatAgent,
        store: Store | None = None,
        slack: SlackSettings | None = None,
        compactor: HistoryCompactor | None = None,
        host: str = '127.0.0.1',
        port: int = 8000,
        base_url: str = '',
    ) -> None:
        self.store = store if store is not None else MemoryStore()
        self.agent = agent
        self._slack = slack
        self._compactor = compactor
        self.host = host
        self.port = port
        self.base_url = base_url or f'http://{host}:{port}'

    @classmethod
    def from_config(cls, path: str) -> 'App':
        """Build the whole application from one YAML file, the no-code path."""
        config = build_app_config(yaml_layer(path))
        return cls.from_app_config(config)

    @classmethod
    def from_app_config(cls, config: AppConfig) -> 'App':
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
        return cls(
            agent=agent,
            store=store,
            slack=config.slack,
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

        if self._slack is not None:
            app.include_router(
                build_slack_router(
                    settings=SlackWebhookSettings(
                        signing_secret=self._slack.signing_secret,
                        fallback_bot_token=self._slack.bot_token,
                    ),
                    store=self.store,
                    agent=self.agent,
                    compactor=self._compactor,
                )
            )
            if self._slack.client_id:
                app.include_router(
                    build_install_router(
                        settings=SlackInstallSettings(
                            client_id=self._slack.client_id,
                            client_secret=self._slack.client_secret,
                            scopes=tuple(self._slack.scopes),
                            base_url=self.base_url,
                        ),
                        store=self.store,
                    )
                )

        return app

    def run(self) -> None:
        """Serve the webhook application."""
        uvicorn.run(self.build_asgi(), host=self.host, port=self.port, log_config=None)
