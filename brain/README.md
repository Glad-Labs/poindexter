# Brain Daemon

**Last Updated:** 2026-04-29

The brain daemon is the always-on supervisor for a Poindexter install.
It runs alongside the worker (`poindexter-brain-daemon` container)
and is responsible for monitoring services, detecting drift in
operator-facing surfaces, and firing Telegram/Discord alerts when
something needs attention.

## What the daemon checks

Each cycle (default: every 5 minutes) the daemon runs a set of
probes against the local stack:

| Probe                        | Module                       | What it watches                                                                |
| ---------------------------- | ---------------------------- | ------------------------------------------------------------------------------ |
| Service health               | `health_probes.py`           | FastAPI worker, Ollama, Postgres, Grafana, Prometheus, Loki, Tempo containers  |
| Business probes              | `business_probes.py`         | Pipeline throughput, queue depth, recent quality scores                        |
| Alert sync                   | `alert_sync.py`              | Reconciles Grafana alert rules with the canonical definitions in `app_settings`|
| **Operator URL probe**       | `operator_url_probe.py`      | Operator-facing URLs and Tailscale IPs that have drifted or gone dark          |

Failures are routed through `operator_notifier.notify_operator()`
(Telegram by default, Discord webhook optional) and persisted to
`brain_decisions` so you can grep them after the fact.

## Operator URL probe

`operator_url_probe.py` (added in [#256](https://github.com/Glad-Labs/poindexter/pull/256), closes [#214](https://github.com/Glad-Labs/poindexter/issues/214))
is a periodic probe that flags operator-facing surfaces — the
URLs you actually click on — when they've drifted or stopped
responding. It exists because service-health probing alone misses
a real failure mode: a Grafana dashboard link points at a stale
Tailscale IP, the underlying service is fine, but every operator
who clicks the link hits a dead end.

### What it probes

Each cycle the probe walks four sources of operator-facing URLs:

1. **Grafana dashboard links** — every `*.json` in `infrastructure/grafana/dashboards/`, including dashboard-level `links`, panel-level `links`, and `fieldConfig.defaults.links` (data-link drill-downs). Templated URLs containing `${var}` or `$__time` are skipped.
2. **`system_devices.tailscale_ip`** — DB rows are compared against the live `tailscale status --json` output. Drift is reported with the exact `UPDATE` statement to fix it. If the `tailscale` CLI isn't installed, this step is skipped silently.
3. **`app_settings` keys ending in `_url`** — `site_url`, `storefront_url`, `oauth_issuer_url`, etc.
4. **Internal compose URL keys** — a curated list including `prefect_api_url`, `grafana_url`, `loki_url`, `tempo_url`, `prometheus_url`, `ollama_base_url`, `internal_api_base_url`, `openclaw_gateway_url`, `sdxl_server_url`.

Each URL is hit with `HEAD` (falling back to a ranged `GET` on 405/501).
Probes run in parallel via `httpx.AsyncClient` with a `Semaphore(10)` cap
and short connect/read timeouts (3s connect, 5s read), so one slow URL
can't stall the cycle.

### Notifications

Failures call `notify_operator()` once per surface per cycle. A
stack-wide outage (every observability service down at once) will
not blast Telegram — each unique surface produces at most one
notification per 15-minute window.

Notification content includes:

- The surface name (e.g. `Mission Control :: Prefect UI`, `app_settings.grafana_url`, `system_devices.brain-pc`)
- The URL that failed and the HTTP status / exception
- A recommended fix — for Tailscale drift, the exact `UPDATE` statement to run

### Schedule

The probe runs on a 15-minute cadence, gated inside
`maybe_run_operator_url_probe()`. The brain daemon's main loop
calls it on every 5-minute cycle but the gate lets only every
third call through, so you don't need a separate scheduler.

### Configuration

The probe is enabled by default and has no `app_settings` toggle —
it costs almost nothing to run and the failure mode it catches
(stale dashboard links) is a real production issue. If you want
to disable it temporarily, comment out the call in
`brain_daemon.py::run_cycle`.

Tunables are module-level constants in `operator_url_probe.py`:

| Constant                     | Default | Purpose                                            |
| ---------------------------- | ------- | -------------------------------------------------- |
| `HTTP_CONNECT_TIMEOUT_S`     | `3.0`   | Per-request connect timeout                        |
| `HTTP_READ_TIMEOUT_S`        | `5.0`   | Per-request read timeout                           |
| `DEFAULT_CONCURRENCY`        | `10`    | Max in-flight HTTP probes                          |
| `PROBE_INTERVAL_SECONDS`     | `900`   | Minimum gap between probe runs (15 minutes)        |
| `INTERNAL_COMPOSE_URL_KEYS`  | (list)  | `app_settings` keys that don't end in `_url` but should still be probed |

### Running the probe directly

The probe is normally invoked by the brain daemon, but you can
run it once on demand for debugging:

```python
import asyncio
import asyncpg

from brain.operator_url_probe import run_operator_url_probe

async def main():
    pool = await asyncpg.create_pool(dsn="postgresql://poindexter:...@localhost/poindexter_brain")
    summary = await run_operator_url_probe(pool)
    print(summary)
    await pool.close()

asyncio.run(main())
```

The returned summary looks like:

```python
{
    "total_urls_probed": 23,
    "url_failures": 1,
    "tailscale_drift_count": 0,
    "notifications_sent": 1,
    "failing_surfaces": [
        {
            "surface": "Pipeline Operations :: Prefect UI",
            "url": "http://100.64.1.5:4200/dashboard",
            "detail": "ConnectError: All connection attempts failed",
        }
    ],
    "drifted_devices": [],
}
```

### When to use it

- **You changed a Tailscale device's IP** — the probe will detect the drift on the next cycle and tell you exactly which `system_devices` row to update.
- **You renamed a service or changed a port** — every dashboard link still pointing at the old URL gets flagged.
- **You retired a service** — links left behind in old dashboards surface as failing probes; remove them or update them.
- **You added a new internal service** — add its `app_settings` URL key to `INTERNAL_COMPOSE_URL_KEYS` if it doesn't end in `_url`, and the probe picks it up automatically.

## Files in this directory

| File                          | Purpose                                                               |
| ----------------------------- | --------------------------------------------------------------------- |
| `brain_daemon.py`             | Main daemon entry point and cycle scheduler                           |
| `health_probes.py`            | Service-level health checks                                           |
| `business_probes.py`          | Pipeline throughput / quality probes                                  |
| `alert_sync.py`               | Reconciles Grafana alert rules                                        |
| `operator_url_probe.py`       | Operator-facing URL/IP drift probe                                    |
| `operator_notifier.py`        | Telegram/Discord notification dispatcher                              |
| `probe_interface.py`          | Probe Protocol + base classes                                         |
| `bootstrap.py`                | Brain bootstrap / first-boot setup                                    |
| `seed_loader.py`              | Loads `seed_app_settings.json` defaults on first boot                 |
| `seed_app_settings.json`      | Default `app_settings` rows shipped with the brain daemon             |
| `docker_utils.py`             | Helpers for inspecting / restarting compose containers                |
| `Dockerfile`                  | Container image for `poindexter-brain-daemon`                         |
| `hallucination-check/`        | Reference data (PyPI top-500, stdlib modules, Ollama models, etc.)    |
