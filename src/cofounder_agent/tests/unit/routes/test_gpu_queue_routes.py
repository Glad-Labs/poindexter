"""routes/gpu_queue_routes.py — HTTP contract for the GPU scheduler's
observable state (poindexter#914 P0, plan Task A5).

The holder comes from POSTGRES. It used to come from `gpu._current_owner`, a
module global describing whichever process answered the request, so the
console (served by the FastAPI worker) printed "lock free" while a render in
another container held the card — directly above a DB-sourced waiter list
showing that holder's own queue. The tests below pin the fix: pg wins, the
in-process view is a labelled fallback, and an untagged holder is still a
holder.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from middleware.api_token_auth import verify_api_token
from poindexter.routes.gpu_queue_routes import router

pytestmark = pytest.mark.unit


def _pg(**over):
    """One `list_pg_holders` row."""
    row = {
        "owner": "video",
        "phase": "media_render",
        "task_id": "f555bedc",
        "pid": 7564,
        "backend_pid": 245475,
        "client_addr": "172.18.0.35",
        "application_name": "poindexter-gpu:video:media_render:f555bedc:pid7564",
        "held_for_s": 1419.2,
        "keys": [7777777777, 10738779002],
        "exclusive": True,
    }
    row.update(over)
    return row


def _holders(rows):
    return patch(
        "poindexter.routes.gpu_queue_routes.list_pg_holders",
        new=AsyncMock(return_value=rows),
    )


def _build_app(*, authed: bool = True) -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    if authed:
        app.dependency_overrides[verify_api_token] = lambda: "test-token"
    return app


class TestGetGpuQueue:
    def test_empty_state_is_honest(self):
        with (
            patch("poindexter.routes.gpu_queue_routes.list_waiters", new=AsyncMock(return_value=[])),
            patch("poindexter.routes.gpu_queue_routes.list_stats", new=AsyncMock(return_value=[])),
            patch("poindexter.routes.gpu_queue_routes._current_holder", return_value=None),
            _holders([]),
        ):
            resp = TestClient(_build_app()).get("/api/gpu/queue")
        assert resp.status_code == 200
        assert resp.json() == {
            "holder": None,
            "holders": [],
            "waiters": [],
            "stats": [],
        }

    def test_holder_comes_from_postgres_not_this_process(self):
        """The bug, pinned: waiters present, nothing holding the lock in THIS
        process, and a real cross-process holder that must still be named."""
        waiters = [
            {
                "pid": 20816,
                "owner": "ollama",
                "model": "gemma",
                "phase": "generate_content",
                "priority": "pipeline",
                "waiting_s": 1338.0,
            }
        ]
        with (
            patch("poindexter.routes.gpu_queue_routes.list_waiters", new=AsyncMock(return_value=waiters)),
            patch("poindexter.routes.gpu_queue_routes.list_stats", new=AsyncMock(return_value=[])),
            # This process holds nothing — exactly the console's situation.
            patch("poindexter.routes.gpu_queue_routes._current_holder", return_value=None),
            _holders([_pg()]),
        ):
            body = TestClient(_build_app()).get("/api/gpu/queue").json()

        assert body["holder"]["owner"] == "video"
        assert body["holder"]["phase"] == "media_render"
        assert body["holder"]["task_id"] == "f555bedc"
        assert body["holder"]["held_for_s"] == 1419.2
        assert body["holder"]["source"] == "postgres"
        assert body["holder"]["keys"] == [7777777777, 10738779002]
        assert body["waiters"][0]["waiting_s"] == 1338.0

    def test_every_scoped_holder_is_listed(self):
        """Device scoping permits two scoped sessions on different cards.
        `holder` is the head for old consumers; `holders` carries both."""
        rows = [
            _pg(owner="video", keys=[7777777777, 10738779002]),
            _pg(owner="ollama", phase="writer", keys=[7777777777, 10738779003]),
        ]
        with (
            patch("poindexter.routes.gpu_queue_routes.list_waiters", new=AsyncMock(return_value=[])),
            patch("poindexter.routes.gpu_queue_routes.list_stats", new=AsyncMock(return_value=[])),
            _holders(rows),
        ):
            body = TestClient(_build_app()).get("/api/gpu/queue").json()

        assert [h["owner"] for h in body["holders"]] == ["video", "ollama"]
        assert body["holder"]["owner"] == "video"

    def test_untagged_holder_is_still_a_holder(self):
        """A session holding the lock without our tag must never render as an
        empty lock — "held by someone who won't say who" is a different answer
        from "free", and collapsing them is the original bug."""
        row = _pg(owner=None, phase=None, task_id=None, pid=None, application_name="")
        with (
            patch("poindexter.routes.gpu_queue_routes.list_waiters", new=AsyncMock(return_value=[])),
            patch("poindexter.routes.gpu_queue_routes.list_stats", new=AsyncMock(return_value=[])),
            _holders([row]),
        ):
            body = TestClient(_build_app()).get("/api/gpu/queue").json()

        assert body["holder"]["owner"] == "unknown"
        assert body["holder"]["pid"] == 245475  # the backend pid still locates it

    def test_in_process_view_is_a_labelled_fallback(self):
        """Postgres unreachable (honest-empty []) but this process holds the
        lock: report it, and say the answer is process-local."""
        import poindexter.routes.gpu_queue_routes as m

        with (
            patch("poindexter.routes.gpu_queue_routes.list_waiters", new=AsyncMock(return_value=[])),
            patch("poindexter.routes.gpu_queue_routes.list_stats", new=AsyncMock(return_value=[])),
            _holders([]),
            patch.object(m.gpu, "_current_owner", "image_gen"),
            patch.object(m.gpu, "_current_model", "z-image"),
        ):
            body = TestClient(_build_app()).get("/api/gpu/queue").json()

        assert body["holder"]["owner"] == "image_gen"
        assert body["holder"]["source"] == "in_process"

    def test_postgres_wins_over_the_in_process_view(self):
        import poindexter.routes.gpu_queue_routes as m

        with (
            patch("poindexter.routes.gpu_queue_routes.list_waiters", new=AsyncMock(return_value=[])),
            patch("poindexter.routes.gpu_queue_routes.list_stats", new=AsyncMock(return_value=[])),
            _holders([_pg()]),
            patch.object(m.gpu, "_current_owner", "image_gen"),
        ):
            body = TestClient(_build_app()).get("/api/gpu/queue").json()

        assert body["holder"]["owner"] == "video"
        assert len(body["holders"]) == 1

    def test_full_state_shape(self):
        waiters = [
            {
                "pid": 42,
                "owner": "ollama",
                "model": "gemma",
                "phase": "writer",
                "priority": "pipeline",
                "waiting_s": 12.34,
            }
        ]
        stats = [
            {
                "owner": "video",
                "phase": "video",
                "samples": 9,
                "ewma_ms": 120000.0,
                "p50_ms": 110000.0,
                "p90_ms": 300000.0,
                "updated_at": datetime.now(timezone.utc),
            }
        ]
        from poindexter.routes.gpu_queue_routes import GpuHolder

        with (
            patch("poindexter.routes.gpu_queue_routes.list_waiters", new=AsyncMock(return_value=waiters)),
            patch("poindexter.routes.gpu_queue_routes.list_stats", new=AsyncMock(return_value=stats)),
            patch(
                "poindexter.routes.gpu_queue_routes._current_holder",
                return_value=GpuHolder(owner="image_gen", model="z-image", held_for_s=5.0),
            ),
            _holders([]),
        ):
            resp = TestClient(_build_app()).get("/api/gpu/queue")

        assert resp.status_code == 200
        body = resp.json()
        assert body["holder"]["owner"] == "image_gen"
        assert body["waiters"][0]["pid"] == 42
        assert body["waiters"][0]["waiting_s"] == 12.3  # rounded
        assert body["stats"][0]["p90_ms"] == 300000.0

    def test_holder_derived_from_scheduler_state(self):
        """_current_holder reads the live scheduler singleton's fields."""
        import poindexter.routes.gpu_queue_routes as m

        with (
            patch.object(m.gpu, "_current_owner", "ollama"),
            patch.object(m.gpu, "_current_model", "gemma"),
        ):
            holder = m._current_holder()
        assert holder is not None
        assert holder.owner == "ollama" and holder.model == "gemma"
        assert holder.held_for_s >= 0.0

        assert holder.source == "in_process"

        with patch.object(m.gpu, "_current_owner", None):
            assert m._current_holder() is None

    def test_requires_auth(self):
        resp = TestClient(_build_app(authed=False)).get("/api/gpu/queue")
        assert resp.status_code in (401, 403)


