import abc
import typing


# The keys a style owns, so whatever the model sets for these is dropped before rendering.
_STYLE_KEYS = ('$schema', 'config', 'background', 'padding', 'autosize', 'width', 'height', 'title', 'usermeta')


class ChartStyle(abc.ABC):
    """How every chart looks, applied over a spec that says only what to draw.

    The model supplies the data, marks, and encodings.
    The style supplies the canvas and the Vega-Lite config, so every chart shares one look.
    """

    @property
    def scale(self) -> int:
        """The pixel ratio a chart is rendered at, two for sharp text on high-density screens."""
        return 2

    def apply(self, spec: dict[str, typing.Any]) -> dict[str, typing.Any]:
        """Return ``spec`` with the model's own styling dropped and this style's canvas set."""
        return {key: value for key, value in spec.items() if key not in _STYLE_KEYS} | self.canvas()

    @abc.abstractmethod
    def canvas(self) -> dict[str, typing.Any]:
        """The top-level Vega-Lite keys that set the size, padding, autosize, and background."""
        ...

    @abc.abstractmethod
    def config(self, brand_color: str | None) -> dict[str, typing.Any]:
        """The Vega-Lite config, drawing ``brand_color`` first when one is given."""
        ...


# The canvas matches LINE's 20:13 hero, so a chart fills a card's hero image on every surface without being cropped.
_WIDTH = 520
_HEIGHT = 338
_PADDING = 24
_BACKGROUND = '#ffffff'
_TEXT_COLOR = '#333333'
_GRID_COLOR = '#e6e6e6'
# Tableau 10, a palette that stays distinguishable for the common forms of color blindness.
_CATEGORY_COLORS = (
    '#4e79a7',
    '#f28e2c',
    '#e15759',
    '#76b7b2',
    '#59a14f',
    '#edc949',
    '#af7aa1',
    '#ff9da7',
    '#9c755f',
    '#bab0ab',
)


class DefaultChartStyle(ChartStyle):
    """The built-in look, a white 20:13 card with Tableau 10 colors, light grid lines, and one font throughout.

    The white background keeps text readable on clients in both light and dark mode.
    """

    def __init__(self, *, font: str) -> None:
        self._font = font

    @typing.override
    def canvas(self) -> dict[str, typing.Any]:
        return {
            'width': _WIDTH,
            'height': _HEIGHT,
            # Fit the whole chart, axes and legend included, inside the canvas.
            'autosize': {'type': 'fit', 'contains': 'padding'},
            'padding': _PADDING,
            'background': _BACKGROUND,
        }

    @typing.override
    def config(self, brand_color: str | None) -> dict[str, typing.Any]:
        categories = [brand_color, *_CATEGORY_COLORS[1:]] if brand_color else list(_CATEGORY_COLORS)
        text = {
            'labelFont': self._font,
            'titleFont': self._font,
            'labelColor': _TEXT_COLOR,
            'titleColor': _TEXT_COLOR,
            'labelFontSize': 12,
            'titleFontSize': 12,
            # Axis and legend titles share one weight, since a CJK font may lack the bold a Latin one has.
            'titleFontWeight': 'normal',
        }
        return {
            'font': self._font,
            'view': {'stroke': None},
            'mark': {'color': categories[0]},
            'bar': {'cornerRadiusEnd': 3},
            'line': {'strokeWidth': 2.5},
            'point': {'filled': True},
            'range': {'category': categories},
            'axis': {
                **text,
                'gridColor': _GRID_COLOR,
                'domainColor': _GRID_COLOR,
                'tickColor': _GRID_COLOR,
                'labelPadding': 6,
                'titlePadding': 10,
            },
            'axisX': {'grid': False, 'labelAngle': 0},
            'legend': {**text, 'orient': 'top', 'symbolType': 'circle'},
        }
