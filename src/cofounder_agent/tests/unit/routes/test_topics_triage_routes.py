"""Unit tests for the topic-triage HTTP routes (Phase 4 operator console).

Covers the batch-oriented triage surface added on top of the existing
URL-seeding endpoints in ``routes/topics_routes.py``:

- GET  /api/topics/proposals          — list open topic batches + candidates
- POST /api/topics/{batch_id}/rank    — set operator candidate order
- POST /api/topics/{batch_id}/resolve — advance the rank-1 winner into the pipeline
- POST /api/topics/{batch_id}/reject  — discard the batch, free the niche slot

These mirror the ``topics_*`` MCP tools, which delegate to
``services.topic_batch_service.TopicBatchService``. The service is mocked here so
the tests pin the HTTP contract (verb, path, body, status, delegation) without a
live DB. The ``{batch_id}`` path segment is a topic *batch* id — resolution
advances the operator-ranked rank-1 candidate. Auth uses the real
``verify_api_token`` dependency (no override) so unauthenticated requests 401.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from middleware.api_token_auth import verify_api_token
from routes.topics_routes import router
from services.topic_batch_service import BatchView, CandidateView
from utils.route_utils import get_database_dependency, get_site_config_dependency


def _make_db():
    db = MagicMock()
    # The triage routes build a TopicBatchService from db_service.pool; the
    # service is patched per-test, so the pool only needs to be *passable*.
    db.pool = MagicMock(name="pool")
    return db


def _build_app(db=None, *, authed=True):
    db = db if db is not None else _make_db()
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_database_dependency] = lambda: db
    app.dependency_overrides[get_site_config_dependency] = lambda: MagicMock()
    if authed:
        app.dependency_overrides[verify_api_token] = lambda: "test-token"
    return app


def _candidate(cid, *, title="Cand", kind="external", rank=1, eff=80.0, op_rank=None):
    return CandidateView(
        id=str(cid), kind=kind, title=title, summary="summary",
        score=eff, decay_factor=1.0, effective_score=eff,
        rank_in_batch=rank, operator_rank=op_rank,
        operator_edited_topic=None, operator_edited_angle=None,
        score_breakdown={},
    )


def _open_batch(batch_id, niche_id, *, slug="glad-labs", name="Glad Labs", candidates=None):
    """Stand-in for the service's OpenBatch wrapper (view + niche meta).

    Uses SimpleNamespace so the route test pins the *shape* the route
    consumes (``.view`` / ``.niche_slug`` / ``.niche_name``) without coupling
    to the concrete wrapper class name.
    """
    view = BatchView(
        id=batch_id, niche_id=niche_id, status="open",
        picked_candidate_id=None, candidates=candidates or [],
    )
    return SimpleNamespace(view=view, niche_slug=slug, niche_name=name)


def _patch_service(svc):
    """Patch TopicBatchService in the route namespace so it returns ``svc``."""
    return patch("routes.topics_routes.TopicBatchService", return_value=svc)


# ---------------------------------------------------------------------------
# GET /api/topics/proposals
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestListProposals:
    def test_lists_open_batches_with_candidates(self):
        bid, nid = uuid4(), uuid4()
        cands = [
            _candidate(uuid4(), title="Top pick", rank=1, eff=88.0, op_rank=1),
            _candidate(uuid4(), title="Runner up", kind="internal", rank=2, eff=70.0),
        ]
        ob = _open_batch(bid, nid, slug="glad-labs", name="Glad Labs", candidates=cands)
        svc = MagicMock()
        svc.list_open_batches = AsyncMock(return_value=[ob])

        with _patch_service(svc):
            client = TestClient(_build_app())
            resp = client.get("/api/topics/proposals")

        assert resp.status_code == 200, resp.text
        data = resp.json()
        # Canonical offset envelope (poindexter#745): items, not the legacy
        # batches/count keys. Unpaginated full listing → limit == len, offset 0.
        assert data["total"] == 1
        assert data["limit"] == 1
        assert data["offset"] == 0
        assert "batches" not in data
        assert "count" not in data
        b = data["items"][0]
        assert b["batch_id"] == str(bid)
        assert b["niche_id"] == str(nid)
        assert b["niche_slug"] == "glad-labs"
        assert b["niche_name"] == "Glad Labs"
        assert b["status"] == "open"
        assert b["candidate_count"] == 2
        assert b["candidates"][0]["title"] == "Top pick"
        assert b["candidates"][0]["kind"] == "external"
        assert b["candidates"][0]["effective_score"] == 88.0
        assert b["candidates"][0]["operator_rank"] == 1
        assert b["candidates"][1]["kind"] == "internal"
        svc.list_open_batches.assert_awaited_once()

    def test_empty_when_no_open_batches(self):
        svc = MagicMock()
        svc.list_open_batches = AsyncMock(return_value=[])
        with _patch_service(svc):
            client = TestClient(_build_app())
            resp = client.get("/api/topics/proposals")
        assert resp.status_code == 200
        assert resp.json() == {"items": [], "total": 0, "limit": 0, "offset": 0}

    def test_unauthenticated_returns_401(self):
        client = TestClient(_build_app(authed=False), raise_server_exceptions=False)
        resp = client.get("/api/topics/proposals")
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# POST /api/topics/{batch_id}/rank
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRankBatch:
    def test_sets_operator_order(self):
        bid = uuid4()
        ids = [str(uuid4()), str(uuid4())]
        svc = MagicMock()
        svc.rank_batch = AsyncMock(return_value=None)
        with _patch_service(svc):
            client = TestClient(_build_app())
            resp = client.post(
                f"/api/topics/{bid}/rank", json={"ordered_candidate_ids": ids},
            )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["ok"] is True
        assert data["ranked"] == 2
        svc.rank_batch.assert_awaited_once()
        kwargs = svc.rank_batch.await_args.kwargs
        assert str(kwargs["batch_id"]) == str(bid)
        assert kwargs["ordered_candidate_ids"] == ids

    def test_empty_list_returns_422(self):
        bid = uuid4()
        svc = MagicMock()
        svc.rank_batch = AsyncMock()
        with _patch_service(svc):
            client = TestClient(_build_app())
            resp = client.post(
                f"/api/topics/{bid}/rank", json={"ordered_candidate_ids": []},
            )
        assert resp.status_code == 422
        svc.rank_batch.assert_not_awaited()

    def test_invalid_batch_id_returns_400(self):
        svc = MagicMock()
        svc.rank_batch = AsyncMock()
        with _patch_service(svc):
            client = TestClient(_build_app(), raise_server_exceptions=False)
            resp = client.post(
                "/api/topics/not-a-uuid/rank", json={"ordered_candidate_ids": ["x"]},
            )
        assert resp.status_code == 400
        svc.rank_batch.assert_not_awaited()

    def test_unauthenticated_returns_401(self):
        bid = uuid4()
        client = TestClient(_build_app(authed=False), raise_server_exceptions=False)
        resp = client.post(
            f"/api/topics/{bid}/rank", json={"ordered_candidate_ids": ["x"]},
        )
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# POST /api/topics/{batch_id}/resolve
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestResolveBatch:
    def test_resolves_batch(self):
        bid = uuid4()
        svc = MagicMock()
        svc.resolve_batch = AsyncMock(return_value=None)
        with _patch_service(svc):
            client = TestClient(_build_app())
            resp = client.post(f"/api/topics/{bid}/resolve")
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["ok"] is True
        assert data["status"] == "resolved"
        svc.resolve_batch.assert_awaited_once()
        assert str(svc.resolve_batch.await_args.kwargs["batch_id"]) == str(bid)

    def test_unranked_batch_returns_400(self):
        # resolve_batch raises ValueError when no operator_rank=1 candidate.
        bid = uuid4()
        svc = MagicMock()
        svc.resolve_batch = AsyncMock(
            side_effect=ValueError("no operator_rank=1 candidate; rank first"),
        )
        with _patch_service(svc):
            client = TestClient(_build_app(), raise_server_exceptions=False)
            resp = client.post(f"/api/topics/{bid}/resolve")
        assert resp.status_code == 400
        assert "rank first" in resp.json()["detail"]

    def test_unauthenticated_returns_401(self):
        bid = uuid4()
        client = TestClient(_build_app(authed=False), raise_server_exceptions=False)
        resp = client.post(f"/api/topics/{bid}/resolve")
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# POST /api/topics/{batch_id}/reject
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRejectBatch:
    def test_rejects_batch_with_reason(self):
        bid = uuid4()
        svc = MagicMock()
        svc.reject_batch = AsyncMock(return_value=None)
        with _patch_service(svc):
            client = TestClient(_build_app())
            resp = client.post(f"/api/topics/{bid}/reject", json={"reason": "off-brand"})
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["ok"] is True
        assert data["status"] == "expired"
        svc.reject_batch.assert_awaited_once()
        kwargs = svc.reject_batch.await_args.kwargs
        assert str(kwargs["batch_id"]) == str(bid)
        assert kwargs["reason"] == "off-brand"

    def test_reason_optional(self):
        bid = uuid4()
        svc = MagicMock()
        svc.reject_batch = AsyncMock(return_value=None)
        with _patch_service(svc):
            client = TestClient(_build_app())
            resp = client.post(f"/api/topics/{bid}/reject", json={})
        assert resp.status_code == 200, resp.text
        assert svc.reject_batch.await_args.kwargs["reason"] == ""

    def test_unauthenticated_returns_401(self):
        bid = uuid4()
        client = TestClient(_build_app(authed=False), raise_server_exceptions=False)
        resp = client.post(f"/api/topics/{bid}/reject", json={})
        assert resp.status_code == 401
