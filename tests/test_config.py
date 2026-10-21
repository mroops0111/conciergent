import collections.abc
import pathlib
import typing

import pytest
import yaml

from conciergent.config import GatewaySpec, build_app_config, yaml_layer
from conciergent.defaults import DEFAULTS
from conciergent.group_chat import GroupChatPolicy


_STORE = {'messages_url': 'redis://localhost:6379/0', 'credentials_url': 'postgresql+asyncpg://localhost/db'}
_MINIMAL_CONFIG = {'agent': {'model': 'gpt-4o-mini', 'system_prompt': 'be helpful'}, 'store': _STORE}


@pytest.fixture
def write_config(tmp_path: pathlib.Path) -> collections.abc.Callable[[dict[str, typing.Any]], pathlib.Path]:
    def _write(config: dict[str, typing.Any]) -> pathlib.Path:
        path = tmp_path / 'manifest.yml'
        path.write_text(yaml.safe_dump(config))
        return path

    return _write


def test_minimal_config_validates(write_config: collections.abc.Callable[[dict[str, typing.Any]], pathlib.Path]):
    config = build_app_config(yaml_layer(write_config(_MINIMAL_CONFIG)))

    assert config.agent.model == 'gpt-4o-mini'
    assert config.surface.slack.enabled is False
    assert config.server.url == 'http://127.0.0.1:8000'


