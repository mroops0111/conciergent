import typing

import httpx

from conciergent import Card, Section, Suggestion
from conciergent.store.message import MessageStore
from conciergent.surfaces.line.surface import LineOAuthBridge, LineReplySurface, ReplyTokenSlot


class FakeMessenger:
    def __init__(self, *, reply_fails: bool = False) -> None:
        self.reply_fails = reply_fails
        self.replies: list[dict[str, typing.Any]] = []
        self.pushes: list[dict[str, typing.Any]] = []
        self.loading_started = 0

    async def reply(self, reply_token: str, message: dict[str, typing.Any]) -> None:
        if self.reply_fails:
            request = httpx.Request('POST', 'https://example.com/v2/bot/message/reply')
            raise httpx.HTTPStatusError('expired', request=request, response=httpx.Response(400, request=request))
        self.replies.append(message)

    async def push(self, user_id: str, message: dict[str, typing.Any]) -> None:
        self.pushes.append(message)

    async def start_loading(self, user_id: str) -> None:
        self.loading_started += 1


def _slot(messenger: FakeMessenger, *, reply_token: str | None = 'tok') -> ReplyTokenSlot:
    return ReplyTokenSlot(typing.cast(typing.Any, messenger), user_id='U1', reply_token=reply_token)


async def test_first_send_uses_reply_then_push():
    messenger = FakeMessenger()
    slot = _slot(messenger)

    await slot.send({'type': 'text', 'text': 'one'})
    await slot.send({'type': 'text', 'text': 'two'})

    assert [m['text'] for m in messenger.replies] == ['one']
    assert [m['text'] for m in messenger.pushes] == ['two']


async def test_failed_reply_falls_back_to_push():
    messenger = FakeMessenger(reply_fails=True)
    slot = _slot(messenger)

    await slot.send({'type': 'text', 'text': 'one'})

    assert messenger.replies == []
    assert [m['text'] for m in messenger.pushes] == ['one']


async def test_without_token_everything_pushes():
    messenger = FakeMessenger()
    slot = _slot(messenger, reply_token=None)

    await slot.send({'type': 'text', 'text': 'one'})

    assert messenger.replies == []
    assert len(messenger.pushes) == 1


async def test_card_with_suggestions_gets_quick_reply_chips():
    messenger = FakeMessenger()
    surface = LineReplySurface(_slot(messenger))

    await surface.send_card(
        Card(header='T', sections=[Section(text='b')], suggestions=[Suggestion(label='More', prompt='more')])
    )

    message = messenger.replies[0]
    assert message['type'] == 'flex'
    assert message['quickReply']['items'][0]['action']['text'] == 'more'


async def test_destructive_card_has_no_chips():
    messenger = FakeMessenger()
    surface = LineReplySurface(_slot(messenger))

    await surface.send_card(
        Card(header='T', sections=[Section(text='b')], suggestions=[Suggestion(label='Yes', prompt='Yes')]),
        destructive=True,
    )

    assert 'quickReply' not in messenger.replies[0]


async def test_text_is_sent_as_one_message():
    messenger = FakeMessenger()
    surface = LineReplySurface(_slot(messenger))

    await surface.send_text('x' * 5001)

    sent = [*messenger.replies, *messenger.pushes]
    assert [len(message['text']) for message in sent] == [5001]


async def test_processing_failure_is_swallowed():
    class ExplodingMessenger(FakeMessenger):
        async def start_loading(self, user_id: str) -> None:
            raise RuntimeError('nope')

    surface = LineReplySurface(_slot(ExplodingMessenger()))

    await surface.show_processing()


async def test_oauth_bridge_renders_a_link_bubble(message_store: MessageStore):
    state = 's1'
    authorize_url = f'https://example.com/authorize?state={state}'
    messenger = FakeMessenger()
    bridge = LineOAuthBridge(message_store, _slot(messenger))
    await message_store.deliver_oauth_code(state, 'code-1')

    code = await bridge.request_authorization(authorize_url)

    assert code == 'code-1'
    rendered = messenger.replies[0]
    assert rendered['type'] == 'flex'
    button = rendered['contents']['footer']['contents'][0]
    assert button['action']['uri'] == authorize_url
