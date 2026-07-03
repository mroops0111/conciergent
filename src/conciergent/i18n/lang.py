import enum
import typing


class Lang(enum.StrEnum):
    """A user-interface language, valued by its BCP 47 tag so it doubles as a catalog file stem."""

    EN = 'en'
    ZH_TW = 'zh-TW'

    @property
    def display_name(self) -> str:
        """The English name of the language, for telling the model which language to answer in."""
        return DISPLAY_NAMES[self]

    @classmethod
    @typing.override
    def _missing_(cls, value: object) -> typing.Self | None:
        # Surfaces report a BCP 47 tag that may carry a region (Slack sends en-US, LINE sends en),
        # so match case-insensitively and fall back to the primary subtag before giving up.
        if not isinstance(value, str):
            return None
        lowered = value.lower()
        by_value = {member.value.lower(): member for member in cls}
        return by_value.get(lowered) or by_value.get(lowered.split('-')[0])


DISPLAY_NAMES: dict[Lang, str] = {
    Lang.EN: 'English',
    Lang.ZH_TW: 'Traditional Chinese',
}


def parse_accept_language(header: str | None) -> Lang | None:
    """Pick the first understood language from an ``Accept-Language`` header, or None.

    For example ``zh-TW,zh;q=0.9,en;q=0.8`` resolves to ``Lang.ZH_TW``.
    """
    if not header:
        return None
    for token in header.split(','):
        try:
            return Lang(token.split(';', 1)[0].strip())
        except ValueError:
            continue
    return None
