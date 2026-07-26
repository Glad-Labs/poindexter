"""routes/gpu_queue_routes.py — HTTP contract for the GPU scheduler's
observable state (poindexter#914 P0, plan Task A5)."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from middleware.api_token_auth import verify_api_token
from routes.gpu_queue_routes import router

pytestmark = pytest.mark.unit


def _build_app(*, authed: bool = True) -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    if authed:
        app.dependency_overrides[verify_api_token] = lambda: "test-token"
    return app


class TestGetGpuQueue:
    def test_empty_state_is_honest(self):
        with (
            patch("routes.gpu_queue_routes.list_waiters", new=AsyncMock(return_value=[])),
            patch("routes.gpu_queue_routes.list_stats", new=AsyncMock(return_value=[])),
            patch("routes.gpu_queue_routes._current_holder", return_value=None),
        ):
            resp = TestClient(_build_app()).get("/api/gpu/queue")
        assert resp.status_code == 200
        assert resp.json() == {"holder": None, "waiters": [], "stats": []}

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
        from routes.gpu_queue_routes import GpuHolder

        with (
            patch("routes.gpu_queue_routes.list_waiters", new=AsyncMock(return_value=waiters)),
            patch("routes.gpu_queue_routes.list_stats", new=AsyncMock(return_value=stats)),
            patch(
                "routes.gpu_queue_routes._current_holder",
                return_value=GpuHolder(owner="image_gen", model="z-image", held_for_s=5.0),
            ),
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
        import routes.gpu_queue_routes as m

        with (
            patch.object(m.gpu, "_current_owner", "ollama"),
            patch.object(m.gpu, "_current_model", "gemma"),
        ):
            holder = m._current_holder()
        assert holder is not None
        assert holder.owner == "ollama" and holder.model == "gemma"
        assert holder.held_for_s >= 0.0

        with patch.object(m.gpu, "_current_owner", None):
            assert m._current_holder() is None

    def test_requires_auth(self):
        resp = TestClient(_build_app(authed=False)).get("/api/gpu/queue")
        assert resp.status_code in (401, 403)
