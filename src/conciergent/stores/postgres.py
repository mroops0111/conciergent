import asyncio
import collections.abc
import time
import typing

import sqlalchemy
import sqlalchemy.exc
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from conciergent.stores.base import DEFAULT_MAX_TURNS, Store


_OAUTH_CODE_TTL_SECONDS = 300.0
_OAUTH_POLL_INTERVAL_SECONDS = 0.2


class Base(DeclarativeBase):
    pass


class HistoryTurn(Base):
    __tablename__ = 'conciergent_history_turns'

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    conversation: Mapped[str] = mapped_column(sqlalchemy.String(255), index=True)
    messages: Mapped[list[typing.Any]] = mapped_column(sqlalchemy.JSON)
    expires_at: Mapped[float] = mapped_column(sqlalchemy.Float)


class Approval(Base):
    __tablename__ = 'conciergent_approvals'

    conversation: Mapped[str] = mapped_column(sqlalchemy.String(255), primary_key=True)
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

    def __init__(self, engine: AsyncEngine, *, max_turns: int = DEFAULT_MAX_TURNS) -> None:
        self._engine = engine
        self._sessions = async_sessionmaker(engine, expire_on_commit=False)
        self._max_turns = max_turns

    @classmethod
    def from_url(cls, url: str, *, max_turns: int = DEFAULT_MAX_TURNS) -> 'PostgresStore':
        return cls(create_async_engine(url), max_turns=max_turns)

    @typing.override
    async def prepare(self) -> None:
        """Create the store's tables when they do not exist yet."""
        async with self._engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

    @typing.override
    async def load_history(self, conversation: str) -> list[typing.Any]:
        async with self._sessions() as session:
            rows = await session.scalars(
                sqlalchemy.select(HistoryTurn)
                .where(HistoryTurn.conversation == conversation, HistoryTurn.expires_at > time.time())
                .order_by(HistoryTurn.id)
            )
            return [message for row in rows for message in row.messages]

    @typing.override
    async def append_history(self, conversation: str, messages: list[typing.Any], *, ttl_seconds: int) -> None:
        async with self._sessions.begin() as session:
            session.add(
                HistoryTurn(conversation=conversation, messages=list(messages), expires_at=time.time() + ttl_seconds)
            )
            await session.flush()
            await self._trim_turns(session, conversation)

    @typing.override
    async def replace_history(self, conversation: str, messages: list[typing.Any], *, ttl_seconds: int) -> None:
        async with self._sessions.begin() as session:
            await session.execute(sqlalchemy.delete(HistoryTurn).where(HistoryTurn.conversation == conversation))
            session.add(
                HistoryTurn(conversation=conversation, messages=list(messages), expires_at=time.time() + ttl_seconds)
            )

    @typing.override
    async def dedupe(self, key: str, *, ttl_seconds: int) -> bool:
        try:
            async with self._sessions.begin() as session:
                existing = await session.get(DedupeKey, key)
                if existing is not None and existing.expires_at > time.time():
                    return True
                if existing is not None:
                    existing.expires_at = time.time() + ttl_seconds
                else:
                    session.add(DedupeKey(key=key, expires_at=time.time() + ttl_seconds))
                return False
        except sqlalchemy.exc.IntegrityError:
            # A concurrent insert of the same key won the race, which is exactly a duplicate delivery.
            return True

    @typing.override
    async def park_approval(
        self, conversation: str, state: collections.abc.Mapping[str, typing.Any], *, ttl_seconds: int
    ) -> None:
        async with self._sessions.begin() as session:
            await session.merge(
                Approval(conversation=conversation, state=dict(state), expires_at=time.time() + ttl_seconds)
            )

    @typing.override
    async def take_approval(self, conversation: str) -> dict[str, typing.Any] | None:
        # A single DELETE with RETURNING hands the row to exactly one concurrent taker.
        async with self._sessions.begin() as session:
            result = await session.execute(
                sqlalchemy.delete(Approval)
                .where(Approval.conversation == conversation)
                .returning(Approval.state, Approval.expires_at)
            )
            row = result.first()
            if row is None:
                return None
            state, expires_at = row
            return dict(state) if expires_at > time.time() else None

    @typing.override
    async def get_mcp_token(self, server: str, principal: str) -> dict[str, typing.Any] | None:
        async with self._sessions() as session:
            row = await session.get(McpToken, (server, principal))
            return dict(row.token) if row is not None else None

    @typing.override
    async def set_mcp_token(self, server: str, principal: str, token: collections.abc.Mapping[str, typing.Any]) -> None:
        async with self._sessions.begin() as session:
            await session.merge(McpToken(server=server, principal=principal, token=dict(token)))

    @typing.override
    async def get_mcp_client(self, server: str) -> dict[str, typing.Any] | None:
        async with self._sessions() as session:
            row = await session.get(McpClient, server)
            return dict(row.client) if row is not None else None

    @typing.override
    async def set_mcp_client(self, server: str, client: collections.abc.Mapping[str, typing.Any]) -> None:
        async with self._sessions.begin() as session:
            await session.merge(McpClient(server=server, client=dict(client)))

    @typing.override
    async def resolve_bot_token(self, surface: str, tenant: str) -> str | None:
        async with self._sessions() as session:
            row = await session.get(BotToken, (surface, tenant))
            return row.token if row is not None else None

    @typing.override
    async def set_bot_token(self, surface: str, tenant: str, token: str) -> None:
        async with self._sessions.begin() as session:
            await session.merge(BotToken(surface=surface, tenant=tenant, token=token))

    @typing.override
    async def deliver_oauth_code(self, state: str, code: str) -> None:
        async with self._sessions.begin() as session:
            await session.merge(OAuthCode(state=state, code=code, expires_at=time.time() + _OAUTH_CODE_TTL_SECONDS))

    @typing.override
    async def await_oauth_code(self, state: str, *, timeout_seconds: float) -> str | None:
        deadline = time.monotonic() + timeout_seconds
        while True:
            async with self._sessions.begin() as session:
                # A single DELETE with RETURNING claims the code for exactly one waiter.
                result = await session.execute(
                    sqlalchemy.delete(OAuthCode)
                    .where(OAuthCode.state == state)
                    .returning(OAuthCode.code, OAuthCode.expires_at)
                )
                row = result.first()
                if row is not None:
                    code, expires_at = row
                    if expires_at > time.time():
                        return code
            if time.monotonic() >= deadline:
                return None
            await asyncio.sleep(min(_OAUTH_POLL_INTERVAL_SECONDS, timeout_seconds))

    async def _trim_turns(self, session: AsyncSession, conversation: str) -> None:
        ids = list(
            await session.scalars(
                sqlalchemy.select(HistoryTurn.id)
                .where(HistoryTurn.conversation == conversation)
                .order_by(HistoryTurn.id.desc())
            )
        )
        stale = ids[self._max_turns :]
        if stale:
            await session.execute(sqlalchemy.delete(HistoryTurn).where(HistoryTurn.id.in_(stale)))
