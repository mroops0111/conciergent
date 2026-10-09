from tests.surfaces.slack.conftest import CHANNEL, MESSAGE_TS, BuildHarness, SlackHarness, event, interaction


async def test_message_event_runs_a_turn_and_replies(harness: SlackHarness) -> None:
    await harness.install()

    response = await harness.post_event(event(text='hi there'))

    assert response.status_code == 200
    assert harness.agent.inputs == ['hi there']
    assert harness.posts and harness.posts[0][0] == CHANNEL


async def test_url_verification_answers_challenge(harness: SlackHarness) -> None:
    response = await harness.post_event({'type': 'url_verification', 'challenge': 'c123'})

    assert response.json() == {'challenge': 'c123'}


async def test_suggestion_interaction_runs_the_prompt(harness: SlackHarness) -> None:
    await harness.install()

    await harness.post_interaction(
        interaction(
            'suggestion:open:0:0',
            value='List more tasks',
            message={'ts': MESSAGE_TS, 'text': 'Tasks'},
            response_url='https://example.com/response',
        )
    )

    assert harness.agent.inputs == ['List more tasks']
    assert harness.patches, 'the interacted message is patched to show processing'


async def test_bad_signature_is_rejected(harness: SlackHarness) -> None:
    response = await harness.post_event(event(), signature='v0=deadbeef')

    assert response.status_code == 401


async def test_duplicate_event_delivery_is_dropped(harness: SlackHarness) -> None:
    await harness.install()
    payload = event(event_id='Ev-dup')

    await harness.post_event(payload)
    await harness.post_event(payload)

    assert harness.agent.inputs == ['hello']


async def test_bot_echo_is_ignored(harness: SlackHarness) -> None:
    await harness.install()

    await harness.post_event(event(bot_id='B99'))

    assert harness.agent.inputs == []


async def test_uninstalled_team_is_ignored(harness: SlackHarness) -> None:
    await harness.post_event(event())

    assert harness.agent.inputs == []


async def test_fallback_bot_token_serves_single_workspace(slack_app: BuildHarness) -> None:
    harness = await slack_app(fallback_bot_token='xoxb-static')

    await harness.post_event(event())

    assert harness.agent.inputs == ['hello']


async def test_open_interaction_dedupes_per_action_id(harness: SlackHarness) -> None:
    await harness.install()

    for action_id in ('suggestion:open:0:0', 'suggestion:open:0:0', 'suggestion:open:0:1'):
        await harness.post_interaction(interaction(action_id, value='refresh'))

    # An open button dedupes on re-click; a different button on the same message still dispatches.
    assert harness.agent.inputs == ['refresh', 'refresh']


async def test_exclusive_interaction_consumes_the_whole_message(harness: SlackHarness) -> None:
    await harness.install()

    await harness.post_interaction(interaction('suggestion:exclusive:0:0', value='pick 0'))
    await harness.post_interaction(interaction('suggestion:exclusive:0:1', value='pick 1'))

    assert harness.agent.inputs == ['pick 0']


async def test_threads_are_separate_conversations(harness: SlackHarness) -> None:
    await harness.install()

    await harness.post_event(event(event_id='EvT1', text='in thread one', thread_ts='100.1'))
    await harness.post_event(event(event_id='EvT2', text='in thread two', thread_ts='200.2'))

    assert await harness.message_store.load_history('slack:T1:U1:100.1') == [{'seen': 'in thread one'}]
    assert await harness.message_store.load_history('slack:T1:U1:200.2') == [{'seen': 'in thread two'}]
