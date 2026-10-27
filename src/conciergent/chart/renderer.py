import copy
import typing

from conciergent.chart.style import ChartStyle


class ChartSpecError(ValueError):
    """A spec the renderer refuses, with a message the model can act on when it retries."""


class ChartRenderer:
    """Render a Vega-Lite spec to a PNG in the given style, refusing anything that reaches outside the spec."""

    def __init__(self, style: ChartStyle, *, font_directory: str | None = None, max_rows: int) -> None:
        try:
            import vl_convert
        except ImportError as error:
            raise RuntimeError('charts need the extra: uv add conciergent[charts]') from error
        if font_directory is not None:
            # A font Vega-Lite cannot find draws no text at all, so a container image without the font registers it.
            vl_convert.register_font_directory(font_directory)
        self._vl_convert = vl_convert
        self._style = style
        self._max_rows = max_rows

    def render(self, spec: typing.Any, *, accent_color: str | None = None) -> bytes:
        """Return the PNG for ``spec``, drawn in ``accent_color`` first when one is given.

        Raises ``ChartSpecError`` for a spec that is not inline data within the row limit,
        or that Vega-Lite cannot compile.
        """
        self._check(spec)
        try:
            return self._vl_convert.vegalite_to_png(
                self._style.apply(copy.deepcopy(spec)),
                scale=self._style.scale,
                config=self._style.config(accent_color),
                # Remote data is already refused, this blocks it a second time inside the renderer.
                allowed_base_urls=[],
            )
        except ValueError as error:
            raise ChartSpecError(f'Vega-Lite could not render the spec: {error}') from error

    def _check(self, spec: typing.Any) -> None:
        if not isinstance(spec, dict):
            raise ChartSpecError('the spec must be a Vega-Lite JSON object')
        if _contains_url(spec):
            # Vega-Lite fetches data and image marks from a url, even with allowed_base_urls empty for image marks,
            # so a spec that names one could make the server request an address the model was talked into.
            raise ChartSpecError('remote data and images are not allowed, put the rows inline in data.values')
        data = spec.get('data')
        values = data.get('values') if isinstance(data, dict) else None
        if not isinstance(values, list) or not values:
            raise ChartSpecError('put the rows inline in a non-empty data.values list')
        if len(values) > self._max_rows:
            raise ChartSpecError(f'data.values has {len(values)} rows, aggregate it to at most {self._max_rows}')


def _contains_url(value: typing.Any) -> bool:
    if isinstance(value, dict):
        return any(key == 'url' or _contains_url(item) for key, item in value.items())
    if isinstance(value, list):
        return any(_contains_url(item) for item in value)
    return False
