import os
import pathlib
import re
import typing

import yaml


# Loading and merging layers needs no config models, so the push command can read its socket path without them.


def yaml_layer(path: str | pathlib.Path) -> dict[str, typing.Any]:
    """Load one YAML config layer, resolving ``${ENV_VAR}`` and ``${ENV_VAR:-default}`` references."""
    raw = yaml.safe_load(pathlib.Path(path).expanduser().read_text()) or {}
    return _resolve_env(raw)


_ENV_PATTERN = re.compile(r'\$\{(\w+)(?::-(.*))?\}')


def _resolve_env(value: typing.Any) -> typing.Any:
    if isinstance(value, dict):
        return {key: _resolve_env(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_resolve_env(item) for item in value]
    if isinstance(value, str):
        match = _ENV_PATTERN.fullmatch(value)
        if match:
            env_value = os.environ.get(match.group(1))
            if env_value is not None:
                return env_value
            return match.group(2) if match.group(2) is not None else ''
    return value


def deep_merge(base: dict[str, typing.Any], override: dict[str, typing.Any]) -> dict[str, typing.Any]:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged
