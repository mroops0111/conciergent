import asyncio
import typing

from conciergent.chart.renderer import ChartRenderer
from conciergent.store.chart import ChartStore


# The route that serves a stored chart, relative to the server's public URL.
CHART_PATH = '/charts/{chart_id}.png'


class ChartPublisher:
    """Render a chart, store it, and name the public URL a chat platform fetches it from.

    Every surface takes a hero image by URL, and LINE takes images no other way,
    so a chart is served from this app's own public URL rather than uploaded to each platform.
    """

    def __init__(self, renderer: ChartRenderer, store: ChartStore, *, base_url: str) -> None:
        self._renderer = renderer
        self._store = store
        self._base_url = base_url.rstrip('/')
        prefix, suffix = CHART_PATH.split('{chart_id}')
        self._url_prefix = self._base_url + prefix
        self._url_suffix = suffix

    @property
    def store(self) -> ChartStore:
        return self._store

    async def publish(self, spec: typing.Any, *, accent_color: str | None = None) -> str:
        """Render ``spec`` and return the new chart's public URL, raising ``ChartSpecError`` for a spec it refuses."""
        # Rendering runs a JavaScript engine for a noticeable fraction of a second, so it stays off the event loop.
        image = await asyncio.to_thread(self._renderer.render, spec, accent_color=accent_color)
        chart_id = await self._store.put(image)
        return self._url_prefix + chart_id + self._url_suffix

    async def is_missing(self, url: str) -> bool:
        """Whether ``url`` points at this app's charts but names none, mistyped, made up, or expired.

        A URL anywhere else is not this publisher's to judge, so it is never missing.
        """
        if not (url.startswith(self._url_prefix) and url.endswith(self._url_suffix)):
            return False
        chart_id = url.removeprefix(self._url_prefix).removesuffix(self._url_suffix)
        return not await self._store.exists(chart_id)
