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
- **DB queue (enqueue → claim)** — `tasks_db.add_task` injects the enqueuer's
  W3C carrier into the new row's `pipeline_tasks.trace_context` column; the
  Prefect content-generation flow re-hydrates it at claim time and attaches it
  around its root span. This carries `traceparent` across the one boundary an
  HTTP propagator can't see — a database row — so a content run links back to
  the API request or scheduled job that created it instead of starting a
  disconnected trace.

All of it degrades gracefully: if an OpenTelemetry distribution isn't installed,
the corresponding symbol is `None` and that instrumentation is skipped without
error.

## Cross-linking to Langfuse (optional)

If you run [Langfuse](https://langfuse.com/) for LLM observability alongside your
OTLP trace backend, the content-pipeline root span carries a `langfuse.trace_url`
(and `langfuse.trace_id`) attribute — a clickable deep-link from the Tempo/Jaeger
span straight to the matching Langfuse trace.

This works because the two systems already share a trace id. LiteLLM's Langfuse
integration emits each LLM call as an OTel span parented under the **active**
span (it auto-detects the current context), and Langfuse keys its trace on the
ingested OTLP `trace_id`. So once W3C propagation is coherent (above), the run's
generations land in Langfuse under the **same** `trace_id` the root span already
has — the deep-link is just `{langfuse_host}/trace/{trace_id}`, built by
`plugins.tracing.stamp_langfuse_trace_url`. No Langfuse host configured → the
attribute is simply absent.

## Coverage

Trace context survives every **HTTP** hop (FastAPI inbound + httpx egress) **and
the `pipeline_tasks` DB queue** (enqueue → claim). When a task is created inside
an active trace — an API request, an MCP call — `add_task` stamps the W3C carrier
onto the row; the Prefect flow re-hydrates it at claim and parents its root span
to that trace. The whole content-generation run, from the enqueuing request
through every pipeline node and LLM call, renders as **one trace** in your
backend.

Tasks created with no active trace (scheduled jobs, CLI invocations) store a
NULL `trace_context` and the flow starts a fresh root span — exactly the
pre-propagation behaviour, so the column is purely additive.

One seam remains by design: the carrier links a run to its **enqueuer's** trace,
not across an in-flight `traceparent` propagated live through the Prefect worker
subprocess at runtime. The flow already owns a root span per run (#711); the DB
carrier parents it. Point your collector at the endpoint above and you get
coherent end-to-end traces today.
