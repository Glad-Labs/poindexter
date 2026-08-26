# CI benchmark ingest — Actions artifacts → benchmark_results → Grafana

**Problem.** The nightly `benchmarks` workflow (`.github/workflows/benchmarks.yml`)
measures endpoint latency with pytest-benchmark against the in-process FastAPI
TestClient and uploads `benchmark_results.json` as a run artifact — which
nothing consumed. No panel, no trend, manual downloads only (violates the
every-metric-gets-a-panel rule). The workflow runs on a **hosted** runner with
a service-container DB and no Tailnet access, so it cannot write results to
the operator DB itself.

**Shape: pull-side ingest.**

```
benchmarks.yml (hosted, nightly 07:00 UTC)
  └─ uploads benchmark-results-<run_id> artifact (90d retention)

IngestBenchmarkResultsJob (worker, every 6 hours)          services/jobs/ingest_benchmark_results.py
  ├─ GET /repos/{repo}/actions/workflows/benchmarks.yml/runs?status=success
  ├─ per new run: already-ingested pre-check (no download for known runs)
  ├─ GET …/runs/{id}/artifacts → GET …/artifacts/{id}/zip (302 → blob storage)
  ├─ unzip in-memory → parse pytest-benchmark JSON (stats are SECONDS)
  └─ INSERT … ON CONFLICT (workflow_run_id, benchmark_name) DO NOTHING

benchmark_results (migration 20260826_032540)
  └─ Observability board → "CI Endpoint Benchmarks" row
     (mean-latency timeseries + latest-run table, Postgres datasource)
```

## Config

| Key                                   | Where                             | Meaning                                                                                                                                                                                                                     |
| ------------------------------------- | --------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `benchmark_ingest_repo`               | `app_settings` (OSS default `''`) | `owner/repo` whose Actions runs hold the artifacts. Empty = job skips quietly (fresh-install posture, mirrors the `github_issues` tap's empty `repos`). The Glad Labs value lives in `services/operator_overrides.py`.      |
| `gh_token`                            | existing secret                   | Reused (dev_diary / github_issues / branch-drift use the same key). Needs repo read + `actions:read`. **Repo set + token empty = half-configured → `ok=False` + a `benchmark_ingest_degraded` finding**, not a quiet green. |
| `plugin.job.ingest_benchmark_results` | auto-persisted by PluginScheduler | `enabled`, plus `config.max_runs_per_cycle` (5), `config.workflow_file` (`benchmarks.yml`), `config.transient_retries` (3).                                                                                                 |

## Reading the panel

The benchmarks are in-process TestClient timings on a **shared hosted
runner**: cross-run noise from runner load is expected, and absolute values
are not production latency. The signal is the relative trend — a handler
suddenly 10× slower after a merge. Authenticated task endpoints run with an
invalid bearer token, so they time the routing + auth-rejection path;
`/api/posts` and `/api/health` exercise real handlers. History starts
2026-08-26 — the workflow's first valid results ever (stack#3337 fixed
0-green-in-71; see `reference_nongating_ci_jobs_rot_invisibly`).

## Retention

`benchmark_results.aged` retention policy: `ttl_prune` on `captured_at`,
730 days. Volume is ~5 rows/night, so this is principle, not pressure.
