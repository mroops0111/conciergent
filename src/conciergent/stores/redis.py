import collections.abc
import json
import typing
import uuid

import redis.asyncio

from conciergent.stores.base import DEFAULT_MAX_TURNS, Store


_INDEX_TTL_SECONDS = 30 * 86400
_OAUTH_CODE_TTL_SECONDS = 300
_PREFIX = 'conciergent'


class RedisStore(Store):
    """Redis-backed ``Store`` for multi-process deployments.

    History keeps one key per turn with its own expiry plus an index list,
    so old turns age out without a read-time cutoff.
    The OAuth handoff rides a blocking list pop, which bridges processes.
    Requires a Redis server of 6.2 or newer for GETDEL.
    """

    def __init__(self, client: redis.asyncio.Redis, *, max_turns: int = DEFAULT_MAX_TURNS) -> None:
        self._redis = client
        self._max_turns = max_turns

    @classmethod
    def from_url(cls, url: str, *, max_turns: int = DEFAULT_MAX_TURNS) -> 'RedisStore':
        return cls(redis.asyncio.Redis.from_url(url), max_turns=max_turns)

    @typing.override
    async def load_history(self, conversation: str) -> list[typing.Any]:
        turn_ids = [_text(raw) for raw in await self._redis.lrange(self._index_key(conversation), 0, -1)]
        if not turn_ids:
            return []
        payloads = await self._redis.mget([self._turn_key(conversation, turn_id) for turn_id in turn_ids])
        messages: list[typing.Any] = []
        for payload in payloads:
            # Expired turn keys come back as None and their index entries are trimmed on the next append.
            if payload is not None:
                messages.extend(json.loads(payload))
        return messages

    @typing.override
    async def append_history(self, conversation: str, messages: list[typing.Any], *, ttl_seconds: int) -> None:
        turn_id = uuid.uuid4().hex
        pipeline = self._redis.pipeline(transaction=True)
        pipeline.set(self._turn_key(conversation, turn_id), json.dumps(messages), ex=ttl_seconds)
        pipeline.rpush(self._index_key(conversation), turn_id)
        pipeline.ltrim(self._index_key(conversation), -self._max_turns, -1)
        pipeline.expire(self._index_key(conversation), _INDEX_TTL_SECONDS)
        await pipeline.execute()

    @typing.override
    async def replace_history(self, conversation: str, messages: list[typing.Any], *, ttl_seconds: int) -> None:
        turn_ids = [_text(raw) for raw in await self._redis.lrange(self._index_key(conversation), 0, -1)]
        turn_id = uuid.uuid4().hex
        pipeline = self._redis.pipeline(transaction=True)
        if turn_ids:
            pipeline.delete(*[self._turn_key(conversation, old) for old in turn_ids])
        pipeline.delete(self._index_key(conversation))
        pipeline.set(self._turn_key(conversation, turn_id), json.dumps(messages), ex=ttl_seconds)
        pipeline.rpush(self._index_key(conversation), turn_id)
        pipeline.expire(self._index_key(conversation), _INDEX_TTL_SECONDS)
        await pipeline.execute()

    @typing.override
    async def dedupe(self, key: str, *, ttl_seconds: int) -> bool:
        recorded = await self._redis.set(f'{_PREFIX}:dedupe:{key}', '1', nx=True, ex=ttl_seconds)
        return recorded is None

    @typing.override
    async def park_approval(
        self, conversation: str, state: collections.abc.Mapping[str, typing.Any], *, ttl_seconds: int
    ) -> None:
        await self._redis.set(f'{_PREFIX}:approval:{conversation}', json.dumps(dict(state)), ex=ttl_seconds)

    @typing.override
    async def take_approval(self, conversation: str) -> dict[str, typing.Any] | None:
        payload = await self._redis.getdel(f'{_PREFIX}:approval:{conversation}')
        return json.loads(payload) if payload is not None else None

    @typing.override
    async def get_mcp_token(self, server: str, principal: str) -> dict[str, typing.Any] | None:
        payload = await self._redis.get(f'{_PREFIX}:mcp-token:{server}:{principal}')
        return json.loads(payload) if payload is not None else None

    @typing.override
    async def set_mcp_token(self, server: str, principal: str, token: collections.abc.Mapping[str, typing.Any]) -> None:
        await self._redis.set(f'{_PREFIX}:mcp-token:{server}:{principal}', json.dumps(dict(token)))

    @typing.override
    async def get_mcp_client(self, server: str) -> dict[str, typing.Any] | None:
        payload = await self._redis.get(f'{_PREFIX}:mcp-client:{server}')
        return json.loads(payload) if payload is not None else None

    @typing.override
    async def set_mcp_client(self, server: str, client: collections.abc.Mapping[str, typing.Any]) -> None:
        await self._redis.set(f'{_PREFIX}:mcp-client:{server}', json.dumps(dict(client)))

    @typing.override
    async def resolve_bot_token(self, surface: str, tenant: str) -> str | None:
        token = await self._redis.get(f'{_PREFIX}:bot-token:{surface}:{tenant}')
        return _text(token) if token is not None else None

    @typing.override
    async def set_bot_token(self, surface: str, tenant: str, token: str) -> None:
        await self._redis.set(f'{_PREFIX}:bot-token:{surface}:{tenant}', token)

    @typing.override
    async def deliver_oauth_code(self, state: str, code: str) -> None:
        pipeline = self._redis.pipeline(transaction=True)
        pipeline.rpush(f'{_PREFIX}:oauth-code:{state}', code)
        # A stranded payload with no waiter is garbage collected by the expiry.
        pipeline.expire(f'{_PREFIX}:oauth-code:{state}', _OAUTH_CODE_TTL_SECONDS)
        await pipeline.execute()

    @typing.override
    async def await_oauth_code(self, state: str, *, timeout_seconds: float) -> str | None:
        if timeout_seconds <= 0:
            # BLPOP treats a zero timeout as block-forever, so a non-positive wait checks once instead.
            popped_now = await self._redis.lpop(f'{_PREFIX}:oauth-code:{state}')
            return _text(popped_now) if isinstance(popped_now, (bytes, str)) else None
        popped = await self._redis.blpop([f'{_PREFIX}:oauth-code:{state}'], timeout=timeout_seconds)
        if popped is None:
            return None
        _, code = popped
        return _text(code)

    def _index_key(self, conversation: str) -> str:
        return f'{_PREFIX}:history:{conversation}:index'

    def _turn_key(self, conversation: str, turn_id: str) -> str:
        return f'{_PREFIX}:history:{conversation}:turn:{turn_id}'


def _text(value: bytes | str) -> str:
    return value.decode() if isinstance(value, bytes) else value
