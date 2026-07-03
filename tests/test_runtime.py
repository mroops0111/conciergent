import dataclasses
import typing

from conciergent import (
    Card,
    Carousel,
    MemoryStore,
    PendingApproval,
    ReplySurface,
    Section,
    TurnResult,
    run_turn,
)
from conciergent.runner import ChatRunner


class RecordingSurface(ReplySurface):
    """A fake surface that records every call instead of touching the network."""

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
    """A stand-in runner that returns a fixed turn, so run_turn is tested without a real agent."""

    output: typing.Any
    new_history: list[typing.Any] = dataclasses.field(default_factory=list)

    async def run(
        self,
        user_input: str,
        *,
        principal: str,
        history: list[typing.Any],
        pending_approval: dict[str, typing.Any] | None,
        bridge: typing.Any = None,
        surface: typing.Any = None,
    ) -> TurnResult:
        return TurnResult(output=self.output, history=self.new_history)


def _runner(output: typing.Any, new_history: list[typing.Any] | None = None) -> ChatRunner:
    # run_turn only needs `.run`, so a scripted stand-in is cast to the concrete runner type.
    return typing.cast(ChatRunner, ScriptedRunner(output=output, new_history=new_history or []))


async def _drive_turn(output: typing.Any):
    surface = RecordingSurface()
    store = MemoryStore()
    await run_turn('hi', principal='slack:T:U', runner=_runner(output), surface=surface, store=store)
    return surface, store


async def test_text_reply_is_dispatched():
    surface, _ = await _drive_turn('hello')
    assert ('processing', None) in surface.calls
    assert ('text', 'hello') in surface.calls


async def test_card_reply_is_dispatched_non_destructive():
    card = Card(title='t', sections=[Section(text='b')])
    surface, _ = await _drive_turn(card)
    assert any(kind == 'card' and payload[0] is card and payload[1] is False for kind, payload in surface.calls)


async def test_carousel_reply_is_dispatched_with_fallback_last():
    option, fallback = Card(title='a'), Card(title='b')
    surface, _ = await _drive_turn(Carousel(options=[option], fallback=fallback))
    assert ('carousel', [option, fallback]) in surface.calls


async def test_history_is_persisted():
    surface = RecordingSurface()
    store = MemoryStore()
    runner = _runner('ok', [{'role': 'user'}, {'role': 'assistant'}])
    await run_turn('hi', principal='p', runner=runner, surface=surface, store=store)
    assert await store.load_history('p') == [{'role': 'user'}, {'role': 'assistant'}]


async def test_pending_approval_parks_and_renders_destructive():
    card = Card(title='Delete everything?')
    surface, store = await _drive_turn(PendingApproval(card=card, state={'resume': 'x'}))
    assert any(kind == 'card' and payload[1] is True for kind, payload in surface.calls)
    assert await store.take_approval('slack:T:U') == {'resume': 'x'}


async def test_pending_approval_does_not_overwrite_history():
    surface = RecordingSurface()
    store = MemoryStore()
    await store.append_history('slack:T:U', [{'role': 'user'}, {'role': 'assistant'}], ttl_seconds=60)
    runner = _runner(PendingApproval(card=Card(title='?'), state={'resume': 'x'}))
    await run_turn('hi', principal='slack:T:U', runner=runner, surface=surface, store=store)
    assert await store.load_history('slack:T:U') == [{'role': 'user'}, {'role': 'assistant'}]


async def test_conversations_scope_history_within_one_principal():
    surface = RecordingSurface()
    store = MemoryStore()
    runner = _runner('ok', [{'turn': 1}])
    await run_turn('hi', principal='p', conversation='p:thread-a', runner=runner, surface=surface, store=store)
    assert await store.load_history('p:thread-a') == [{'turn': 1}]
    assert await store.load_history('p:thread-b') == []
    assert await store.load_history('p') == []
