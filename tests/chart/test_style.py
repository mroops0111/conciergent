from conciergent.chart import DefaultChartStyle
from tests.conftest import CHART_FONT, chart_spec


_STYLE = DefaultChartStyle(font=CHART_FONT)


def test_the_canvas_replaces_the_models_own_styling():
    spec = chart_spec(config={'background': 'black'}, title='Ignored', width=50, padding=0, background='black')

    styled = _STYLE.apply(spec)

    assert 'config' not in styled and 'title' not in styled
    assert {key: styled[key] for key in _STYLE.canvas()} == _STYLE.canvas()


def test_what_to_draw_is_kept():
    spec = chart_spec(transform=[{'filter': 'datum.count > 3'}])

    styled = _STYLE.apply(spec)

    for key in ('data', 'mark', 'encoding', 'transform'):
        assert styled[key] == spec[key]


def test_the_brand_color_leads_the_palette():
    config = _STYLE.config('#123456')

    assert config['range']['category'][0] == '#123456'
    assert config['mark']['color'] == '#123456'


def test_without_a_brand_color_the_palette_is_tableau_10():
    config = _STYLE.config(None)

    assert config['range']['category'][0] == '#4e79a7'
    assert len(config['range']['category']) == 10


def test_every_text_uses_the_font():
    config = _STYLE.config(None)

    assert config['font'] == CHART_FONT
    for section in ('axis', 'legend'):
        assert config[section]['labelFont'] == CHART_FONT
        assert config[section]['titleFont'] == CHART_FONT