def test_env_references_resolve(
    write_config: collections.abc.Callable[[dict[str, typing.Any]], pathlib.Path], monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv('TEST_SIGNING', 'sek')
    path = write_config(
        {
            **_MINIMAL_CONFIG,
            'surface': {
                'slack': {
                    'enabled': True,
                    'signing_secret': '${TEST_SIGNING}',
                    'bot_token': '${TEST_MISSING:-fallback-token}',
                },
            },
        }
    )

    config = build_app_config(yaml_layer(path))

    assert config.surface.slack.enabled is True
    assert config.surface.slack.signing_secret == 'sek'
    assert config.surface.slack.bot_token == 'fallback-token'


def test_later_layers_win(write_config: collections.abc.Callable[[dict[str, typing.Any]], pathlib.Path]):
    config = build_app_config(yaml_layer(write_config(_MINIMAL_CONFIG)), {'server': {'port': 9999}})

    assert config.server.port == 9999
    assert config.server.host == '127.0.0.1'


def test_store_requires_both_urls():
    with pytest.raises(ValueError, match='credentials_url'):
        build_app_config({'agent': {'model': 'm', 'system_prompt': 'p'}, 'store': {'messages_url': 'redis://x'}})


def test_enabled_surface_requires_its_secret():
    # An empty secret would make every webhook signature forgeable, so an enabled surface must set one.
    with pytest.raises(ValueError):
        build_app_config({'store': _STORE, 'surface': {'slack': {'enabled': True, 'signing_secret': ''}}})
    with pytest.raises(ValueError):
        build_app_config(
            {'store': _STORE, 'surface': {'line': {'enabled': True, 'channel_secret': '', 'channel_access_token': 't'}}}
        )


def test_gateway_specs_parse():
    config = build_app_config(
        {
            'agent': {'model': 'm', 'system_prompt': 'p'},
            'store': _STORE,
            'gateway': {'specs': [{'name': 'petstore', 'spec': './petstore.json'}]},
        }
    )

    assert config.gateway is not None
    assert config.gateway.specs[0].name == 'petstore'


def test_non_text_knobs_parse_and_default_to_the_shipped_values():
    config = build_app_config(
        {
            'surface': {
                'slack': {'enabled': True, 'signing_secret': 's', 'brand_color': '#123456'},
                'line': {'enabled': True, 'channel_secret': 'cs', 'channel_access_token': 't'},
            },
            'conversation': {'approval_ttl_seconds': 900},
            'store': {**_STORE, 'max_turns': 20},
        }
    )

    # An unset knob resolves to the real value from defaults.yml, not an empty sentinel.
    assert config.agent.mcp_read_timeout_seconds == 300.0
    assert config.surface.slack.brand_color == '#123456'
    assert config.surface.slack.destructive_color == '#DC3545'
    assert config.surface.slack.api_timeout_seconds == 30.0
    assert config.surface.line.brand_color == '#586af2'
    assert config.conversation.approval_ttl_seconds == 900
    assert config.conversation.history_ttl_seconds == 604800
    assert config.conversation.oauth_wait_timeout_seconds == 240.0
    assert config.store.max_turns == 20


def test_scaffolded_manifest_validates(monkeypatch: pytest.MonkeyPatch):
    # The manifest that `conciergent init` scaffolds must stay loadable and show the shipped defaults,
    # guarding against config drift.
    monkeypatch.setenv('SLACK_SIGNING_SECRET', 'dummy')
    manifest = pathlib.Path(__file__).parents[1] / 'src' / 'conciergent' / 'templates' / 'manifest.yml'

    config = build_app_config(yaml_layer(manifest))

    assert config.surface.slack.enabled is True
    assert config.store.messages_url and config.store.credentials_url
    assert (config.server.host, config.server.port) == (DEFAULTS.server.host, DEFAULTS.server.port)


def _group_chats_config(spec_auth: dict[str, typing.Any] | None) -> dict[str, typing.Any]:
    return {
        **_MINIMAL_CONFIG,
        'surface': {
            'line': {
                'enabled': True,
                'channel_secret': 's',
                'channel_access_token': 't',
                'group_chats': {'enabled': True},
            }
        },
        'gateway': {
            'enabled': True,
            'redis_url': 'redis://localhost:6379/0',
            'specs': [{'name': 'api', 'spec': './api.json', 'auth': spec_auth}],
        },
    }


@pytest.mark.parametrize(
    'auth',
    [
        None,
        {'type': 'bearer', 'token': 't'},
        {'type': 'api_key', 'token': 't'},
        {'type': 'oauth2', 'flow': 'client_credentials'},
    ],
)
def test_group_chats_accept_specs_with_a_shared_identity(auth: dict[str, typing.Any] | None):
    config = build_app_config(_group_chats_config(auth))

    assert config.surface.line.group_chats.policy() == GroupChatPolicy(enabled=True, reply_trigger='mention')


@pytest.mark.parametrize('auth', [{'type': 'oauth2'}, {'type': 'oauth2', 'flow': 'authorization_code'}])
def test_group_chats_reject_a_per_user_oauth_spec(auth: dict[str, typing.Any]):
    with pytest.raises(ValueError, match='per-user oauth2: api'):
        build_app_config(_group_chats_config(auth))


def test_a_per_user_spec_is_fine_while_group_chats_are_off():
    config = _group_chats_config({'type': 'oauth2'})
    config['surface']['line']['group_chats']['enabled'] = False

    assert build_app_config(config).gateway.specs[0].needs_user_authorization is True


def test_gateway_spec_exposure_shorthand_and_server_config():
    shorthand = GatewaySpec.model_validate(
        {'name': 'api', 'spec': 'https://example.com/openapi.json', 'exposure': 'dynamic'}
    )
    assert shorthand.exposure == {'style': 'dynamic'}

    full = GatewaySpec.model_validate(
        {
            'name': 'api',
            'spec': 'https://example.com/openapi.json',
            'auth': {'type': 'bearer', 'token': 't'},
            'operations': {'listPets': {'name': 'list_pets'}},
            'instructions': 'Pets.',
        }
    )
    assert full.server_config() == {
        'name': 'api',
        'spec': 'https://example.com/openapi.json',
        'auth': {'type': 'bearer', 'token': 't'},
        'operations': {'listPets': {'name': 'list_pets'}},
        'instructions': 'Pets.',
    }


def _scheduled_config(*schedules: dict[str, typing.Any], line_enabled: bool = True) -> dict[str, typing.Any]:
    return {
        **_MINIMAL_CONFIG,
        'surface': {'line': {'enabled': line_enabled, 'channel_secret': 's', 'channel_access_token': 't'}},
        'schedules': list(schedules),
    }


def _schedule(**overrides: typing.Any) -> dict[str, typing.Any]:
    return {
        'name': 'daily-digest',
        'cron': '0 21 * * *',
        'timezone': 'Asia/Taipei',
        'prompt': 'Summarize today.',
        'target': {'surface': 'line', 'to': 'C1'},
        **overrides,
    }


def test_schedules_parse_with_safe_defaults():
    (schedule,) = build_app_config(_scheduled_config(_schedule())).schedules

    assert (schedule.allow_skip, schedule.allow_destructive) == (False, False)
    assert schedule.principal is None and schedule.failure_message is None


def test_schedules_default_to_none_and_accept_a_bare_key():
    assert build_app_config(_MINIMAL_CONFIG).schedules == []
    assert build_app_config({**_MINIMAL_CONFIG, 'schedules': None}).schedules == []


def test_a_schedule_target_resolves_from_the_environment(
    monkeypatch: pytest.MonkeyPatch, write_config: collections.abc.Callable[[dict[str, typing.Any]], pathlib.Path]
):
    monkeypatch.setenv('LINE_NOTIFY_TARGET', 'C42')
    path = write_config(_scheduled_config(_schedule(target={'surface': 'line', 'to': '${LINE_NOTIFY_TARGET}'})))

    assert build_app_config(yaml_layer(path)).schedules[0].target.to == 'C42'


@pytest.mark.parametrize(
    ('overrides', 'message'),
    [
        ({'cron': 'every day'}, 'not a valid cron expression'),
        ({'timezone': 'Asia/Nowhere'}, 'not a known IANA timezone'),
        ({'target': {'surface': 'line', 'to': ''}}, 'at least 1 character'),
        ({'target': {'surface': 'line', 'to': 'X1'}}, 'schedule daily-digest: .*starting with U, C, or R'),
        ({'target': {'surface': 'teams', 'to': 'C1'}}, "Input should be 'slack', 'line' or 'discord'"),
        ({'misfire_grace_seconds': -1}, 'greater than or equal to 0'),
    ],
)
def test_a_malformed_schedule_is_rejected(overrides: dict[str, typing.Any], message: str):
    with pytest.raises(ValueError, match=message):
        build_app_config(_scheduled_config(_schedule(**overrides)))


def test_schedule_names_must_be_unique():
    with pytest.raises(ValueError, match='repeated: daily-digest'):
        build_app_config(_scheduled_config(_schedule(), _schedule()))


def test_a_schedule_needs_its_target_surface_enabled():
    with pytest.raises(ValueError, match='targets line, which is not enabled'):
        build_app_config(_scheduled_config(_schedule(), line_enabled=False))


def test_a_schedule_without_a_principal_rejects_a_per_user_spec():
    config = _group_chats_config({'type': 'oauth2'})
    config['surface']['line']['group_chats']['enabled'] = False
    config['schedules'] = [_schedule()]

    with pytest.raises(ValueError, match=r'schedules without a principal \(daily-digest\).*per-user oauth2: api'):
        build_app_config(config)

    config['schedules'] = [_schedule(principal='line:U1')]
    assert build_app_config(config).schedules[0].principal == 'line:U1'


def test_a_schedule_needs_a_surface_that_can_push():
    config = _scheduled_config(_schedule(target={'surface': 'slack', 'to': 'C1'}))
    config['surface']['slack'] = {'enabled': True, 'signing_secret': 's'}

    with pytest.raises(ValueError, match='targets slack, which cannot push messages yet'):
        build_app_config(config)
