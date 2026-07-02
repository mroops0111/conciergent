from conciergent import MemoryStore


async def test_history_round_trips():
    store = MemoryStore()
    assert await store.load_history('p') == []
    await store.append_history('p', [1, 2], ttl_seconds=60)
    await store.append_history('p', [3], ttl_seconds=60)
    assert await store.load_history('p') == [1, 2, 3]


async def test_history_is_isolated_per_principal():
    store = MemoryStore()
    await store.append_history('a', ['x'], ttl_seconds=60)
    assert await store.load_history('b') == []


async def test_history_turn_expires_after_ttl():
    store = MemoryStore()
    await store.append_history('p', ['old'], ttl_seconds=0)
    await store.append_history('p', ['live'], ttl_seconds=60)
    assert await store.load_history('p') == ['live']


async def test_history_keeps_only_recent_turns():
    store = MemoryStore(max_turns=2)
    for turn in (1, 2, 3):
        await store.append_history('p', [turn], ttl_seconds=60)
    assert await store.load_history('p') == [2, 3]


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


async def test_mcp_token_round_trips_per_server_and_principal():
    store = MemoryStore()
    assert await store.get_mcp_token('petstore', 'slack:T:U') is None
    await store.set_mcp_token('petstore', 'slack:T:U', {'access_token': 'a'})
    assert await store.get_mcp_token('petstore', 'slack:T:U') == {'access_token': 'a'}
    assert await store.get_mcp_token('petstore', 'slack:T:OTHER') is None
    assert await store.get_mcp_token('other', 'slack:T:U') is None


async def test_mcp_client_round_trips_per_server():
    store = MemoryStore()
    assert await store.get_mcp_client('petstore') is None
    await store.set_mcp_client('petstore', {'client_id': 'c1'})
    assert await store.get_mcp_client('petstore') == {'client_id': 'c1'}
    assert await store.get_mcp_client('other') is None


async def test_replace_history_collapses_all_turns():
    store = MemoryStore()
    await store.append_history('p', [1], ttl_seconds=60)
    await store.append_history('p', [2], ttl_seconds=60)
    await store.replace_history('p', ['summary', 2], ttl_seconds=60)
    assert await store.load_history('p') == ['summary', 2]


async def test_bot_token_round_trips_per_surface_and_tenant():
    store = MemoryStore()
    assert await store.resolve_bot_token('slack', 'T1') is None
    await store.set_bot_token('slack', 'T1', 'xoxb-1')
    assert await store.resolve_bot_token('slack', 'T1') == 'xoxb-1'
    assert await store.resolve_bot_token('slack', 'T2') is None


async def test_oauth_code_is_delivered_to_the_waiter():
    import asyncio

    store = MemoryStore()

    async def deliver() -> None:
        await asyncio.sleep(0)
        await store.deliver_oauth_code('s1', 'code-1')

    task = asyncio.create_task(deliver())
    assert await store.await_oauth_code('s1', timeout_seconds=5) == 'code-1'
    await task


async def test_oauth_code_wait_times_out_to_none():
    store = MemoryStore()
    assert await store.await_oauth_code('nobody', timeout_seconds=0.01) is None


async def test_mcp_token_is_copied_not_aliased():
    store = MemoryStore()
    token = {'access_token': 'a'}
    await store.set_mcp_token('petstore', 'slack:T:U', token)
    token['access_token'] = 'mutated'
    assert await store.get_mcp_token('petstore', 'slack:T:U') == {'access_token': 'a'}
