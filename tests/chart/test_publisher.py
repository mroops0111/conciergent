import pytest
import redis.asyncio

from conciergent.chart import ChartPublisher, ChartSpecError
from conciergent.store.chart import ChartStore
from tests.conftest import CHART_BASE_URL, chart_renderer, chart_spec


_CHARTS_URL = f'{CHART_BASE_URL}/charts/'


async def test_a_published_chart_is_stored_under_its_public_url(
    chart_publisher: ChartPublisher, chart_store: ChartStore
):
    url = await chart_publisher.publish(chart_spec())

    assert url.startswith(_CHARTS_URL) and url.endswith('.png')
    image = await chart_store.get(url.removeprefix(_CHARTS_URL).removesuffix('.png'))
    assert (image or b'').startswith(b'\x89PNG')
    assert not await chart_publisher.is_missing(url)


async def test_a_trailing_slash_on_the_base_url_is_ignored(chart_store: ChartStore):
    publisher = ChartPublisher(chart_renderer(), chart_store, base_url=f'{CHART_BASE_URL}/')

    url = await publisher.publish(chart_spec())

    assert url.startswith(_CHARTS_URL)


async def test_a_refused_spec_stores_nothing(chart_publisher: ChartPublisher, messages_url: str):
    with pytest.raises(ChartSpecError):
        await chart_publisher.publish(chart_spec(data={'url': 'https://example.com/data.json'}))

    client = redis.asyncio.Redis.from_url(messages_url)
    try:
        assert await client.keys('conciergent:chart:*') == []
    finally:
        await client.aclose()


@pytest.mark.parametrize('chart_id', ['missing-but-well-formed-id-0123', 'short'])
async def test_a_chart_url_that_names_no_stored_chart_is_missing(chart_publisher: ChartPublisher, chart_id: str):
    assert await chart_publisher.is_missing(f'{_CHARTS_URL}{chart_id}.png')


@pytest.mark.parametrize(
    'url',
    [
        'https://images.example.com/charts/missing-but-well-formed-id-0123.png',
        f'{CHART_BASE_URL}/logo.png',
    ],
)
async def test_an_image_elsewhere_is_never_missing(chart_publisher: ChartPublisher, url: str):
    # Only a URL under this app's charts is the publisher's to judge.
    assert not await chart_publisher.is_missing(url)
