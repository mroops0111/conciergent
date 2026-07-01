from conciergent import MemoryStore


async def test_history_save_load():
    store = MemoryStore()
    assert await store.load_history('p') == []
    await store.save_history('p', [1, 2, 3])
    assert await store.load_history('p') == [1, 2, 3]


async def test_history_is_isolated_per_principal():
    store = MemoryStore()
    await store.save_history('a', ['x'])
    assert await store.load_history('b') == []


async def test_seen_reports_duplicates():
    store = MemoryStore()
    assert await store.seen('e1', ttl_seconds=60) is False
    assert await store.seen('e1', ttl_seconds=60) is True
    assert await store.seen('e2', ttl_seconds=60) is False


async def test_approval_park_and_take_once():
    store = MemoryStore()
    assert await store.take_approval('p') is None
    await store.park_approval('p', {'k': 'v'}, ttl_seconds=60)
    assert await store.take_approval('p') == {'k': 'v'}
    assert await store.take_approval('p') is None
