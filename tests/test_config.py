import pathlib

import pytest

from conciergent.config import build_app_config, yaml_layer


_STORE = {'messages_url': 'redis://localhost:6379/0', 'credentials_url': 'postgresql+asyncpg://localhost/db'}

_MINIMAL = """\
agent:
  model: gemini-3-flash
  system_prompt: be helpful
store:
  messages_url: redis://localhost:6379/0
  credentials_url: postgresql+asyncpg://localhost/db
"""


def test_minimal_config_validates(tmp_path: pathlib.Path):
    path = tmp_path / 'conciergent.yml'
    path.write_text(_MINIMAL)
    config = build_app_config(yaml_layer(path))
    assert config.agent.model == 'gemini-3-flash'
    assert config.slack is None
    assert config.server.url == 'http://127.0.0.1:8000'


def test_env_references_resolve(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv('TEST_SIGNING', 'sek')
    path = tmp_path / 'conciergent.yml'
    path.write_text(
        _MINIMAL
        + """\
slack:
  signing_secret: ${TEST_SIGNING}
  bot_token: ${TEST_MISSING:-fallback-token}
"""
    )
    config = build_app_config(yaml_layer(path))
    assert config.slack is not None
    assert config.slack.signing_secret == 'sek'
    assert config.slack.bot_token == 'fallback-token'


def test_later_layers_win(tmp_path: pathlib.Path):
    path = tmp_path / 'conciergent.yml'
    path.write_text(_MINIMAL)
    config = build_app_config(yaml_layer(path), {'server': {'port': 9999}})
    assert config.server.port == 9999
    assert config.server.host == '127.0.0.1'


def test_store_requires_both_urls():
    with pytest.raises(ValueError, match='credentials_url'):
        build_app_config({'agent': {'model': 'm', 'system_prompt': 'p'}, 'store': {'messages_url': 'redis://x'}})


def test_empty_secret_fails_fast():
    # An unset env var resolves to an empty string, which must not silently become a forgeable secret.
    with pytest.raises(ValueError):
        build_app_config(
            {'agent': {'model': 'm', 'system_prompt': 'p'}, 'store': _STORE, 'slack': {'signing_secret': ''}}
        )
    with pytest.raises(ValueError):
        build_app_config(
            {
                'agent': {'model': 'm', 'system_prompt': 'p'},
                'store': _STORE,
                'line': {'channel_secret': '', 'channel_access_token': 't'},
            }
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
            'agent': {'model': 'm', 'system_prompt': 'p'},
            'slack': {'signing_secret': 's', 'brand_color': '#123456'},
            'line': {'channel_secret': 'cs', 'channel_access_token': 't'},
            'conversation': {'approval_ttl_seconds': 900},
            'store': {**_STORE, 'max_turns': 20},
        }
    )
    # An unset knob resolves to the real value from defaults.yml, not an empty sentinel.
    assert config.agent.mcp_read_timeout_seconds == 120.0
    assert config.slack is not None and config.slack.brand_color == '#123456'
    assert config.slack.destructive_color == '#DC3545'
    assert config.slack.api_timeout_seconds == 30.0
    assert config.line is not None and config.line.brand_color == '#586af2'
    assert config.conversation.approval_ttl_seconds == 900
    assert config.conversation.history_ttl_seconds == 604800
    assert config.conversation.oauth_wait_timeout_seconds == 240.0
    assert config.store.max_turns == 20
