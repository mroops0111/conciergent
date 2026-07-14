import collections.abc
import typing

import sqlalchemy
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class McpToken(Base):
    __tablename__ = 'conciergent_mcp_tokens'

    server: Mapped[str] = mapped_column(sqlalchemy.String(512), primary_key=True)
    principal: Mapped[str] = mapped_column(sqlalchemy.String(255), primary_key=True)
    token: Mapped[dict[str, typing.Any]] = mapped_column(sqlalchemy.JSON)


class McpClient(Base):
    __tablename__ = 'conciergent_mcp_clients'

    server: Mapped[str] = mapped_column(sqlalchemy.String(512), primary_key=True)
    client: Mapped[dict[str, typing.Any]] = mapped_column(sqlalchemy.JSON)


class BotToken(Base):
    __tablename__ = 'conciergent_bot_tokens'

    surface: Mapped[str] = mapped_column(sqlalchemy.String(64), primary_key=True)
    tenant: Mapped[str] = mapped_column(sqlalchemy.String(255), primary_key=True)
    token: Mapped[str] = mapped_column(sqlalchemy.String(512))
    # The principal of the user who installed the app, kept so a later flow can attribute the install.
    installed_principal: Mapped[str | None] = mapped_column(sqlalchemy.String(255), nullable=True)


class UserLocale(Base):
    __tablename__ = 'conciergent_user_locales'

    principal: Mapped[str] = mapped_column(sqlalchemy.String(255), primary_key=True)
    locale: Mapped[str] = mapped_column(sqlalchemy.String(32))


class CredentialStore:
    """SQL-backed store for long-lived credentials, any SQLAlchemy async engine works.

    Holds what must survive restarts, MCP OAuth tokens and clients, per-tenant bot tokens, and each user's
    last-seen UI locale. A conversation's expiring, message-bearing state lives in ``MessageStore`` instead.
    """

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine
        self._sessions = async_sessionmaker(engine, expire_on_commit=False)

    @classmethod
    def from_url(cls, url: str) -> 'CredentialStore':
        return cls(create_async_engine(url))

    async def prepare(self) -> None:
        """Create the store's tables when they do not exist yet."""
        async with self._engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

    async def get_mcp_token(self, server: str, principal: str) -> dict[str, typing.Any] | None:
        async with self._sessions() as session:
            row = await session.get(McpToken, (server, principal))
            return dict(row.token) if row is not None else None

    async def set_mcp_token(self, server: str, principal: str, token: collections.abc.Mapping[str, typing.Any]) -> None:
        async with self._sessions.begin() as session:
            await session.merge(McpToken(server=server, principal=principal, token=dict(token)))

    async def delete_mcp_token(self, server: str, principal: str) -> None:
        async with self._sessions.begin() as session:
            await session.execute(
                sqlalchemy.delete(McpToken).where(McpToken.server == server, McpToken.principal == principal)
            )

    async def get_mcp_client(self, server: str) -> dict[str, typing.Any] | None:
        async with self._sessions() as session:
            row = await session.get(McpClient, server)
            return dict(row.client) if row is not None else None

    async def set_mcp_client(self, server: str, client: collections.abc.Mapping[str, typing.Any]) -> None:
        async with self._sessions.begin() as session:
            await session.merge(McpClient(server=server, client=dict(client)))

    async def resolve_bot_token(self, surface: str, tenant: str) -> str | None:
        async with self._sessions() as session:
            row = await session.get(BotToken, (surface, tenant))
            return row.token if row is not None else None

    async def set_bot_token(
        self, surface: str, tenant: str, token: str, *, installed_principal: str | None = None
    ) -> None:
        async with self._sessions.begin() as session:
            await session.merge(
                BotToken(surface=surface, tenant=tenant, token=token, installed_principal=installed_principal)
            )

    async def get_locale(self, principal: str) -> str | None:
        async with self._sessions() as session:
            row = await session.get(UserLocale, principal)
            return row.locale if row is not None else None

    async def set_locale(self, principal: str, locale: str) -> None:
        async with self._sessions.begin() as session:
            await session.merge(UserLocale(principal=principal, locale=locale))
