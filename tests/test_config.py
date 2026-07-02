import pathlib

import pytest

from conciergent.config import build_app_config, yaml_layer


_MINIMAL = """\
agent:
  model: gemini-3-flash
  system_prompt: be helpful
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
