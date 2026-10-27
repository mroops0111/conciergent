import abc
import re
import secrets
import typing

import redis.asyncio


class ChartStore(abc.ABC):
    """Store rendered chart images, each kept for a fixed time under an unguessable id.

    The id is the only access control, since a chat platform fetches the image without credentials.
    """

    @property
    @abc.abstractmethod
    def ttl_seconds(self) -> int:
        """How long a stored image stays, which also bounds how long a client may cache it."""
        ...

    @abc.abstractmethod
    async def put(self, image: bytes) -> str:
        """Store ``image`` and return its new id."""
        ...

    @abc.abstractmethod
    async def get(self, chart_id: str) -> bytes | None:
        """The stored image, or None when the id names no image or the image has expired."""
        ...

    @abc.abstractmethod
    async def exists(self, chart_id: str) -> bool: ...


_PREFIX = 'conciergent:chart'
# token_urlsafe output, so a request path that could not be an id is rejected before it reaches Redis.
_CHART_ID_PATTERN = re.compile(r'[A-Za-z0-9_-]{16,64}')


class RedisChartStore(ChartStore):
    """Keep chart images in Redis, expiring each with a Redis TTL."""

    def __init__(self, client: redis.asyncio.Redis, *, ttl_seconds: int) -> None:
        self._redis = client
        self._ttl_seconds = ttl_seconds

    @classmethod
    def from_url(cls, url: str, *, ttl_seconds: int) -> 'RedisChartStore':
        return cls(redis.asyncio.Redis.from_url(url), ttl_seconds=ttl_seconds)

    @property
    @typing.override
    def ttl_seconds(self) -> int:
        return self._ttl_seconds

    @typing.override
    async def put(self, image: bytes) -> str:
        chart_id = secrets.token_urlsafe(24)
        await self._redis.set(self._key(chart_id), image, ex=self._ttl_seconds)
        return chart_id

    @typing.override
    async def get(self, chart_id: str) -> bytes | None:
        if not _CHART_ID_PATTERN.fullmatch(chart_id):
            return None
        image = await self._redis.get(self._key(chart_id))
        # The client is built without decode_responses, so a stored image always comes back as bytes.
        return image if isinstance(image, bytes) else None

    @typing.override
    async def exists(self, chart_id: str) -> bool:
        if not _CHART_ID_PATTERN.fullmatch(chart_id):
            return False
        return bool(await self._redis.exists(self._key(chart_id)))

    def _key(self, chart_id: str) -> str:
        return f'{_PREFIX}:{chart_id}'
