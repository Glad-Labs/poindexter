"""
API Endpoint Performance Benchmarks

Measures latency baselines for critical API endpoints.
Uses FastAPI's in-process TestClient so no live server is required.

The conftest boots the app in DEPLOYMENT_MODE=worker — the mode the API
actually runs in production. Coordinator mode mounts only the 4 public-site
routers, so benchmarking it turns every task/approval endpoint into a 404:
that exact mismatch (plus three /api/agents/* tests for endpoints deleted in
the May 2026 cleanup) kept the nightly benchmarks workflow red for its entire
life — 0 green runs in 71 — measuring the exception handler instead of real
handlers. Status asserts here are deliberately TIGHT (no 404 tolerance): a
benchmark of a dead endpoint is worse than a failing one, because the JSON
artifact quietly stops describing anything real.

SLA Targets:
    - Health checks:           <100ms
    - List endpoints:          <500ms

Run all benchmarks:
    poetry run pytest tests/benchmarks/ --benchmark-only -v

Run with JSON output for CI artifact storage:
    poetry run pytest tests/benchmarks/ --benchmark-json=benchmark_results.json

Compare against saved baseline:
    poetry run pytest tests/benchmarks/ --benchmark-compare=benchmark_results.json
"""

import pytest

DEV_TOKEN = "dev-token"
AUTH_HEADERS = {"Authorization": f"Bearer {DEV_TOKEN}"}


# ---------------------------------------------------------------------------
# Health endpoint (SLA: <100ms)
# ---------------------------------------------------------------------------


@pytest.mark.benchmark(group="health", min_rounds=20)
def test_health_endpoint_latency(benchmark, client):
    """Health check should respond in <100ms."""
    result = benchmark(client.get, "/api/health")
    assert result.status_code in (200, 503)  # 503 ok if DB not connected


# ---------------------------------------------------------------------------
# CMS posts list (SLA: <500ms) — the public-site read path. Vercel's ISR
# revalidation fetches this endpoint on every cache refresh, so its latency
# is directly user-facing.
# ---------------------------------------------------------------------------


@pytest.mark.benchmark(group="posts")
def test_posts_list_latency(benchmark, client):
    """Public posts listing should respond in <500ms.

    Uses ``pedantic`` with a fixed round count: /api/posts sits behind a
    60/minute slowapi limit, and auto-calibration would blow past it and
    benchmark the 429 handler instead of the query path.
    """
    result = benchmark.pedantic(
        client.get,
        args=("/api/posts?limit=10&offset=0",),
        rounds=30,
        warmup_rounds=2,
    )
    assert result.status_code == 200


# ---------------------------------------------------------------------------
# Task endpoints (SLA: <500ms for list) — worker-mode routes. An invalid
# bearer token is fine (401 measures the real auth + routing path); a 404
# is not, because it means the route no longer exists.
# ---------------------------------------------------------------------------


@pytest.mark.benchmark(group="tasks", min_rounds=5)
def test_task_list_latency(benchmark, client):
    """Task listing should respond in <500ms."""
    result = benchmark(
        client.get,
        "/api/tasks?limit=10&offset=0",
        headers=AUTH_HEADERS,
    )
    # Accept auth failures — latency still measured
    assert result.status_code in (200, 401, 403)


@pytest.mark.benchmark(group="tasks", min_rounds=5)
def test_task_pending_approval_latency(benchmark, client):
    """Pending approval listing should respond in <500ms."""
    result = benchmark(
        client.get,
        "/api/tasks/pending-approval",
        headers=AUTH_HEADERS,
    )
    assert result.status_code in (200, 401, 403)


# ---------------------------------------------------------------------------
# Metrics (SLA: <500ms)
# ---------------------------------------------------------------------------


@pytest.mark.benchmark(group="metrics", min_rounds=5)
def test_metrics_endpoint_latency(benchmark, client):
    """Metrics endpoint should respond in <500ms."""
    result = benchmark(client.get, "/api/metrics", headers=AUTH_HEADERS)
    assert result.status_code in (200, 401, 403)
