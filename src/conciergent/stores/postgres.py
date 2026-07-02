import asyncio
import collections.abc
import time
import typing

import sqlalchemy
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from .base import Store


_DEFAULT_MAX_TURNS = 10
_OAUTH_CODE_TTL_SECONDS = 300.0
_OAUTH_POLL_INTERVAL_SECONDS = 0.2


class Base(DeclarativeBase):
    pass


class HistoryTurn(Base):
    __tablename__ = 'conciergent_history_turns'

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    principal: Mapped[str] = mapped_column(sqlalchemy.String(255), index=True)
    messages: Mapped[list[typing.Any]] = mapped_column(sqlalchemy.JSON)
    expires_at: Mapped[float] = mapped_column(sqlalchemy.Float)


class Approval(Base):
    __tablename__ = 'conciergent_approvals'

    principal: Mapped[str] = mapped_column(sqlalchemy.String(255), primary_key=True)
    state: Mapped[dict[str, typing.Any]] = mapped_column(sqlalchemy.JSON)
    expires_at: Mapped[float] = mapped_column(sqlalchemy.Float)


class DedupeKey(Base):
    __tablename__ = 'conciergent_dedupe_keys'

    key: Mapped[str] = mapped_column(sqlalchemy.String(512), primary_key=True)
    expires_at: Mapped[float] = mapped_column(sqlalchemy.Float)


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


class OAuthCode(Base):
    __tablename__ = 'conciergent_oauth_codes'

    state: Mapped[str] = mapped_column(sqlalchemy.String(512), primary_key=True)
    code: Mapped[str] = mapped_column(sqlalchemy.String(512))
    expires_at: Mapped[float] = mapped_column(sqlalchemy.Float)


class PostgresStore(Store):
    """SQL-backed ``Store`` for durable multi-process deployments, any SQLAlchemy async engine works.

    The OAuth handoff is bridged by polling the codes table,
    so the waiting process picks the code up within the poll interval.
    """

    def __init__(self, engine: AsyncEngine, *, max_turns: int = _DEFAULT_MAX_TURNS) -> None:
        self._engine = engine
        self._sessions = async_sessionmaker(engine, expire_on_commit=False)
        self._max_turns = max_turns

    @classmethod
    def from_url(cls, url: str, *, max_turns: int = _DEFAULT_MAX_TURNS) -> 'PostgresStore':
        return cls(create_async_engine(url), max_turns=max_turns)

    async def prepare(self) -> None:
        """Create the store's tables when they do not exist yet."""
        async with self._engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

    async def load_history(self, principal: str) -> list[typing.Any]:
        async with self._sessions() as session:
            rows = await session.scalars(
                sqlalchemy.select(HistoryTurn)
                .where(HistoryTurn.principal == principal, HistoryTurn.expires_at > time.time())
                .order_by(HistoryTurn.id)
            )
            return [message for row in rows for message in row.messages]

    async def append_history(self, principal: str, messages: list[typing.Any], *, ttl_seconds: int) -> None:
        async with self._sessions.begin() as session:
            session.add(HistoryTurn(principal=principal, messages=list(messages), expires_at=time.time() + ttl_seconds))
            await session.flush()
            await self._trim_turns(session, principal)

    async def replace_history(self, principal: str, messages: list[typing.Any], *, ttl_seconds: int) -> None:
        async with self._sessions.begin() as session:
            await session.execute(sqlalchemy.delete(HistoryTurn).where(HistoryTurn.principal == principal))
            session.add(HistoryTurn(principal=principal, messages=list(messages), expires_at=time.time() + ttl_seconds))

    async def dedupe(self, key: str, *, ttl_seconds: int) -> bool:
        async with self._sessions.begin() as session:
            existing = await session.get(DedupeKey, key)
            if existing is not None and existing.expires_at > time.time():
                return True
            if existing is not None:
                existing.expires_at = time.time() + ttl_seconds
            else:
                session.add(DedupeKey(key=key, expires_at=time.time() + ttl_seconds))
            return False

    async def park_approval(
        self, principal: str, state: collections.abc.Mapping[str, typing.Any], *, ttl_seconds: int
    ) -> None:
        async with self._sessions.begin() as session:
            await session.merge(Approval(principal=principal, state=dict(state), expires_at=time.time() + ttl_seconds))

    async def take_approval(self, principal: str) -> dict[str, typing.Any] | None:
        async with self._sessions.begin() as session:
            row = await session.get(Approval, principal)
            if row is None:
                return None
            await session.delete(row)
            return dict(row.state) if row.expires_at > time.time() else None

    async def get_mcp_token(self, server: str, principal: str) -> dict[str, typing.Any] | None:
        async with self._sessions() as session:
            row = await session.get(McpToken, (server, principal))
            return dict(row.token) if row is not None else None

    async def set_mcp_token(self, server: str, principal: str, token: collections.abc.Mapping[str, typing.Any]) -> None:
        async with self._sessions.begin() as session:
            await session.merge(McpToken(server=server, principal=principal, token=dict(token)))

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

    async def set_bot_token(self, surface: str, tenant: str, token: str) -> None:
        async with self._sessions.begin() as session:
            await session.merge(BotToken(surface=surface, tenant=tenant, token=token))

    async def deliver_oauth_code(self, state: str, code: str) -> None:
        async with self._sessions.begin() as session:
            await session.merge(OAuthCode(state=state, code=code, expires_at=time.time() + _OAUTH_CODE_TTL_SECONDS))

    async def await_oauth_code(self, state: str, *, timeout_seconds: float) -> str | None:
        deadline = time.monotonic() + timeout_seconds
        while True:
            async with self._sessions.begin() as session:
                row = await session.get(OAuthCode, state)
                if row is not None:
                    await session.delete(row)
                    if row.expires_at > time.time():
                        return row.code
            if time.monotonic() >= deadline:
                return None
            await asyncio.sleep(min(_OAUTH_POLL_INTERVAL_SECONDS, timeout_seconds))

    async def _trim_turns(self, session: typing.Any, principal: str) -> None:
        ids = list(
            await session.scalars(
                sqlalchemy.select(HistoryTurn.id)
                .where(HistoryTurn.principal == principal)
                .order_by(HistoryTurn.id.desc())
            )
        )
        stale = ids[self._max_turns :]
        if stale:
            await session.execute(sqlalchemy.delete(HistoryTurn).where(HistoryTurn.id.in_(stale)))
