"""
Unit tests for routes/pipeline_events_routes.py.

Tests cover all three endpoints:
- GET /api/pipeline/events       — list recent pipeline events with filters
- GET /api/pipeline/events/task/{task_id} — all events for a single task
- GET /pipeline                  — HTML dashboard (smoke test)

Also tests the _format_event helper for edge cases (JSON details, string
details, missing fields, etc.).

DB is mocked via monkeypatch on the module-level _get_pool coroutine.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from routes.pipeline_events_routes import (
    _PIPELINE_EVENT_TYPES,
    _format_event,
    router,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

NOW = datetime.now(timezone.utc)
TASK_ID = "550e8400-e29b-41d4-a716-446655440000"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_row(
    id_: int = 1,
    event_type: str = "qa_decision",
    task_id: str = TASK_ID,
    details: dict | str | None = None,
    severity: str = "info",
    source: str = "multi_model_qa",
) -> dict:
    """Build a dict that looks like an audit_log DB row."""
    return {
        "id": id_,
        "timestamp": NOW,
        "event_type": event_type,
        "source": source,
        "task_id": task_id,
        "details": details if details is not None else {"approved": True, "score": 85},
        "severity": severity,
    }


class _FakeAcquire:
    """Async context manager that mimics asyncpg pool.acquire()."""

    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, *exc):
        return False


class _FailingAcquire:
    """Async context manager that raises on enter (simulates DB failure)."""

    def __init__(self, error):
        self._error = error

    async def __aenter__(self):
        raise self._error

    async def __aexit__(self, *exc):
        return False


def _mock_pool_with_rows(rows: list[dict]) -> tuple[MagicMock, AsyncMock]:
    """Build a mock pool whose acquire().fetch() returns *rows*.

    The route code does ``async with pool.acquire() as conn`` so
    pool.acquire() must return an async context manager (not a coroutine).

    Returns (mock_pool, mock_conn) so tests can inspect query args.
    """
    mock_conn = AsyncMock()
    # The route calls dict(r) on each row. Plain dicts survive dict() fine.
    mock_conn.fetch = AsyncMock(return_value=rows)

    mock_pool = MagicMock()
    mock_pool.acquire.return_value = _FakeAcquire(mock_conn)
    return mock_pool, mock_conn


def _make_client(mock_pool: MagicMock) -> TestClient:
    """Build a TestClient with the pipeline events router and a mocked pool."""
    app = FastAPI()
    app.include_router(router)
    # Patch _get_pool at the module level to return our mock pool
    import routes.pipeline_events_routes as mod

    async def _fake_get_pool():
        return mock_pool

    mod._get_pool = _fake_get_pool
    return TestClient(app)


# ---------------------------------------------------------------------------
# _format_event unit tests
# ---------------------------------------------------------------------------


class TestFormatEvent:
    """Tests for the _format_event helper that flattens DB rows."""

    def test_basic_row(self):
        row = _make_row()
        result = _format_event(row)
        assert result["id"] == 1
        assert result["event_type"] == "qa_decision"
        assert result["task_id"] == TASK_ID
        assert result["severity"] == "info"
        assert result["source"] == "multi_model_qa"
        assert isinstance(result["details"], dict)
        assert result["details"]["approved"] is True

    def test_timestamp_iso_format(self):
        row = _make_row()
        result = _format_event(row)
        assert result["timestamp"] == NOW.isoformat()

    def test_missing_timestamp(self):
        row = _make_row()
        row["timestamp"] = None
        result = _format_event(row)
        assert result["timestamp"] is None

    def test_details_as_json_string(self):
        """When details is a JSON string it should be parsed."""
        payload = {"score": 92, "approved": True}
        row = _make_row(details=json.dumps(payload))
        result = _format_event(row)
        assert result["details"] == payload

    def test_details_as_invalid_string(self):
        """Non-JSON string details should be wrapped in {raw: ...}."""
        row = _make_row(details="not json at all")
        result = _format_event(row)
        assert result["details"] == {"raw": "not json at all"}

    def test_details_none(self):
        """None details should become an empty dict."""
        row = _make_row()
        row["details"] = None  # explicitly set to None after construction
        result = _format_event(row)
        assert result["details"] == {}

    def test_severity_defaults_to_info(self):
        row = _make_row()
        del row["severity"]
        result = _format_event(row)
        assert result["severity"] == "info"

    def test_details_already_dict(self):
        """Dict details pass through unchanged."""
        details = {"feedback": "looks great", "issues": []}
        row = _make_row(details=details)
        result = _format_event(row)
        assert result["details"] == details

    def test_details_empty_string(self):
        """Empty string is falsy, should become empty dict."""
        row = _make_row()
        row["details"] = ""
        result = _format_event(row)
        assert result["details"] == {}


# ---------------------------------------------------------------------------
# GET /api/pipeline/events
# ---------------------------------------------------------------------------


class TestListPipelineEvents:
    """Tests for the list pipeline events endpoint."""

    def test_returns_events(self):
        rows = [_make_row(id_=i, event_type="qa_decision") for i in range(3)]
        pool, conn = _mock_pool_with_rows(rows)
        client = _make_client(pool)
        resp = client.get("/api/pipeline/events")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 3
        assert len(data["events"]) == 3
        assert "server_time" in data

    def test_empty_result(self):
        pool, conn = _mock_pool_with_rows([])
        client = _make_client(pool)
        resp = client.get("/api/pipeline/events")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 0
        assert data["events"] == []

    def test_filter_by_event_type(self):
        rows = [_make_row(event_type="qa_failed")]
        pool, conn = _mock_pool_with_rows(rows)
        client = _make_client(pool)
        resp = client.get("/api/pipeline/events?event_type=qa_failed")
        assert resp.status_code == 200
        call_args = conn.fetch.call_args
        sql = call_args[0][0]
        # When event_type is specified, the query should use event_type = $2
        assert "event_type = $2" in sql

    def test_filter_by_task_id(self):
        rows = [_make_row()]
        pool, conn = _mock_pool_with_rows(rows)
        client = _make_client(pool)
        resp = client.get(f"/api/pipeline/events?task_id={TASK_ID}")
        assert resp.status_code == 200
        call_args = conn.fetch.call_args
        sql = call_args[0][0]
        # task_id filter should appear in SQL
        assert "task_id = $" in sql

    def test_filter_by_both(self):
        rows = [_make_row()]
        pool, conn = _mock_pool_with_rows(rows)
        client = _make_client(pool)
        resp = client.get(
            f"/api/pipeline/events?event_type=qa_decision&task_id={TASK_ID}"
        )
        assert resp.status_code == 200
        call_args = conn.fetch.call_args
        sql = call_args[0][0]
        assert "event_type = $2" in sql
        assert "task_id = $3" in sql

    def test_default_params_sent_to_query(self):
        """Default limit=50, since_minutes=60 should be in query params."""
        pool, conn = _mock_pool_with_rows([])
        client = _make_client(pool)
        resp = client.get("/api/pipeline/events")
        assert resp.status_code == 200
        call_args = conn.fetch.call_args
        params = call_args[0][1:]  # positional args after sql
        # First param is since_minutes (60), then the event types list, then limit (50)
        assert params[0] == 60  # since_minutes default
        assert params[1] == list(_PIPELINE_EVENT_TYPES)  # default event types
        assert params[2] == 50  # limit default

    def test_custom_limit_and_since(self):
        pool, conn = _mock_pool_with_rows([])
        client = _make_client(pool)
        resp = client.get("/api/pipeline/events?limit=10&since_minutes=30")
        assert resp.status_code == 200
        call_args = conn.fetch.call_args
        params = call_args[0][1:]
        assert params[0] == 30  # since_minutes
        assert params[-1] == 10  # limit is always last

    def test_limit_validation_too_high(self):
        pool, _ = _mock_pool_with_rows([])
        client = _make_client(pool)
        resp = client.get("/api/pipeline/events?limit=9999")
        assert resp.status_code == 422  # FastAPI validation error

    def test_limit_validation_too_low(self):
        pool, _ = _mock_pool_with_rows([])
        client = _make_client(pool)
        resp = client.get("/api/pipeline/events?limit=0")
        assert resp.status_code == 422

    def test_since_minutes_validation(self):
        pool, _ = _mock_pool_with_rows([])
        client = _make_client(pool)
        resp = client.get("/api/pipeline/events?since_minutes=0")
        assert resp.status_code == 422
        resp2 = client.get("/api/pipeline/events?since_minutes=2000")
        assert resp2.status_code == 422

    def test_db_error_returns_500(self):
        pool = MagicMock()
        pool.acquire.return_value = _FailingAcquire(RuntimeError("connection lost"))
        client = _make_client(pool)
        resp = client.get("/api/pipeline/events")
        assert resp.status_code == 500
        assert "Failed to load pipeline events" in resp.json()["detail"]

    def test_event_format_in_response(self):
        """Verify each event in the response has the expected keys."""
        rows = [_make_row(id_=1)]
        pool, _ = _mock_pool_with_rows(rows)
        client = _make_client(pool)
        resp = client.get("/api/pipeline/events")
        assert resp.status_code == 200
        event = resp.json()["events"][0]
        expected_keys = {"id", "timestamp", "event_type", "source", "task_id", "severity", "details"}
        assert set(event.keys()) == expected_keys


# ---------------------------------------------------------------------------
# GET /api/pipeline/events/task/{task_id}
# ---------------------------------------------------------------------------


class TestTaskPipelineEvents:
    """Tests for the single-task events endpoint."""

    def test_returns_events_for_task(self):
        rows = [
            _make_row(id_=1, event_type="task_started"),
            _make_row(id_=2, event_type="qa_decision"),
            _make_row(id_=3, event_type="pipeline_complete"),
        ]
        pool, _ = _mock_pool_with_rows(rows)
        client = _make_client(pool)
        resp = client.get(f"/api/pipeline/events/task/{TASK_ID}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["task_id"] == TASK_ID
        assert data["count"] == 3
        assert len(data["events"]) == 3

    def test_empty_for_unknown_task(self):
        pool, _ = _mock_pool_with_rows([])
        client = _make_client(pool)
        resp = client.get("/api/pipeline/events/task/unknown-id")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 0
        assert data["events"] == []

    def test_query_passes_pipeline_event_types(self):
        pool, conn = _mock_pool_with_rows([])
        client = _make_client(pool)
        resp = client.get(f"/api/pipeline/events/task/{TASK_ID}")
        assert resp.status_code == 200
        call_args = conn.fetch.call_args
        params = call_args[0][1:]  # skip sql
        assert params[0] == TASK_ID
        assert params[1] == list(_PIPELINE_EVENT_TYPES)

    def test_response_includes_task_id(self):
        pool, _ = _mock_pool_with_rows([])
        client = _make_client(pool)
        resp = client.get(f"/api/pipeline/events/task/{TASK_ID}")
        assert resp.status_code == 200
        assert resp.json()["task_id"] == TASK_ID

    def test_db_error_returns_500(self):
        pool = MagicMock()
        pool.acquire.return_value = _FailingAcquire(RuntimeError("db down"))
        client = _make_client(pool)
        resp = client.get(f"/api/pipeline/events/task/{TASK_ID}")
        assert resp.status_code == 500
        assert "Failed to load task events" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# GET /pipeline (HTML dashboard)
# ---------------------------------------------------------------------------


class TestPipelineDashboard:
    """Smoke tests for the HTML dashboard endpoint."""

    def test_returns_html(self):
        pool, _ = _mock_pool_with_rows([])
        client = _make_client(pool)
        resp = client.get("/pipeline")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]

    def test_contains_key_elements(self):
        pool, _ = _mock_pool_with_rows([])
        client = _make_client(pool)
        resp = client.get("/pipeline")
        html = resp.text
        assert "Poindexter Pipeline" in html
        assert "fetchEvents" in html
        assert "/api/pipeline/events" in html
        assert "qa_decision" in html

    def test_has_noindex_meta(self):
        """Dashboard should not be indexed by search engines."""
        pool, _ = _mock_pool_with_rows([])
        client = _make_client(pool)
        resp = client.get("/pipeline")
        assert "noindex" in resp.text

    def test_has_auto_refresh(self):
        """Dashboard should poll every 5 seconds."""
        pool, _ = _mock_pool_with_rows([])
        client = _make_client(pool)
        resp = client.get("/pipeline")
        assert "setInterval(fetchEvents, 5000)" in resp.text


# ---------------------------------------------------------------------------
# _PIPELINE_EVENT_TYPES constant
# ---------------------------------------------------------------------------


class TestPipelineEventTypes:
    """Sanity checks on the event types constant."""

    def test_is_tuple(self):
        assert isinstance(_PIPELINE_EVENT_TYPES, tuple)

    def test_contains_core_types(self):
        for t in ("qa_decision", "qa_aggregate", "qa_passed", "qa_failed",
                   "rewrite_decision", "pipeline_complete"):
            assert t in _PIPELINE_EVENT_TYPES

    def test_no_duplicates(self):
        assert len(_PIPELINE_EVENT_TYPES) == len(set(_PIPELINE_EVENT_TYPES))
