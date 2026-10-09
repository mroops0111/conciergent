import dataclasses
import typing

from conciergent import (
    Card,
    Carousel,
    PendingApproval,
    ReplySurface,
    Section,
    TurnResult,
    run_turn,
)
from conciergent.agent.runner import ChatRunner
from conciergent.groups import GroupTurn
from conciergent.store.message import MessageStore


_PRINCIPAL = 'slack:T:U'


class RecordingSurface(ReplySurface):
    def __init__(self) -> None:
        self.calls: list[tuple[str, typing.Any]] = []

    async def send_text(self, text: str) -> None:
        self.calls.append(('text', text))

    async def send_card(self, card: Card, *, destructive: bool = False) -> None:
        self.calls.append(('card', (card, destructive)))

    async def send_carousel(self, cards: list[Card]) -> None:
        self.calls.append(('carousel', cards))

    async def show_processing(self) -> None:
        self.calls.append(('processing', None))


@dataclasses.dataclass
class ScriptedRunner:
    output: typing.Any
    new_history: list[typing.Any] = dataclasses.field(default_factory=list)
    invalidate_history: bool = False
    # The pending approval each run received, so a test can tell whose approval a turn resumed.
    resumed: list[dict[str, typing.Any] | None] = dataclasses.field(default_factory=list)
    groups_supported: bool = True

    async def supports_groups(self) -> bool:
        return self.groups_supported

    async def run(
        self,
        user_input: str,
        *,
        principal: str,
        history: list[typing.Any],
        pending_approval: dict[str, typing.Any] | None,
        bridge: typing.Any = None,
        surface: typing.Any = None,
        group: typing.Any = None,
    ) -> TurnResult:
        self.resumed.append(pending_approval)
        return TurnResult(output=self.output, history=self.new_history, invalidate_history=self.invalidate_history)


def _runner(
    output: typing.Any, new_history: list[typing.Any] | None = None, *, invalidate_history: bool = False
) -> ChatRunner:
    # run_turn only needs `.run`, so a scripted stand-in is cast to the concrete runner type.
    return typing.cast(
        ChatRunner,
        ScriptedRunner(output=output, new_history=new_history or [], invalidate_history=invalidate_history),
    )


async def _drive_turn(output: typing.Any, message_store: MessageStore) -> RecordingSurface:
    surface = RecordingSurface()
    await run_turn('hi', principal=_PRINCIPAL, runner=_runner(output), surface=surface, message_store=message_store)
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
    surface = RecordingSurface()

    await run_turn(
        'hi', principal=principal, runner=_runner('ok', new_history), surface=surface, message_store=message_store
    )

    assert await message_store.load_history(principal) == new_history


async def test_invalidate_history_clears_the_stored_history(message_store: MessageStore):
    await message_store.append_history(_PRINCIPAL, [{'role': 'user'}], ttl_seconds=60)
    surface = RecordingSurface()
    runner = _runner('signed out', [{'role': 'assistant'}], invalidate_history=True)

    await run_turn('sign me out', principal=_PRINCIPAL, runner=runner, surface=surface, message_store=message_store)

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
    surface = RecordingSurface()
    await message_store.append_history(_PRINCIPAL, existing_history, ttl_seconds=60)
    runner = _runner(PendingApproval(card=Card(header='?', sections=[Section(text='b')]), state={'resume': 'x'}))

    await run_turn('hi', principal=_PRINCIPAL, runner=runner, surface=surface, message_store=message_store)

    assert await message_store.load_history(_PRINCIPAL) == existing_history


async def test_conversations_scope_history_within_one_principal(message_store: MessageStore):
    principal = 'p'
    conversation = 'p:thread-a'
    turn_history = [{'turn': 1}]
    surface = RecordingSurface()

    await run_turn(
        'hi',
        principal=principal,
        conversation=conversation,
        runner=_runner('ok', turn_history),
        surface=surface,
        message_store=message_store,
    )

    assert await message_store.load_history(conversation) == turn_history
    assert await message_store.load_history('p:thread-b') == []
    assert await message_store.load_history(principal) == []


class AcknowledgingSurface(RecordingSurface):
    async def acknowledge_silently(self) -> None:
        self.calls.append(('acknowledged', None))


_GROUP = 'line:group:G1'
_ALICE = 'line:Ua'
_BOB = 'line:Ub'


async def test_group_approval_is_owned_by_the_member_who_parked_it(message_store: MessageStore):
    state = {'resume': 'alice'}
    runner = ScriptedRunner(output=PendingApproval(card=Card(header='?', sections=[Section(text='b')]), state=state))
    await run_turn(
        'delete it',
        principal=_ALICE,
        runner=typing.cast(ChatRunner, runner),
        surface=RecordingSurface(),
        message_store=message_store,
        group=GroupTurn(_GROUP, 'Alice'),
    )
    runner.output = 'ok'

    # Bob's own message runs as a fresh turn and leaves Alice's approval parked.
    await run_turn(
        'what time is it',
        principal=_BOB,
        runner=typing.cast(ChatRunner, runner),
        surface=RecordingSurface(),
        message_store=message_store,
        group=GroupTurn(_GROUP, 'Bob'),
    )
    await run_turn(
        'Confirm',
        principal=_ALICE,
        runner=typing.cast(ChatRunner, runner),
        surface=RecordingSurface(),
        message_store=message_store,
        group=GroupTurn(_GROUP, 'Alice'),
    )

    assert runner.resumed == [None, None, state]


async def test_group_confirm_without_an_own_approval_is_dropped_quietly(message_store: MessageStore):
    state = {'resume': 'alice'}
    await message_store.park_approval(_GROUP, state, ttl_seconds=60, owner=_ALICE)
    runner = ScriptedRunner(output='ok')
    surface = AcknowledgingSurface()

    await run_turn(
        'Confirm',
        principal=_BOB,
        runner=typing.cast(ChatRunner, runner),
        surface=surface,
        message_store=message_store,
        group=GroupTurn(_GROUP, 'Bob'),
    )

    assert runner.resumed == []
    assert surface.calls == [('acknowledged', None)]
    assert await message_store.take_approval(_GROUP, owner=_ALICE) == state


async def test_direct_confirm_without_an_approval_still_runs(message_store: MessageStore):
    runner = ScriptedRunner(output='ok')

    await run_turn(
        'Confirm',
        principal=_ALICE,
        runner=typing.cast(ChatRunner, runner),
        surface=AcknowledgingSurface(),
        message_store=message_store,
    )

    assert runner.resumed == [None]


async def test_a_group_turn_is_dropped_quietly_while_groups_cannot_be_served(message_store: MessageStore):
    runner = ScriptedRunner(output='ok', groups_supported=False)
    surface = AcknowledgingSurface()

    await run_turn(
        'hello',
        principal=_BOB,
        runner=typing.cast(ChatRunner, runner),
        surface=surface,
        message_store=message_store,
        group=GroupTurn(_GROUP, 'Bob'),
    )

    assert runner.resumed == []
    assert surface.calls == [('acknowledged', None)]
