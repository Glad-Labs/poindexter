"""
Benchmark test fixtures.

These tests measure API endpoint latency using the FastAPI TestClient.
They do NOT require a running server — the app is invoked in-process.

Run:
    cd src/cofounder_agent
    poetry run pytest tests/benchmarks/ -v --benchmark-only
    poetry run pytest tests/benchmarks/ --benchmark-json=benchmark_results.json

Baseline SLA targets:
    - List endpoints (GET /api/tasks, GET /api/agents):    <500ms p99
    - Health / lightweight endpoints:                      <100ms p99
    - Create endpoints (POST /api/tasks):                  <2000ms p99
"""

import os

import pytest
from fastapi.testclient import TestClient

DEV_TOKEN = "dev-token"
AUTH_HEADERS = {"Authorization": f"Bearer {DEV_TOKEN}"}


@pytest.fixture(scope="session")
def app():
    """Import and return the FastAPI app with minimal env setup."""
    # Set required env vars so the app can import without crashing
    os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost:5432/test")
    os.environ.setdefault("ANTHROPIC_API_KEY", "benchmark-placeholder")
    os.environ.setdefault("SECRET_KEY", "benchmark-secret-key")

    from main import app as _app  # noqa: PLC0415

    return _app


@pytest.fixture(scope="session")
def client(app):
    """Return a TestClient configured for benchmark tests."""
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c
