import atexit
import logging
import typing

import fastmcp
from opentelemetry import _logs, trace
from pydantic_ai import Agent
from pydantic_ai.models.instrumented import InstrumentationSettings


if typing.TYPE_CHECKING:
    from opentelemetry.sdk.resources import Resource


logger = logging.getLogger(__name__)

# Spans from this tracer are no-ops until a tracer provider is set, by ``setup`` or by the host application.
tracer = trace.get_tracer('conciergent')

_SERVICE_NAME = 'conciergent'
_INSTALL_HINT = 'telemetry needs the extra: uv add "conciergent[otel]"'
_HTTP_CLIENT_LOGGERS = ('httpx', 'httpcore')


class _ExportedRecordsFilter(logging.Filter):
    """Keep two kinds of log line out of the export, while stderr and the log file still show them.

    OpenTelemetry's own lines, such as a failed export, would feed the export they report on.
    An HTTP client's request lines name each URL, which can carry a token, so only its warnings and errors go out.
    """

    @typing.override
    def filter(self, record: logging.LogRecord) -> bool:
        if record.name.startswith('opentelemetry'):
            return False
        return not (record.name.startswith(_HTTP_CLIENT_LOGGERS) and record.levelno < logging.WARNING)


def setup(*, traces: bool = True, logs: bool = False, capture_content: bool = False) -> None:
    """Send the chosen OpenTelemetry signals for the whole process, configured by ``OTEL_EXPORTER_OTLP_*``.

    With ``traces``, each turn becomes a trace with the agent's spans inside it.
    Prompts, completions, and tool arguments and results stay out of the spans unless ``capture_content`` is set.
    With ``logs``, log lines go out too, each tied to the span it was logged in, if any.
    A host that already set a tracer or logger provider keeps it, along with wherever it exports.
    Call it after logging is configured, since the log export joins the root logger's handlers.
    """
    if traces:
        _export_traces(capture_content=capture_content)
    if logs:
        _export_logs()


def _export_traces(*, capture_content: bool) -> None:
    if isinstance(trace.get_tracer_provider(), trace.ProxyTracerProvider):
        _install_otlp_tracer_provider()
    else:
        logger.info('Tracing through the tracer provider already set')
    # Every agent, including the history summarizer's, emits its run, model, and tool spans.
    Agent.instrument_all(InstrumentationSettings(include_content=capture_content))
    # The mcp SDK already spans each MCP request and carries the trace in its _meta.
    # FastMCP's own client spans would repeat that layer, so FastMCP only propagates.
    fastmcp.settings.telemetry_mode = 'propagation_only'


def _install_otlp_tracer_provider() -> None:
    # The SDK and the exporters come with the otel extra, so they are imported only once telemetry is on.
    try:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError as error:
        raise RuntimeError(_INSTALL_HINT) from error
    provider = TracerProvider(resource=_resource())
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(provider)
    # A push exits right after its turn, so the batch still queued must be flushed on the way out.
    atexit.register(provider.shutdown)


def _export_logs() -> None:
    try:
        from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
        from opentelemetry.instrumentation.logging.handler import LoggingHandler
        from opentelemetry.sdk._logs import LoggerProvider
        from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
    except ImportError as error:
        raise RuntimeError(_INSTALL_HINT) from error
    # A host that already set a logger provider keeps it, as with the tracer provider.
    if not isinstance(_logs.get_logger_provider(), LoggerProvider):
        provider = LoggerProvider(resource=_resource())
        provider.add_log_record_processor(BatchLogRecordProcessor(OTLPLogExporter()))
        _logs.set_logger_provider(provider)
        atexit.register(provider.shutdown)
    root = logging.getLogger()
    if not any(isinstance(handler, LoggingHandler) for handler in root.handlers):
        handler = LoggingHandler()
        handler.addFilter(_ExportedRecordsFilter())
        root.addHandler(handler)


def _resource() -> 'Resource':
    from opentelemetry.sdk.resources import Resource

    resource = Resource.create()
    # OTEL_SERVICE_NAME or OTEL_RESOURCE_ATTRIBUTES may name the service, and only an unnamed one gets the default.
    if str(resource.attributes.get('service.name', '')).startswith('unknown_service'):
        resource = resource.merge(Resource({'service.name': _SERVICE_NAME}))
    return resource
