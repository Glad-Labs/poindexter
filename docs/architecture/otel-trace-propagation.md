# OpenTelemetry trace propagation — bring your own collector

Poindexter emits standards-compliant [OpenTelemetry](https://opentelemetry.io/)
traces over OTLP. Point it at **your own** OTLP-compatible backend (Grafana
Tempo, Honeycomb, Datadog, Jaeger, …) and you get the pipeline's spans in the
tooling you already run — no coupling to the backends this repo happens to ship.

## Turn it on

Two settings (both in `app_settings`, DB-backed):

| Setting                       | Value                                                                              |
| ----------------------------- | ---------------------------------------------------------------------------------- |
| `enable_tracing`              | `true`                                                                             |
| `otel_exporter_otlp_endpoint` | your collector's OTLP/HTTP traces endpoint, e.g. `http://localhost:4318/v1/traces` |

When `enable_tracing=true` but no endpoint is set, spans are still created but
not exported (a warning is logged) — so nothing breaks, you just see no traces
until you point it somewhere. When `enable_tracing` is false, tracing is a
no-op. The minimal `docker-compose.consumer.yml` stack ships **no** tracing
backend, so this is opt-in there.

## What's instrumented

`services/telemetry.py::setup_telemetry` wires:

- **Inbound HTTP** — `FastAPIInstrumentor` extracts the W3C `traceparent`
  header off incoming requests, so a trace started upstream continues here.
- **Outbound HTTP (egress)** — `HTTPXClientInstrumentor` emits a client span
  for every outbound `httpx` call (Ollama, LiteLLM, web research, publish) and
  **injects the W3C `traceparent` header** so the downstream service joins the
  same trace.
- **Global propagator** — pinned to W3C Trace Context
  (`TraceContextTextMapPropagator`). The OTel default is already W3C; pinning it
  makes the standards contract explicit and guarantees header injection.
- **Hot-path spans** — `@traced_method` on the Ollama client (`ollama.generate`
  / `chat` / `embed`), with long prompt attributes truncated to keep span size
  sane (full prompt bodies live in Langfuse via `@observe`).
- **LLM auto-spans** — `OpenAIInstrumentor`, where the OpenAI SDK path is used.

All of it degrades gracefully: if an OpenTelemetry distribution isn't installed,
the corresponding symbol is `None` and that instrumentation is skipped without
error.

## Current coverage and a known seam

Trace context now survives every **HTTP** hop. Two **non-HTTP** boundaries do
not yet propagate context, so a full content-generation run can still appear as
more than one trace:

- the **Prefect worker subprocess** boundary (the flow runs in its own
  process), and
- the **`pipeline_tasks` DB-queue handoff** (enqueue → claim).

Carrying `traceparent` across those two seams — so a single run renders as one
unbroken trace from task-claim through every LLM call — is the next increment.
Until then, point your collector at the endpoint above and you get coherent
per-request and per-egress traces today.
