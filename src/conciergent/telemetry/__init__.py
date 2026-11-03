import logging

import fastmcp
from opentelemetry import trace
from pydantic_ai import Agent
from pydantic_ai.models.instrumented import InstrumentationSettings


logger = logging.getLogger(__name__)

# Spans from this tracer are no-ops until a tracer provider is set, by ``setup`` or by the host application.
tracer = trace.get_tracer('conciergent')


def setup(*, traces: bool = False, logs: bool = False, capture_content: bool = False) -> None:
    """Send the chosen OpenTelemetry signals for the whole process, configured by ``OTEL_EXPORTER_OTLP_*``.

    With ``traces``, each turn becomes a trace with the agent's spans inside it.
    Prompts, completions, and tool arguments and results stay out of the spans unless ``capture_content`` is set.
    With ``logs``, log lines go out too, each tied to the span it was logged in, if any.
    A host that already set a tracer or logger provider keeps it, along with wherever it exports.
    """
    if traces:
        _export_traces(capture_content=capture_content)
    if logs:
        _otlp().export_logs()


def _export_traces(*, capture_content: bool) -> None:
    if isinstance(trace.get_tracer_provider(), trace.ProxyTracerProvider):
        _otlp().install_tracer_provider()
    else:
        logger.info('Tracing through the tracer provider already set')
    # Every agent, including the history summarizer's, emits its run, model, and tool spans.
    Agent.instrument_all(InstrumentationSettings(include_content=capture_content))
    # The mcp SDK already spans each MCP request and carries the trace in its _meta.
    # FastMCP's own client spans would repeat that layer, so FastMCP only propagates.
    fastmcp.settings.telemetry_mode = 'propagation_only'


# The return type is left to inference, which keeps the module's own type, so calls into it stay checked.
def _otlp():
    # The SDK and the exporters come with the otel extra, so they load only when an export needs them.
    try:
        from conciergent.telemetry import otlp
    except ImportError as error:
        raise RuntimeError('telemetry needs the extra: uv add "conciergent[otel]"') from error
    return otlp
