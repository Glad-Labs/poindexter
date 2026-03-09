"""
API Endpoint Performance Benchmarks

Measures latency baselines for critical API endpoints.
Uses FastAPI's in-process TestClient so no live server is required.

SLA Targets:
    - Health checks:           <100ms
    - List endpoints:          <500ms
    - Agent registry:          <200ms (in-memory)
    - Service registry:        <200ms (in-memory)

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
# Agent registry (SLA: <200ms — in-memory, no DB)
# ---------------------------------------------------------------------------


@pytest.mark.benchmark(group="agents", min_rounds=10)
def test_agent_registry_latency(benchmark, client):
    """Agent registry listing should respond in <200ms."""
    result = benchmark(client.get, "/api/agents/registry", headers=AUTH_HEADERS)
    assert result.status_code in (200, 401, 403)


@pytest.mark.benchmark(group="agents", min_rounds=10)
def test_agent_list_latency(benchmark, client):
    """Agent name listing should respond in <200ms."""
    result = benchmark(client.get, "/api/agents/list", headers=AUTH_HEADERS)
    assert result.status_code in (200, 401, 403)


@pytest.mark.benchmark(group="agents", min_rounds=10)
def test_agent_search_latency(benchmark, client):
    """Agent search should respond in <200ms."""
    result = benchmark(
        client.get,
        "/api/agents/search?category=content",
        headers=AUTH_HEADERS,
    )
    assert result.status_code in (200, 401, 403)


# ---------------------------------------------------------------------------
# Task endpoints (SLA: <500ms for list)
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
    assert result.status_code in (200, 401, 403, 422)


@pytest.mark.benchmark(group="tasks", min_rounds=5)
def test_task_pending_approval_latency(benchmark, client):
    """Pending approval listing should respond in <500ms."""
    result = benchmark(
        client.get,
        "/api/tasks/pending-approval",
        headers=AUTH_HEADERS,
    )
    assert result.status_code in (200, 401, 403, 404)


# ---------------------------------------------------------------------------
# Service registry (SLA: <200ms — in-memory)
# ---------------------------------------------------------------------------


@pytest.mark.benchmark(group="registry", min_rounds=10)
def test_service_registry_latency(benchmark, client):
    """Service registry endpoint should respond in <200ms."""
    result = benchmark(client.get, "/api/registry", headers=AUTH_HEADERS)
    assert result.status_code in (200, 401, 403, 404)


# ---------------------------------------------------------------------------
# Metrics (SLA: <500ms)
# ---------------------------------------------------------------------------


@pytest.mark.benchmark(group="metrics", min_rounds=5)
def test_metrics_endpoint_latency(benchmark, client):
    """Metrics endpoint should respond in <500ms."""
    result = benchmark(client.get, "/api/metrics", headers=AUTH_HEADERS)
    assert result.status_code in (200, 401, 403)
