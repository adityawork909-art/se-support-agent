"""AI-GOV scaffold & instrument: tracing every agent project carries from its first commit.

Call ``setup()`` once when the agent starts. From then on every step the agent takes - model calls,
tool calls, retrieval, guardrail checks - is an OpenTelemetry span carrying its OpenInference kind,
so every agent in the organisation logs in the same format.

Spans always go to a local JSON-lines file (``AIGOV_TRACE_FILE``), which the build gate reads.
When ``PHOENIX_COLLECTOR_ENDPOINT`` is set they go to Arize Phoenix as well.

That file stays on the build machine. The build gate sends AI-GOV span names, kinds, statuses and
durations only - never an input, an output or an attribute value.
"""
import atexit
import json
import os
from contextlib import contextmanager

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor, SpanExporter, SpanExportResult

TRACE_FILE = os.environ.get("AIGOV_TRACE_FILE", "aigov-spans.jsonl")
_KIND = "openinference.span.kind"


class JsonlSpanExporter(SpanExporter):
    """Writes each finished span as one JSON line. Used by the build gate and the dataset builder."""

    def export(self, spans):
        with open(TRACE_FILE, "a", encoding="utf-8") as out:
            for s in spans:
                attrs = {k: v for k, v in dict(s.attributes or {}).items() if isinstance(v, (str, int, float, bool))}
                out.write(json.dumps({
                    "name": s.name,
                    "kind": str(attrs.get(_KIND, "")).lower(),
                    "status": "error" if s.status.status_code.name == "ERROR" else "ok",
                    "duration_ms": round((s.end_time - s.start_time) / 1e6, 3),
                    "trace_id": format(s.context.trace_id, "032x"),
                    "attributes": attrs,
                }) + "\n")
        return SpanExportResult.SUCCESS

    def shutdown(self):
        return None


def _phoenix_provider(name):
    """Phoenix's own provider, when a collector is configured and the package is installed."""
    if not os.environ.get("PHOENIX_COLLECTOR_ENDPOINT"):
        return None
    try:
        from phoenix.otel import register
    except ImportError:
        return None
    try:
        return register(project_name=name, set_global_tracer_provider=False)
    except TypeError:
        return register(project_name=name)


def setup(service_name=None):
    """Turn tracing on for this process and return a tracer."""
    name = service_name or os.environ.get("OTEL_SERVICE_NAME", "agent")
    provider = _phoenix_provider(name) or TracerProvider(resource=Resource.create({"service.name": name}))
    provider.add_span_processor(SimpleSpanProcessor(JsonlSpanExporter()))
    trace.set_tracer_provider(provider)
    atexit.register(provider.shutdown)
    return trace.get_tracer(name)


@contextmanager
def span(name, kind, input=None):
    """A step the auto-instrumentors do not cover - a guardrail, a custom tool - with its kind.

    ``kind`` is one of: agent, llm, tool, retriever, guardrail, chain, embedding, reranker,
    evaluator, prompt.
    """
    tracer = trace.get_tracer("aigov")
    with tracer.start_as_current_span(name, attributes={_KIND: kind.upper()}) as current:
        if input is not None:
            current.set_attribute("input.value", str(input))
        yield current
