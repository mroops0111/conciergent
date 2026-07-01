from conciergent import MemoryStore


async def test_history_round_trips():
    store = MemoryStore()
    assert await store.load_history('p') == []
    await store.save_history('p', [1, 2, 3])
    assert await store.load_history('p') == [1, 2, 3]


async def test_history_is_isolated_per_principal():
    store = MemoryStore()
    await store.save_history('a', ['x'])
    assert await store.load_history('b') == []


async def test_dedupe_reports_repeats():
    store = MemoryStore()
    assert await store.dedupe('e1', ttl_seconds=60) is False
    assert await store.dedupe('e1', ttl_seconds=60) is True
    assert await store.dedupe('e2', ttl_seconds=60) is False


async def test_approval_is_taken_once():
    store = MemoryStore()
    assert await store.take_approval('p') is None
    await store.park_approval('p', {'k': 'v'}, ttl_seconds=60)
    assert await store.take_approval('p') == {'k': 'v'}
    assert await store.take_approval('p') is None


async def test_approval_expires_after_ttl():
    store = MemoryStore()
    await store.park_approval('p', {'k': 'v'}, ttl_seconds=0)
    assert await store.take_approval('p') is None
