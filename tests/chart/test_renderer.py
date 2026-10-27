import struct
import typing

import pytest

from conciergent.chart import ChartRenderer, ChartSpecError, DefaultChartStyle
from conciergent.defaults import defaults_layer


_FONT = defaults_layer()['capabilities']['charts']['font']


def _renderer(*, max_rows: int = 500) -> ChartRenderer:
    return ChartRenderer(DefaultChartStyle(font=_FONT), max_rows=max_rows)


def _spec(**overrides: typing.Any) -> dict[str, typing.Any]:
    return {
        'data': {'values': [{'day': 'Mon', 'count': 3}, {'day': 'Tue', 'count': 5}]},
        'mark': 'bar',
        'encoding': {
            'x': {'field': 'day', 'type': 'nominal'},
            'y': {'field': 'count', 'type': 'quantitative'},
        },
        **overrides,
    }


def _png_size(image: bytes) -> tuple[int, int]:
    assert image.startswith(b'\x89PNG\r\n\x1a\n')
    width, height = struct.unpack('>II', image[16:24])
    return width, height


def test_renders_a_png_at_the_hero_size():
    image = _renderer().render(_spec())

    # Twice the 520 by 338 canvas, which is LINE's 20:13 hero ratio.
    assert _png_size(image) == (1040, 676)


def test_the_size_holds_whatever_the_spec_asks_for():
    image = _renderer().render(_spec(width=50, height=900, padding=0, autosize='none', title='Ignored'))

    assert _png_size(image) == (1040, 676)


def test_the_accent_color_and_model_config_do_not_break_rendering():
    image = _renderer().render(_spec(config={'background': 'black'}), accent_color='#586af2')

    assert _png_size(image) == (1040, 676)


@pytest.mark.parametrize(
    'spec',
    [
        _spec(data={'url': 'https://example.com/data.json'}),
        # Vega-Lite fetches an image mark's url even when remote data is blocked.
        _spec(mark='image', encoding={'url': {'value': 'https://example.com/pixel.png'}}),
        _spec(layer=[{'data': {'url': 'http://169.254.169.254/'}, 'mark': 'bar'}]),
    ],
)
def test_refuses_any_url_in_the_spec(spec: dict[str, typing.Any]):
    with pytest.raises(ChartSpecError, match=r'data\.values'):
        _renderer().render(spec)


@pytest.mark.parametrize('data', [None, {}, {'values': []}, {'values': 'rows'}])
def test_refuses_a_spec_without_inline_rows(data: typing.Any):
    with pytest.raises(ChartSpecError, match=r'data\.values'):
        _renderer().render(_spec(data=data))


def test_refuses_more_rows_than_the_limit():
    rows = [{'day': str(index), 'count': index} for index in range(4)]

    with pytest.raises(ChartSpecError, match='at most 3'):
        _renderer(max_rows=3).render(_spec(data={'values': rows}))


def test_refuses_a_spec_that_is_not_an_object():
    with pytest.raises(ChartSpecError, match='JSON object'):
        _renderer().render(['not', 'a', 'spec'])


def test_reports_a_spec_vega_lite_cannot_compile():
    with pytest.raises(ChartSpecError, match='Vega-Lite could not render'):
        _renderer().render(_spec(mark='no-such-mark'))


def test_does_not_modify_the_given_spec():
    spec = _spec(title='Kept on the caller side')

    _renderer().render(spec)

    assert spec['title'] == 'Kept on the caller side'
    assert 'width' not in spec


class _SquareStyle(DefaultChartStyle):
    @typing.override
    def canvas(self) -> dict[str, typing.Any]:
        return super().canvas() | {'width': 300, 'height': 300}


def test_a_style_subclass_sets_the_canvas_without_touching_the_renderer():
    image = ChartRenderer(_SquareStyle(font=_FONT), max_rows=500).render(_spec())

    assert _png_size(image) == (600, 600)
