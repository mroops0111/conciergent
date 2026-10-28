import re
import secrets

import redis.asyncio


_PREFIX = 'conciergent:chart'
# token_urlsafe output, so a request path that could not be an id is rejected before it reaches Redis.
_CHART_ID_PATTERN = re.compile(r'[A-Za-z0-9_-]{16,64}')


class ChartStore:
    """Redis-backed store for rendered chart images, each kept for a fixed time under an unguessable id.

    The id is the only access control, since a chat platform fetches the image without credentials.
    """

    def __init__(self, client: redis.asyncio.Redis, *, ttl_seconds: int) -> None:
        self._redis = client
        self._ttl_seconds = ttl_seconds

    @classmethod
    def from_url(cls, url: str, *, ttl_seconds: int) -> 'ChartStore':
        return cls(redis.asyncio.Redis.from_url(url), ttl_seconds=ttl_seconds)

    @property
    def ttl_seconds(self) -> int:
        return self._ttl_seconds

    async def put(self, image: bytes) -> str:
        chart_id = secrets.token_urlsafe(24)
        await self._redis.set(self._key(chart_id), image, ex=self._ttl_seconds)
        return chart_id

    async def get(self, chart_id: str) -> bytes | None:
        if not _CHART_ID_PATTERN.fullmatch(chart_id):
            return None
        image = await self._redis.get(self._key(chart_id))
        # The client is built without decode_responses, so a stored image always comes back as bytes.
        return image if isinstance(image, bytes) else None

    async def exists(self, chart_id: str) -> bool:
        if not _CHART_ID_PATTERN.fullmatch(chart_id):
            return False
        return bool(await self._redis.exists(self._key(chart_id)))

    def _key(self, chart_id: str) -> str:
        return f'{_PREFIX}:{chart_id}'
