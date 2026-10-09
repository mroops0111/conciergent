import collections.abc
import pathlib
import typing

import pytest
import yaml

from conciergent.config import build_app_config, yaml_layer
from conciergent.defaults import DEFAULTS


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
    # The manifest that `conciergent init` scaffolds must stay loadable and show the shipped defaults, guarding against config drift.
    monkeypatch.setenv('SLACK_SIGNING_SECRET', 'dummy')
    manifest = pathlib.Path(__file__).parents[1] / 'src' / 'conciergent' / 'templates' / 'manifest.yml'

    config = build_app_config(yaml_layer(manifest))

    assert config.surface.slack.enabled is True
    assert config.store.messages_url and config.store.credentials_url
    assert (config.server.host, config.server.port) == (DEFAULTS.server.host, DEFAULTS.server.port)


def _groups_config(spec_auth: dict[str, typing.Any] | None) -> dict[str, typing.Any]:
    return {
        **_MINIMAL_CONFIG,
        'surface': {
            'line': {
                'enabled': True,
                'channel_secret': 's',
                'channel_access_token': 't',
                'groups': {'enabled': True, 'allowed': ['C1']},
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
def test_groups_accept_specs_with_a_shared_identity(auth: dict[str, typing.Any] | None):
    config = build_app_config(_groups_config(auth))

    assert config.surface.line.groups.policy().admits('C1')
    assert config.surface.line.groups.reply_to == 'mention'


@pytest.mark.parametrize('auth', [{'type': 'oauth2'}, {'type': 'oauth2', 'flow': 'authorization_code'}])
def test_groups_reject_a_per_user_oauth_spec(auth: dict[str, typing.Any]):
    with pytest.raises(ValueError, match='per-user oauth2: api'):
        build_app_config(_groups_config(auth))


def test_a_per_user_spec_is_fine_while_groups_are_off():
    config = _groups_config({'type': 'oauth2'})
    config['surface']['line']['groups']['enabled'] = False

    assert build_app_config(config).gateway.specs[0].needs_user_authorization is True
