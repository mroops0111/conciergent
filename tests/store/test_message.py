import asyncio

from conciergent.store.message import MessageStore


async def test_history_round_trips(message_store: MessageStore):
    assert await message_store.load_history('p') == []
    await message_store.append_history('p', [{'a': 1}], ttl_seconds=60)
    await message_store.append_history('p', [{'b': 2}], ttl_seconds=60)
    assert await message_store.load_history('p') == [{'a': 1}, {'b': 2}]


async def test_history_is_isolated_per_principal(message_store: MessageStore):
    await message_store.append_history('a', ['x'], ttl_seconds=60)
    assert await message_store.load_history('b') == []


async def test_replace_history_collapses_all_turns(message_store: MessageStore):
    await message_store.append_history('p', [1], ttl_seconds=60)
    await message_store.append_history('p', [2], ttl_seconds=60)
    await message_store.replace_history('p', ['summary', 2], ttl_seconds=60)
    assert await message_store.load_history('p') == ['summary', 2]


async def test_history_keeps_only_recent_turns(message_store: MessageStore):
    for turn in range(12):
        await message_store.append_history('p', [turn], ttl_seconds=60)
    assert await message_store.load_history('p') == list(range(2, 12))


async def test_dedupe_reports_repeats(message_store: MessageStore):
    assert await message_store.dedupe('e1', ttl_seconds=60) is False
    assert await message_store.dedupe('e1', ttl_seconds=60) is True
    assert await message_store.dedupe('e2', ttl_seconds=60) is False


async def test_approval_is_taken_once(message_store: MessageStore):
    assert await message_store.take_approval('p') is None
    await message_store.park_approval('p', {'k': 'v'}, ttl_seconds=60)
    assert await message_store.take_approval('p') == {'k': 'v'}
    assert await message_store.take_approval('p') is None


async def test_oauth_code_reaches_the_waiter(message_store: MessageStore):
    async def deliver() -> None:
        await asyncio.sleep(0.05)
        await message_store.deliver_oauth_code('s1', 'code-1')

    task = asyncio.create_task(deliver())
    assert await message_store.await_oauth_code('s1', timeout_seconds=5) == ('code-1', 's1')
    await task


async def test_oauth_code_wait_times_out(message_store: MessageStore):
    assert await message_store.await_oauth_code('nobody', timeout_seconds=0.3) is None


async def test_oauth_code_zero_timeout_checks_once(message_store: MessageStore):
    assert await message_store.await_oauth_code('nobody', timeout_seconds=0) is None
    await message_store.deliver_oauth_code('ready', 'code-r')
    assert await message_store.await_oauth_code('ready', timeout_seconds=0) == ('code-r', 'ready')
