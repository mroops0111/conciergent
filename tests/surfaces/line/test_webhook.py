from conciergent import i18n
from tests.surfaces.line.conftest import LineHarness, follow_event, message_event


async def test_text_message_runs_a_turn_and_replies_with_the_token(harness: LineHarness) -> None:
    response = await harness.post(message_event('hi there'))

    assert response.status_code == 200
    assert harness.agent.inputs == ['hi there']
    assert harness.replies and harness.replies[0]['text'] == 'echo hi there'


async def test_follow_event_bootstraps_and_greets_a_returning_user(harness: LineHarness) -> None:
    await harness.post(follow_event())

    assert harness.agent.inputs == []
    assert harness.agent.bootstrapped == ['line:U1']
    assert harness.replies and harness.replies[0]['text'] == i18n.t('greeting.welcome_back', None)


async def test_follow_greets_ready_after_a_fresh_authorization(harness: LineHarness) -> None:
    harness.agent.bootstrap_result = True

    await harness.post(follow_event())

    assert harness.replies and harness.replies[0]['text'] == i18n.t('greeting.ready', None)


async def test_bad_signature_is_rejected(harness: LineHarness) -> None:
    response = await harness.post(message_event(), signature='bogus')

    assert response.status_code == 401


async def test_duplicate_delivery_is_dropped(harness: LineHarness) -> None:
    event = message_event(event_id='dup')

    await harness.post(event)
    await harness.post(event)

    assert harness.agent.inputs == ['hello']


async def test_non_text_messages_are_ignored(harness: LineHarness) -> None:
    await harness.post(message_event(event_id='ev-sticker', message={'type': 'sticker'}))

    assert harness.agent.inputs == []


async def test_id_less_events_each_dispatch(harness: LineHarness) -> None:
    # Without a webhookEventId there is nothing to dedupe on, so both deliveries run.
    await harness.post(message_event('no id', event_id=None), message_event('no id', event_id=None))

    assert harness.agent.inputs == ['no id', 'no id']
