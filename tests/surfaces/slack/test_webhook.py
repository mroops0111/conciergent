import collections.abc
import json

from tests.surfaces.slack.conftest import CHANNEL, SlackHarness


SignHeaders = collections.abc.Callable[[bytes], dict[str, str]]
BuildBody = collections.abc.Callable[..., bytes]


async def test_message_event_runs_a_turn_and_replies(
    harness: SlackHarness, sign_headers: SignHeaders, event_body: BuildBody
) -> None:
    await harness.install()
    body = event_body(text='hi there')

    response = await harness.client.post('/slack/events', content=body, headers=sign_headers(body))

    assert response.status_code == 200
    assert harness.agent.inputs == ['hi there']
    assert harness.posts and harness.posts[0][0] == CHANNEL


async def test_url_verification_answers_challenge(harness: SlackHarness, sign_headers: SignHeaders) -> None:
    body = json.dumps({'type': 'url_verification', 'challenge': 'c123'}).encode()

    response = await harness.client.post('/slack/events', content=body, headers=sign_headers(body))

    assert response.json() == {'challenge': 'c123'}


async def test_suggestion_interaction_runs_the_prompt(
    harness: SlackHarness, sign_headers: SignHeaders, interaction_body: BuildBody
) -> None:
    await harness.install()
    body = interaction_body(
        'suggestion:open:0:0', value='List more tasks', text='Tasks', response_url='https://example.com/response'
    )

    await harness.client.post('/slack/interactions', content=body, headers=sign_headers(body))

    assert harness.agent.inputs == ['List more tasks']
    assert harness.patches, 'the interacted message is patched to show processing'


async def test_bad_signature_is_rejected(
    harness: SlackHarness, sign_headers: SignHeaders, event_body: BuildBody
) -> None:
    body = event_body()
    headers = sign_headers(body)
    headers['X-Slack-Signature'] = 'v0=deadbeef'

    response = await harness.client.post('/slack/events', content=body, headers=headers)

    assert response.status_code == 401


async def test_duplicate_event_delivery_is_dropped(
    harness: SlackHarness, sign_headers: SignHeaders, event_body: BuildBody
) -> None:
    await harness.install()
    body = event_body(event_id='Ev-dup')

    await harness.client.post('/slack/events', content=body, headers=sign_headers(body))
    await harness.client.post('/slack/events', content=body, headers=sign_headers(body))

    assert harness.agent.inputs == ['hello']


async def test_bot_echo_is_ignored(harness: SlackHarness, sign_headers: SignHeaders, event_body: BuildBody) -> None:
    await harness.install()
    body = event_body(bot_id='B99')

    await harness.client.post('/slack/events', content=body, headers=sign_headers(body))

    assert harness.agent.inputs == []


async def test_uninstalled_team_is_ignored(
    harness: SlackHarness, sign_headers: SignHeaders, event_body: BuildBody
) -> None:
    body = event_body()

    await harness.client.post('/slack/events', content=body, headers=sign_headers(body))

    assert harness.agent.inputs == []


async def test_fallback_bot_token_serves_single_workspace(
    slack_app: collections.abc.Callable[..., collections.abc.Awaitable[SlackHarness]],
    sign_headers: SignHeaders,
    event_body: BuildBody,
) -> None:
    harness = await slack_app(fallback_bot_token='xoxb-static')
    body = event_body()

    await harness.client.post('/slack/events', content=body, headers=sign_headers(body))

    assert harness.agent.inputs == ['hello']


async def test_open_interaction_dedupes_per_action_id(
    harness: SlackHarness, sign_headers: SignHeaders, interaction_body: BuildBody
) -> None:
    await harness.install()
    bodies = [
        interaction_body('suggestion:open:0:0', value='refresh'),
        interaction_body('suggestion:open:0:0', value='refresh'),
        interaction_body('suggestion:open:0:1', value='refresh'),
    ]

    for body in bodies:
        await harness.client.post('/slack/interactions', content=body, headers=sign_headers(body))

    # An open button dedupes on re-click; a different button on the same message still dispatches.
    assert harness.agent.inputs == ['refresh', 'refresh']


async def test_exclusive_interaction_consumes_the_whole_message(
    harness: SlackHarness, sign_headers: SignHeaders, interaction_body: BuildBody
) -> None:
    await harness.install()
    first = interaction_body('suggestion:exclusive:0:0', value='pick 0')
    second = interaction_body('suggestion:exclusive:0:1', value='pick 1')

    await harness.client.post('/slack/interactions', content=first, headers=sign_headers(first))
    await harness.client.post('/slack/interactions', content=second, headers=sign_headers(second))

    assert harness.agent.inputs == ['pick 0']


async def test_threads_are_separate_conversations(
    harness: SlackHarness, sign_headers: SignHeaders, event_body: BuildBody
) -> None:
    await harness.install()
    first = event_body(event_id='EvT1', text='in thread one', thread_ts='100.1')
    second = event_body(event_id='EvT2', text='in thread two', thread_ts='200.2')

    await harness.client.post('/slack/events', content=first, headers=sign_headers(first))
    await harness.client.post('/slack/events', content=second, headers=sign_headers(second))

    assert await harness.message_store.load_history('slack:T1:U1:100.1') == [{'seen': 'in thread one'}]
    assert await harness.message_store.load_history('slack:T1:U1:200.2') == [{'seen': 'in thread two'}]
