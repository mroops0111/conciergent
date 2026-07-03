import asyncio

import pytest

from conciergent.runtime import OAuthHandoffExpiredError, StatefulOAuthBridge
from conciergent.store.message import MessageStore


class RecordingBridge(StatefulOAuthBridge):
    def __init__(self, message_store: MessageStore, *, wait_timeout_seconds: float = 5.0) -> None:
        super().__init__(message_store, wait_timeout_seconds=wait_timeout_seconds)
        self.rendered: list[str] = []

    async def _render_authorization_ui(self, authorize_url: str) -> None:
        self.rendered.append(authorize_url)


async def test_code_round_trips_through_the_store(message_store: MessageStore):
    bridge = RecordingBridge(message_store)

    async def user_authorizes() -> None:
        await asyncio.sleep(0)
        await message_store.deliver_oauth_code('abc', 'the-code')

    task = asyncio.create_task(user_authorizes())
    code = await bridge.request_authorization('https://example.com/authorize?state=abc')
    await task
    assert code == 'the-code'
    assert bridge.rendered == ['https://example.com/authorize?state=abc']


async def test_missing_state_is_rejected(message_store: MessageStore):
    bridge = RecordingBridge(message_store)
    with pytest.raises(ValueError, match='state'):
        await bridge.request_authorization('https://example.com/authorize')


async def test_timeout_raises_expiry(message_store: MessageStore):
    bridge = RecordingBridge(message_store, wait_timeout_seconds=0.01)
    with pytest.raises(OAuthHandoffExpiredError):
        await bridge.request_authorization('https://example.com/authorize?state=zzz')


def test_handoff_expiry_detection_unwraps_groups():
    from conciergent.runtime import is_handoff_expiry

    plain = OAuthHandoffExpiredError()
    assert is_handoff_expiry(plain)
    assert is_handoff_expiry(ExceptionGroup('g', [plain]))
    assert is_handoff_expiry(ExceptionGroup('g', [ExceptionGroup('inner', [plain])]))
    assert not is_handoff_expiry(RuntimeError('boom'))
    assert not is_handoff_expiry(ExceptionGroup('g', [plain, RuntimeError('boom')]))