class TestHolderTagParsing:
    """`parse_holder_tag_fields` is what turns a stamped connection back into
    an operator-legible holder. The tag shape is duplicated by value in
    brain/health_probes.py, so its round-trip is pinned here."""

    def test_round_trips_a_worker_tag(self):
        from poindexter.services.gpu_scheduler import (
            _holder_tag,
            parse_holder_tag_fields,
        )

        tag = _holder_tag("video", "media_render", "f555bedc-1111-2222-3333-444444444444")
        fields = parse_holder_tag_fields(tag["application_name"])
        assert fields["owner"] == "video"
        assert fields["phase"] == "media_render"
        assert fields["task_id"] == "f555bedc"
        assert isinstance(fields["pid"], int)

    def test_round_trips_a_brain_probe_tag(self):
        """The brain cannot import gpu_scheduler (its container ships stdlib +
        asyncpg only), so it rebuilds this tag by hand. If the shapes drift,
        a brain-held lock goes back to reading as an anonymous holder."""
        import os

        from poindexter.services.gpu_scheduler import parse_holder_tag_fields

        tag = f"poindexter-gpu:brain_probe:content_gen:pid{os.getpid()}"
        fields = parse_holder_tag_fields(tag)
        assert fields["owner"] == "brain_probe"
        assert fields["phase"] == "content_gen"
        assert fields["pid"] == os.getpid()

    def test_untagged_session_keeps_its_raw_name(self):
        from poindexter.services.gpu_scheduler import parse_holder_tag_fields

        fields = parse_holder_tag_fields("psql")
        assert fields["owner"] is None
        assert fields["application_name"] == "psql"
