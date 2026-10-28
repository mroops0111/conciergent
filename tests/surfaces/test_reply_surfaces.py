import typing

import pytest

from conciergent.reply import ReplySurface
from conciergent.surfaces.discord.surface import DiscordMessenger, DiscordReplySurface
from conciergent.surfaces.line.surface import LineReplySurface, ReplyTokenSlot
from conciergent.surfaces.slack.surface import SlackMessenger, SlackReplySurface


@pytest.mark.parametrize(
    'build',
    [
        lambda color: SlackReplySurface(typing.cast(SlackMessenger, None), channel='C1', brand_color=color),
        lambda color: LineReplySurface(typing.cast(ReplyTokenSlot, None), brand_color=color),
        lambda color: DiscordReplySurface(typing.cast(DiscordMessenger, None), channel_id='1', brand_color=color),
    ],
    ids=['slack', 'line', 'discord'],
)
def test_each_surface_exposes_the_color_it_draws_its_cards_in(build: typing.Callable[[str], ReplySurface]):
    """Reading the brand color sends nothing, so the surfaces need no working messenger."""
    assert build('#123456').brand_color == '#123456'
