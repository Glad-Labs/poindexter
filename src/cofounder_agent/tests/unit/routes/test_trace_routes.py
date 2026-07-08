"""Unit tests for the console task-trace HTTP routes (``routes/trace_routes.py``).

Pins the HTTP contract — verb, path, run_id forwarding, honest-empty on error,
auth, and the literal-path-before-``/{task_id}`` ordering — without a live DB.
``services.trace_read`` is mocked per test; its SQL is covered separately by
``tests/integration_db/test_trace_read.py``. Auth uses the real
``verify_api_token`` dependency (overridden only in the authed cases) so an
unauthenticated request 401s.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import routes.trace_routes as trace_routes
from middleware.api_token_auth import verify_api_token
from routes.trace_routes import router
from utils.route_utils import get_database_dependency, get_site_config_dependency

ACTIVE = {
    "runs": [{"task_id": "T1", "topic": "A topic", "status": "in_progress",
              "stage": "content.generate_draft", "percentage": 40, "nodes_done": 3}],
    "recent": [{"task_id": "T0", "topic": "done", "status": "published"}],
}
SUMMARY = {"window_hours": 24, "tasks": 5, "by_status": {"published": 3},
           "pass_rate": 0.75, "avg_quality": 82.0, "avg_cost_usd": 0.02}
TRACE = {"task_id": "T1", "run_id": "R2", "runs": [], "task": {"topic": "A topic"},
         "nodes": [{"seq": 0, "atom": "content.generate_draft", "status": "ok"}],
         "corpus": "- Source A", "decisions": [], "qa": [],
         "cost_rollup": {"by_model": [], "total_usd": 0.0}, "final": None,
         "halt": None, "langfuse": {"session_id": "T1", "run_id": "R2"}}


def _make_db():
    db = MagicMock()
    db.pool = MagicMock(name="pool")  # only needs to be passable; read fns are mocked
    return db


def _make_sc():
    sc = MagicMock()
    sc.get = MagicMock(return_value="10")  # trace_recent_limit
    return sc


def _build_app(*, authed=True):
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_database_dependency] = lambda: _make_db()
    app.dependency_overrides[get_site_config_dependency] = lambda: _make_sc()
    if authed:
        app.dependency_overrides[verify_api_token] = lambda: "test-token"
    return app


@pytest.fixture(autouse=True)
def _clear_summary_cache():
    # The /summary route caches in-process; reset it so tests don't see a
    # neighbour's cached value.
    trace_routes._SUMMARY_CACHE.update({"ts": 0.0, "val": None})
    yield
    trace_routes._SUMMARY_CACHE.update({"ts": 0.0, "val": None})


@pytest.mark.unit
class TestTraceActive:
    def test_returns_board(self):
        with patch.object(trace_routes.trace_read, "get_active",
                          new=AsyncMock(return_value=ACTIVE)) as m:
            resp = TestClient(_build_app()).get("/api/trace/active")
        assert resp.status_code == 200
        assert resp.json()["runs"][0]["task_id"] == "T1"
        assert m.await_args.kwargs["recent_limit"] == 10  # from trace_recent_limit
        m.assert_awaited_once()

    def test_honest_empty_on_error(self):
        with patch.object(trace_routes.trace_read, "get_active",
                          new=AsyncMock(side_effect=RuntimeError("boom"))):
            resp = TestClient(_build_app()).get("/api/trace/active")
        assert resp.status_code == 200
        assert resp.json() == {"runs": [], "recent": []}

    def test_requires_auth(self):
        assert TestClient(_build_app(authed=False)).get("/api/trace/active").status_code == 401


@pytest.mark.unit
class TestTraceSummary:
    def test_returns_summary(self):
        with patch.object(trace_routes.trace_read, "get_summary",
                          new=AsyncMock(return_value=SUMMARY)) as m:
            resp = TestClient(_build_app()).get("/api/trace/summary")
        assert resp.status_code == 200
        assert resp.json()["tasks"] == 5
        m.assert_awaited_once()

    def test_caches_within_ttl(self):
        with patch.object(trace_routes.trace_read, "get_summary",
                          new=AsyncMock(return_value=SUMMARY)) as m:
            client = TestClient(_build_app())
            client.get("/api/trace/summary")
            client.get("/api/trace/summary")
        m.assert_awaited_once()  # second call served from the in-process cache


@pytest.mark.unit
class TestTraceDetail:
    def test_returns_trace_and_forwards_run_id(self):
        with patch.object(trace_routes.trace_read, "get_trace",
                          new=AsyncMock(return_value=TRACE)) as m:
            resp = TestClient(_build_app()).get("/api/trace/T1?run_id=R2")
        assert resp.status_code == 200
        assert resp.json()["task_id"] == "T1"
        # get_trace(pool, task_id, run_id)
        assert m.await_args.args[1] == "T1"
        assert m.await_args.args[2] == "R2"

    def test_default_run_id_is_none(self):
        with patch.object(trace_routes.trace_read, "get_trace",
                          new=AsyncMock(return_value=TRACE)) as m:
            TestClient(_build_app()).get("/api/trace/T1")
        assert m.await_args.args[2] is None  # empty run_id -> None (latest content run)

    def test_literal_active_not_shadowed_by_task_id_route(self):
        """/active must match its literal route, never the /{task_id} catch-all."""
        with patch.object(trace_routes.trace_read, "get_active",
                          new=AsyncMock(return_value=ACTIVE)) as ma, \
             patch.object(trace_routes.trace_read, "get_trace",
                          new=AsyncMock(return_value=TRACE)) as mt:
            resp = TestClient(_build_app()).get("/api/trace/active")
        assert resp.status_code == 200
        ma.assert_awaited_once()
        mt.assert_not_awaited()
