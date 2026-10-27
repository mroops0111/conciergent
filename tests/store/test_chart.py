import pytest
import redis.asyncio

from conciergent.chart import ChartPublisher, ChartRenderer, DefaultChartStyle
from conciergent.defaults import defaults_layer
from conciergent.store.chart import ChartStore


_IMAGE = b'\x89PNG\r\n\x1a\nimage'


async def test_an_image_round_trips_under_its_id(chart_store: ChartStore):
    chart_id = await chart_store.put(_IMAGE)

    assert await chart_store.get(chart_id) == _IMAGE
    assert await chart_store.exists(chart_id)


async def test_each_image_gets_its_own_unguessable_id(chart_store: ChartStore):
    first = await chart_store.put(_IMAGE)
    second = await chart_store.put(_IMAGE)

    assert first != second
    assert len(first) >= 32


@pytest.mark.parametrize('chart_id', ['missing-but-well-formed-id-0123', 'short', '../etc/passwd', 'a:b' * 10])
async def test_an_unknown_or_malformed_id_finds_nothing(chart_store: ChartStore, chart_id: str):
    assert await chart_store.get(chart_id) is None
    assert not await chart_store.exists(chart_id)


async def test_an_image_expires_after_the_ttl(chart_store: ChartStore, messages_url: str):
    chart_id = await chart_store.put(_IMAGE)
    client = redis.asyncio.Redis.from_url(messages_url)
    try:
        ttl = await client.ttl(f'conciergent:chart:{chart_id}')
    finally:
        await client.aclose()

    assert 0 < ttl <= 60


async def test_the_publisher_returns_the_public_url_of_a_stored_chart(chart_store: ChartStore):
    renderer = ChartRenderer(DefaultChartStyle(font=defaults_layer()['capabilities']['charts']['font']), max_rows=10)
    publisher = ChartPublisher(renderer, chart_store, base_url='https://bot.example.com/')
    spec = {
        'data': {'values': [{'day': 'Mon', 'count': 3}]},
        'mark': 'bar',
        'encoding': {'x': {'field': 'day', 'type': 'nominal'}, 'y': {'field': 'count', 'type': 'quantitative'}},
    }

    url = await publisher.publish(spec)

    prefix = 'https://bot.example.com/charts/'
    assert url.startswith(prefix) and url.endswith('.png')
    assert (await chart_store.get(url.removeprefix(prefix).removesuffix('.png')) or b'').startswith(b'\x89PNG')
    assert not await publisher.is_missing(url)
    assert await publisher.is_missing(prefix + 'missing-but-well-formed-id-0123.png')
    # Another host's image is not the publisher's to judge.
    assert not await publisher.is_missing('https://images.example.com/charts/missing-but-well-formed-id-0123.png')
