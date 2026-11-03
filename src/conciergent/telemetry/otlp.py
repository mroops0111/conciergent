import atexit
import logging
import typing

from opentelemetry import _logs, trace
from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.logging.handler import LoggingHandler
from opentelemetry.sdk._logs import LoggerProvider
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor


_SERVICE_NAME = 'conciergent'
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


def install_tracer_provider() -> None:
    """Export spans over OTLP for the whole process."""
    provider = TracerProvider(resource=_resource())
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(provider)
    # A push exits right after its turn, so the batch still queued must be flushed on the way out.
    atexit.register(provider.shutdown)


def export_logs() -> None:
    """Send the root logger's lines over OTLP, through a logger provider the host set or one installed here."""
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


def _resource() -> Resource:
    resource = Resource.create()
    # OTEL_SERVICE_NAME or OTEL_RESOURCE_ATTRIBUTES may name the service, and only an unnamed one gets the default.
    if str(resource.attributes.get('service.name', '')).startswith('unknown_service'):
        resource = resource.merge(Resource({'service.name': _SERVICE_NAME}))
    return resource
