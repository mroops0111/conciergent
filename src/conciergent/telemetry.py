import atexit
import logging

import fastmcp
from opentelemetry import trace
from pydantic_ai import Agent
from pydantic_ai.models.instrumented import InstrumentationSettings


logger = logging.getLogger(__name__)

# Spans from this tracer are no-ops until a tracer provider is set, by ``setup`` or by the host application.
tracer = trace.get_tracer('conciergent')

_SERVICE_NAME = 'conciergent'


def setup(*, capture_content: bool = False) -> None:
    """Turn on tracing for the whole process, so each turn becomes a trace with the agent's spans inside it.

    A host that already set a tracer provider keeps it, along with wherever it exports.
    Otherwise spans export over OTLP, configured by the standard ``OTEL_EXPORTER_OTLP_*`` environment variables.
    Prompts, completions, and tool arguments and results stay out of the spans unless ``capture_content`` is set.
    """
    if isinstance(trace.get_tracer_provider(), trace.ProxyTracerProvider):
        _export_over_otlp()
    else:
        logger.info('Tracing through the tracer provider the host application set')
    # Every agent, including the history summarizer's, emits its run, model, and tool spans.
    Agent.instrument_all(InstrumentationSettings(include_content=capture_content))
    # The mcp SDK already spans each MCP request and carries the trace in its _meta.
    # FastMCP's own client spans would repeat that layer, so FastMCP only propagates.
    fastmcp.settings.telemetry_mode = 'propagation_only'


def _export_over_otlp() -> None:
    try:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError as error:
        raise RuntimeError('telemetry needs the extra: uv add conciergent[otel]') from error
    resource = Resource.create()
    # OTEL_SERVICE_NAME or OTEL_RESOURCE_ATTRIBUTES may name the service, and only an unnamed one gets the default.
    if str(resource.attributes.get('service.name', '')).startswith('unknown_service'):
        resource = resource.merge(Resource({'service.name': _SERVICE_NAME}))
    provider = TracerProvider(resource=resource)
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(provider)
    # A push exits right after its turn, so the batch still queued must be flushed on the way out.
    atexit.register(provider.shutdown)
