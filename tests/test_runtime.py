import typing

from conciergent import (
    Card,
    Carousel,
    PendingApproval,
    Section,
    run_turn,
)
from conciergent.groups import GroupTurn
from conciergent.store.message import MessageStore
from tests.conftest import RecordingSurface, StubRunner


_PRINCIPAL = 'slack:T:U'


async def _drive_turn(output: typing.Any, message_store: MessageStore) -> RecordingSurface:
    return await _turn('hi', StubRunner(output), message_store)


async def _turn(
    user_input: str,
    runner: StubRunner,
    message_store: MessageStore,
    *,
    principal: str = _PRINCIPAL,
    conversation: str | None = None,
    group: GroupTurn | None = None,
) -> RecordingSurface:
    surface = RecordingSurface()
    await run_turn(
        user_input,
        principal=principal,
        runner=runner.as_runner(),
        surface=surface,
        message_store=message_store,
        conversation=conversation,
        group=group,
    )
    return surface


async def test_text_reply_is_dispatched(message_store: MessageStore):
    text = 'hello'

    surface = await _drive_turn(text, message_store)

    assert ('processing', None) in surface.calls
    assert ('text', text) in surface.calls


async def test_card_reply_is_dispatched_non_destructive(message_store: MessageStore):
    card = Card(header='t', sections=[Section(text='b')])

    surface = await _drive_turn(card, message_store)

    assert any(kind == 'card' and payload[0] is card and payload[1] is False for kind, payload in surface.calls)


async def test_carousel_reply_is_dispatched_with_fallback_last(message_store: MessageStore):
    option = Card(header='a', sections=[Section(text='a')])
    fallback = Card(header='b', sections=[Section(text='b')])

    surface = await _drive_turn(Carousel(options=[option], fallback=fallback), message_store)

    assert ('carousel', [option, fallback]) in surface.calls


async def test_history_is_persisted(message_store: MessageStore):
    principal = 'p'
    new_history = [{'role': 'user'}, {'role': 'assistant'}]

    await _turn('hi', StubRunner('ok', new_history=new_history), message_store, principal=principal)

    assert await message_store.load_history(principal) == new_history


async def test_invalidate_history_clears_the_stored_history(message_store: MessageStore):
    await message_store.append_history(_PRINCIPAL, [{'role': 'user'}], ttl_seconds=60)
    runner = StubRunner('signed out', new_history=[{'role': 'assistant'}], invalidate_history=True)

    surface = await _turn('sign me out', runner, message_store)

    # A sign-out drops the prior turns instead of appending, so the next message starts fresh.
    assert await message_store.load_history(_PRINCIPAL) == []
    assert ('text', 'signed out') in surface.calls


async def test_pending_approval_parks_and_renders_destructive(message_store: MessageStore):
    card = Card(header='Delete everything?', sections=[Section(text='This cannot be undone.')])
    state = {'resume': 'x'}

    surface = await _drive_turn(PendingApproval(card=card, state=state), message_store)

    assert any(kind == 'card' and payload[1] is True for kind, payload in surface.calls)
    assert await message_store.take_approval(_PRINCIPAL) == state


async def test_pending_approval_does_not_overwrite_history(message_store: MessageStore):
    existing_history = [{'role': 'user'}, {'role': 'assistant'}]
    await message_store.append_history(_PRINCIPAL, existing_history, ttl_seconds=60)
    runner = StubRunner(PendingApproval(card=Card(header='?', sections=[Section(text='b')]), state={}))

    await _turn('hi', runner, message_store)

    assert await message_store.load_history(_PRINCIPAL) == existing_history


async def test_conversations_scope_history_within_one_principal(message_store: MessageStore):
    principal = 'p'
    conversation = 'p:thread-a'
    turn_history = [{'turn': 1}]

    await _turn(
        'hi',
        StubRunner('ok', new_history=turn_history),
        message_store,
        principal=principal,
        conversation=conversation,
    )

    assert await message_store.load_history(conversation) == turn_history
    assert await message_store.load_history('p:thread-b') == []
    assert await message_store.load_history(principal) == []


_GROUP = 'line:group:G1'
_ALICE = 'line:Ua'
_BOB = 'line:Ub'
_ALICE_IN_GROUP = GroupTurn(_GROUP, 'Alice')
_BOB_IN_GROUP = GroupTurn(_GROUP, 'Bob')


async def test_group_approval_is_owned_by_the_member_who_parked_it(message_store: MessageStore):
    state = {'resume': 'alice'}
    runner = StubRunner(PendingApproval(card=Card(header='?', sections=[Section(text='b')]), state=state))
    await _turn('delete it', runner, message_store, principal=_ALICE, group=_ALICE_IN_GROUP)
    runner.output = 'ok'

    # Bob's own message runs as a fresh turn and leaves Alice's approval parked.
    await _turn('what time is it', runner, message_store, principal=_BOB, group=_BOB_IN_GROUP)
    await _turn('Confirm', runner, message_store, principal=_ALICE, group=_ALICE_IN_GROUP)

    assert [call['pending_approval'] for call in runner.calls] == [None, None, state]


async def test_group_confirm_without_an_own_approval_is_dropped_quietly(message_store: MessageStore):
    state = {'resume': 'alice'}
    await message_store.park_approval(_GROUP, state, ttl_seconds=60, owner=_ALICE)
    runner = StubRunner('ok')

    surface = await _turn('Confirm', runner, message_store, principal=_BOB, group=_BOB_IN_GROUP)

    assert [call['pending_approval'] for call in runner.calls] == []
    assert surface.calls == [('acknowledged', None)]
    assert await message_store.take_approval(_GROUP, owner=_ALICE) == state


async def test_direct_confirm_without_an_approval_still_runs(message_store: MessageStore):
    runner = StubRunner('ok')

    await _turn('Confirm', runner, message_store, principal=_ALICE)

    assert [call['pending_approval'] for call in runner.calls] == [None]


async def test_a_group_turn_is_dropped_quietly_while_groups_cannot_be_served(message_store: MessageStore):
    runner = StubRunner('ok')
    runner.groups_supported = False

    surface = await _turn('hello', runner, message_store, principal=_BOB, group=_BOB_IN_GROUP)

    assert [call['pending_approval'] for call in runner.calls] == []
    assert surface.calls == [('acknowledged', None)]
