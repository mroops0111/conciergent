import collections.abc
import typing

from conciergent import i18n
from tests.surfaces.line.conftest import LineHarness


SignHeaders = collections.abc.Callable[[bytes], dict[str, str]]
BuildEvent = collections.abc.Callable[..., dict[str, typing.Any]]
BuildBody = collections.abc.Callable[..., bytes]


async def test_text_message_runs_a_turn_and_replies_with_the_token(
    harness: LineHarness, sign_headers: SignHeaders, message_event: BuildEvent, line_body: BuildBody
) -> None:
    body = line_body(message_event(text='hi there'))

    response = await harness.client.post('/line/events', content=body, headers=sign_headers(body))

    assert response.status_code == 200
    assert harness.agent.inputs == ['hi there']
    assert harness.replies and harness.replies[0]['text'] == 'echo hi there'


async def test_follow_event_bootstraps_and_greets_a_returning_user(
    harness: LineHarness, sign_headers: SignHeaders, follow_event: BuildEvent, line_body: BuildBody
) -> None:
    body = line_body(follow_event())

    await harness.client.post('/line/events', content=body, headers=sign_headers(body))

    assert harness.agent.inputs == []
    assert harness.agent.bootstrapped == ['line:U1']
    assert harness.replies and harness.replies[0]['text'] == i18n.t('follow.welcome_back', None)


async def test_follow_greets_ready_after_a_fresh_authorization(
    harness: LineHarness, sign_headers: SignHeaders, follow_event: BuildEvent, line_body: BuildBody
) -> None:
    harness.agent.bootstrap_result = True
    body = line_body(follow_event())

    await harness.client.post('/line/events', content=body, headers=sign_headers(body))

    assert harness.replies and harness.replies[0]['text'] == i18n.t('follow.ready', None)


async def test_bad_signature_is_rejected(harness: LineHarness, message_event: BuildEvent, line_body: BuildBody) -> None:
    body = line_body(message_event())

    response = await harness.client.post('/line/events', content=body, headers={'X-Line-Signature': 'bogus'})

    assert response.status_code == 401


async def test_duplicate_delivery_is_dropped(
    harness: LineHarness, sign_headers: SignHeaders, message_event: BuildEvent, line_body: BuildBody
) -> None:
    body = line_body(message_event(event_id='dup'))

    await harness.client.post('/line/events', content=body, headers=sign_headers(body))
    await harness.client.post('/line/events', content=body, headers=sign_headers(body))

    assert harness.agent.inputs == ['hello']


async def test_non_text_messages_are_ignored(
    harness: LineHarness, sign_headers: SignHeaders, message_event: BuildEvent, line_body: BuildBody
) -> None:
    body = line_body(message_event(event_id='ev-sticker', message={'type': 'sticker'}))

    await harness.client.post('/line/events', content=body, headers=sign_headers(body))

    assert harness.agent.inputs == []


async def test_id_less_events_each_dispatch(
    harness: LineHarness, sign_headers: SignHeaders, message_event: BuildEvent, line_body: BuildBody
) -> None:
    # Without a webhookEventId there is nothing to dedupe on, so both deliveries run.
    body = line_body(message_event(event_id=None, text='no id'), message_event(event_id=None, text='no id'))

    await harness.client.post('/line/events', content=body, headers=sign_headers(body))

    assert harness.agent.inputs == ['no id', 'no id']
