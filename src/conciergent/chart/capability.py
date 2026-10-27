import dataclasses
import typing

import fastapi
from pydantic_ai import FunctionToolset, ModelRetry, RunContext
from pydantic_ai.output import OutputContext
from pydantic_ai.toolsets import AgentToolset

from conciergent.agent.capability import Capability
from conciergent.agent.deps import AgentDeps
from conciergent.chart.publisher import CHART_PATH, ChartPublisher
from conciergent.chart.renderer import ChartSpecError
from conciergent.reply import Card, Carousel


CREATE_CHART_TOOL_NAME = 'create_chart'


@dataclasses.dataclass
class ChartCapability(Capability):
    """Let the agent answer with a chart, which its ``create_chart`` tool draws and the app serves by URL.

    The model puts the returned URL in a card's ``hero_image_url``, so a surface shows a chart like any hero image.
    """

    publisher: ChartPublisher

    @classmethod
    @typing.override
    def get_serialization_name(cls) -> str | None:
        # It holds a live publisher, so it is built in code rather than from an agent spec.
        return None

    @typing.override
    def get_toolset(self) -> AgentToolset[AgentDeps]:
        toolset = FunctionToolset[AgentDeps]()
        toolset.add_function(self._create_chart, name=CREATE_CHART_TOOL_NAME)
        return toolset

    async def _create_chart(self, ctx: RunContext[AgentDeps], spec: dict[str, typing.Any]) -> str:
        """Draw a chart from a Vega-Lite spec and return the chart image's URL.

        Use it when the answer is a trend over time, a comparison, or a breakdown that reads better as a chart.
        Write only what to draw, which is inline `data.values` rows taken from tool results, `mark`,
        `encoding`, and optionally `transform` or `layer`.
        Never invent numbers.
        Remote data or images by `url` are refused, and the size, font, colors, and title are set by the server,
        so leave them out and label the axes in the user's language.
        Then reply with a reply_card whose `hero_image_url` is the returned URL, its header naming what the chart shows,
        and a section summarizing the key figures, since some clients cannot show images.
        """
        surface = ctx.deps.surface
        accent_color = surface.accent_color if surface is not None else None
        try:
            return await self.publisher.publish(spec, accent_color=accent_color)
        except ChartSpecError as error:
            raise ModelRetry(str(error)) from error

    @typing.override
    async def after_output_process(
        self, ctx: RunContext[AgentDeps], *, output_context: OutputContext, output: typing.Any
    ) -> typing.Any:
        # A mistyped or made-up chart URL would show as a broken image, so the model is sent back to copy it again.
        cards = [*output.options, output.fallback] if isinstance(output, Carousel) else [output]
        for card in cards:
            if isinstance(card, Card) and card.hero_image_url and await self.publisher.is_missing(card.hero_image_url):
                raise ModelRetry(
                    f'{card.hero_image_url} names no chart. '
                    f'Set hero_image_url to the URL exactly as {CREATE_CHART_TOOL_NAME} returned it.'
                )
        return output

    @typing.override
    def build_routers(self) -> list[fastapi.APIRouter]:
        router = fastapi.APIRouter()
        store = self.publisher.store

        @router.get(CHART_PATH)
        async def chart_image(chart_id: str) -> fastapi.Response:
            image = await store.get(chart_id)
            if image is None:
                return fastapi.Response(status_code=fastapi.status.HTTP_404_NOT_FOUND)
            # An id names one image forever, so a client or proxy may cache it until the chart expires.
            cache_control = f'public, max-age={store.ttl_seconds}, immutable'
            return fastapi.Response(image, media_type='image/png', headers={'Cache-Control': cache_control})

        return [router]
