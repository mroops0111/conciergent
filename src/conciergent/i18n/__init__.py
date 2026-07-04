import collections.abc
import importlib.resources
import importlib.resources.abc
import pathlib
import typing

import yaml

from conciergent.i18n.lang import Lang


# A locale directory is either a package resource (the shipped catalog) or a filesystem path (overrides).
LocaleDir = importlib.resources.abc.Traversable | pathlib.Path


# Any key a translation omits is served from English instead of failing.
FALLBACK_LANG = Lang.EN


def _flatten(data: dict[str, typing.Any], prefix: str = '') -> dict[str, str]:
    """Turn a nested locale mapping into flat dotted keys, so ``approval: {header: ...}`` becomes ``approval.header``."""
    flat: dict[str, str] = {}
    for key, value in data.items():
        full_key = f'{prefix}.{key}' if prefix else key
        if isinstance(value, dict):
            flat.update(_flatten(value, full_key))
        else:
            flat[full_key] = value
    return flat


def _merge_dir(catalog: dict[str, dict[Lang, str]], directory: LocaleDir) -> None:
    """Merge every ``{lang}.yml`` in one directory into the catalog, later directories overriding earlier ones."""
    for entry in directory.iterdir():
        if not entry.name.endswith('.yml'):
            continue
        try:
            lang = Lang(entry.name[: -len('.yml')])
        except ValueError:
            # A file whose stem is not a known language code is not a locale, skip it.
            continue
        loaded = yaml.safe_load(entry.read_text(encoding='utf-8')) or {}
        for key, value in _flatten(loaded).items():
            catalog.setdefault(key, {})[lang] = value


def _load_catalog(extra_dirs: collections.abc.Iterable[LocaleDir] = ()) -> dict[str, dict[Lang, str]]:
    catalog: dict[str, dict[Lang, str]] = {}
    _merge_dir(catalog, importlib.resources.files('conciergent.i18n').joinpath('locales'))
    for directory in extra_dirs:
        _merge_dir(catalog, directory)
    return catalog


CATALOG: dict[str, dict[Lang, str]] = _load_catalog()


def load_overrides(directories: collections.abc.Iterable[LocaleDir]) -> None:
    """Replace the catalog with the shipped one plus the given override directories layered on top.

    An app-builder points config at a locale directory to rebrand or add languages,
    and its files override matching keys per language while leaving everything else on the shipped defaults.
    """
    # Swap the contents in place instead of rebinding, so every reader keeps the same object.
    fresh = _load_catalog(directories)
    CATALOG.clear()
    CATALOG.update(fresh)


def t(key: str, lang: Lang | None, *, default: str | None = None, **kwargs: object) -> str:
    """Look up a UI string by dotted key in the user's language, falling back to English then ``default``.

    ``kwargs`` are substituted into the string with ``str.format``,
    so ``t('approval.body', lang, tools=names)`` fills a ``{tools}`` placeholder.
    """
    entry = CATALOG.get(key)
    if entry is None:
        if default is None:
            raise KeyError(key)
        return default.format(**kwargs) if kwargs else default
    text = (entry.get(lang) if lang is not None else None) or entry[FALLBACK_LANG]
    return text.format(**kwargs) if kwargs else text
