import enum


class ChatSurface(enum.StrEnum):
    """A supported chat surface."""

    slack = 'slack'
    line = 'line'


def make_principal(surface: ChatSurface | str, *parts: str) -> str:
    """Build a stable principal that uniquely identifies a user on a surface.

    For example, ``make_principal(ChatSurface.slack, team_id, user_id)`` yields ``'slack:T1:U1'``.
    """
    surface_value = surface.value if isinstance(surface, ChatSurface) else surface
    return ':'.join([surface_value, *parts])


def parse_principal(principal: str) -> tuple[str, tuple[str, ...]]:
    """Split a principal back into its surface and its remaining parts."""
    surface_value, _, rest = principal.partition(':')
    parts = tuple(rest.split(':')) if rest else ()
    return surface_value, parts
