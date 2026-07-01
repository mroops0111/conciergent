from dataclasses import dataclass, field
from typing import Any

from conciergent import (
    AgentResult,
    Card,
    Carousel,
    ChatAgent,
    MemoryStore,
    PendingApproval,
    ReplySurface,
    Section,
    run_turn,
)


class RecordingSurface(ReplySurface):
    """A fake surface that records every call instead of touching the network."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []

    async def send_text(self, text: str) -> None:
        self.calls.append(('text', text))

    async def send_card(self, card: Card, *, destructive: bool = False) -> None:
        self.calls.append(('card', (card, destructive)))

    async def send_carousel(self, cards: list[Card]) -> None:
        self.calls.append(('carousel', cards))

    async def show_processing(self) -> None:
        self.calls.append(('processing', None))


@dataclass
class ScriptedAgent(ChatAgent):
    output: Any
    new_history: list[Any] = field(default_factory=list)

    async def run(
        self,
        user_input: str,
        *,
        principal: str,
        history: list[Any],
        pending: dict[str, Any] | None,
    ) -> AgentResult:
        return AgentResult(output=self.output, history=self.new_history)


async def _drive(output: Any):
    surface = RecordingSurface()
    store = MemoryStore()
    result = await run_turn(
        'hi',
        principal='slack:T:U',
        agent=ScriptedAgent(output=output),
        surface=surface,
        store=store,
    )
    return surface, store, result


async def test_text_reply_is_dispatched():
    surface, _, result = await _drive('hello')
    assert ('processing', None) in surface.calls
    assert ('text', 'hello') in surface.calls
    assert result == 'hello'


async def test_card_reply_is_dispatched_non_destructive():
    card = Card(title='t', sections=[Section(text='b')])
    surface, _, _ = await _drive(card)
    assert any(kind == 'card' and payload[0] is card and payload[1] is False for kind, payload in surface.calls)


async def test_carousel_reply_is_dispatched():
    cards = [Card(title='a'), Card(title='b')]
    surface, _, _ = await _drive(Carousel(cards=cards))
    assert ('carousel', cards) in surface.calls


async def test_pending_approval_parks_and_renders_destructive():
    card = Card(title='Delete everything?')
    surface, store, _ = await _drive(PendingApproval(card=card, state={'resume': 'x'}))
    assert any(kind == 'card' and payload[1] is True for kind, payload in surface.calls)
    assert await store.take_approval('slack:T:U') == {'resume': 'x'}


async def test_history_is_persisted():
    surface = RecordingSurface()
    store = MemoryStore()
    agent = ScriptedAgent(output='ok', new_history=[{'role': 'user'}, {'role': 'assistant'}])
    await run_turn('hi', principal='p', agent=agent, surface=surface, store=store)
    assert await store.load_history('p') == [{'role': 'user'}, {'role': 'assistant'}]
