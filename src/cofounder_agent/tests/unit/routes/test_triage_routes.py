"""Unit tests for routes/triage_routes.py (Glad-Labs/poindexter#347 step 3).

Covers the failure-mapping table from the spec + idempotency cache:

- 401 when no Authorization header (delegated to ``verify_api_token``)
- 200 + cached payload on the second call for the same alert_event_id
- 503 ``no_provider`` when the configured tier has no provider wired
- 503 ``triage_disabled`` when ``ops_triage_enabled=false``
- 402 ``cost_guarded`` when ``CostGuard.preflight`` raises
- 200 with ``diagnosis=""`` (no 5xx) when the LLM returns empty

Mocks asyncpg pool, model_router, and bypasses ``verify_api_token`` via
the FastAPI dependency override the same way every other route test
does. Nothing in this file hits a real LLM, real Postgres, or real
Telegram.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from middleware.api_token_auth import verify_api_token
from routes import triage_routes
from routes.triage_routes import router, set_model_router_for_tests
from services.site_config import SiteConfig
from utils.route_utils import get_database_dependency, get_site_config_dependency


# ---------------------------------------------------------------------------
# Pool / DB mocks
# ---------------------------------------------------------------------------


def _make_pool():
    """asyncpg-style pool whose acquire() yields a connection that
    returns reasonable defaults for the firefighter_service queries.
    Tests override individual fetches when they need to.
    """
    conn = MagicMock()
    conn.fetchrow = AsyncMock(side_effect=_default_fetchrow)
    conn.fetch = AsyncMock(return_value=[])
    conn.execute = AsyncMock(return_value="OK")

    pool = MagicMock()

    @asynccontextmanager
    async def _acquire():
        yield conn

    pool.acquire = _acquire
    return pool, conn


async def _default_fetchrow(sql: str, *args):
    """Returns a minimal alert row for the firefighter context fetch."""
    upper = sql.upper()
    if "FROM ALERT_EVENTS" in upper and "WHERE ID =" in upper:
        return {
            "id": args[0],
            "alertname": "test_alert",
            "status": "firing",
            "severity": "warning",
            "category": "test",
            "labels": {"severity": "warning"},
            "annotations": {"summary": "test"},
            "starts_at": None,
            "ends_at": None,
            "fingerprint": None,
            "received_at": None,
        }
    return None


class _MockDB:
    """DB service stub — only needs ``.pool`` for the route."""

    def __init__(self, pool):
        self.pool = pool


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_app(*, site_cfg: SiteConfig | None = None, pool=None) -> FastAPI:
    if pool is None:
        pool, _ = _make_pool()
    if site_cfg is None:
        site_cfg = SiteConfig(initial_config={
            "ops_triage_enabled": "true",
            "local_llm_api_url": "http://localhost:11434",
            "ops_triage_max_context_tokens": "100000",
            "ops_triage_cache_ttl_seconds": "3600",
        })

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_database_dependency] = lambda: _MockDB(pool)
    app.dependency_overrides[get_site_config_dependency] = lambda: site_cfg
    return app


def _stub_router_factory(text="diagnosis text", model="ollama/glm-4.7-5090", tokens=42):
    """Return a router-factory the route can call with site_config."""

    def _factory(_site_config):
        router_obj = MagicMock()
        router_obj.invoke = AsyncMock(return_value={
            "text": text, "model": model, "tokens": tokens,
        })
        return router_obj

    return _factory


@pytest.fixture(autouse=True)
def _reset_module_state():
    """Wipe the cache + router-factory between tests."""
    triage_routes._cache_clear_for_tests()
    set_model_router_for_tests(None)
    yield
    triage_routes._cache_clear_for_tests()
    set_model_router_for_tests(None)


def _post_payload(alert_event_id: int = 42) -> dict[str, Any]:
    return {
        "alert_event_id": alert_event_id,
        "alertname": "test_alert",
        "severity": "warning",
        "labels": {"severity": "warning"},
        "annotations": {"summary": "test"},
    }


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestAuth:
    def test_missing_authorization_returns_401(self):
        # Build app WITHOUT overriding verify_api_token so the real
        # dependency runs and rejects the missing header.
        app = _build_app()
        # Don't override verify_api_token — let the real one fire.
        client = TestClient(app)
        resp = client.post("/api/triage", json=_post_payload())
        assert resp.status_code == 401

    def test_overridden_auth_passes(self):
        app = _build_app()
        app.dependency_overrides[verify_api_token] = lambda: "test-token"
        set_model_router_for_tests(_stub_router_factory())
        client = TestClient(app)
        resp = client.post("/api/triage", json=_post_payload())
        assert resp.status_code == 200, resp.text


# ---------------------------------------------------------------------------
# Idempotency cache
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestIdempotency:
    def test_second_call_serves_from_cache(self):
        app = _build_app()
        app.dependency_overrides[verify_api_token] = lambda: "test-token"

        # Track invocations on the router so we can assert it's hit only
        # once across the two POSTs.
        call_count = {"n": 0}

        def _factory(_cfg):
            router_obj = MagicMock()

            async def _invoke(**_kwargs):
                call_count["n"] += 1
                return {"text": "first call diagnosis", "model": "ollama/glm-4.7-5090", "tokens": 5}

            router_obj.invoke = _invoke
            return router_obj

        set_model_router_for_tests(_factory)

        client = TestClient(app)
        first = client.post("/api/triage", json=_post_payload(alert_event_id=99))
        assert first.status_code == 200
        assert first.json()["diagnosis"] == "first call diagnosis"
        assert first.json()["cached"] is False

        second = client.post("/api/triage", json=_post_payload(alert_event_id=99))
        assert second.status_code == 200
        assert second.json()["diagnosis"] == "first call diagnosis"
        assert second.json()["cached"] is True

        # Crucial: the LLM was only invoked once.
        assert call_count["n"] == 1

    def test_different_alert_id_does_not_hit_cache(self):
        app = _build_app()
        app.dependency_overrides[verify_api_token] = lambda: "test-token"
        set_model_router_for_tests(_stub_router_factory(text="diff"))
        client = TestClient(app)

        client.post("/api/triage", json=_post_payload(alert_event_id=1))
        resp = client.post("/api/triage", json=_post_payload(alert_event_id=2))
        assert resp.status_code == 200
        assert resp.json()["cached"] is False


# ---------------------------------------------------------------------------
# 503 paths
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestNoProvider:
    def test_missing_local_llm_url_returns_503(self):
        # local_llm_api_url empty -> no provider for the local-default
        # ops_triage tier.
        site_cfg = SiteConfig(initial_config={
            "ops_triage_enabled": "true",
            "local_llm_api_url": "",
        })
        app = _build_app(site_cfg=site_cfg)
        app.dependency_overrides[verify_api_token] = lambda: "test-token"
        set_model_router_for_tests(_stub_router_factory())
        client = TestClient(app)
        resp = client.post("/api/triage", json=_post_payload())
        assert resp.status_code == 503
        body = resp.json()
        # FastAPI wraps the dict detail under "detail".
        assert body["detail"]["code"] == "no_provider"
        assert "ops_triage" in body["detail"]["message"]

    def test_disabled_returns_503_triage_disabled(self):
        site_cfg = SiteConfig(initial_config={
            "ops_triage_enabled": "false",
            "local_llm_api_url": "http://localhost:11434",
        })
        app = _build_app(site_cfg=site_cfg)
        app.dependency_overrides[verify_api_token] = lambda: "test-token"
        set_model_router_for_tests(_stub_router_factory())
        client = TestClient(app)
        resp = client.post("/api/triage", json=_post_payload())
        assert resp.status_code == 503
        assert resp.json()["detail"]["code"] == "triage_disabled"


# ---------------------------------------------------------------------------
# 402 path — cost_guard
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCostGuard:
    def test_cost_guard_denial_returns_402(self, monkeypatch):
        # Force the guard to deny, even on a local URL, by patching
        # is_local_base_url + CostGuard.preflight.
        from services.cost_guard import CostGuardExhausted

        site_cfg = SiteConfig(initial_config={
            "ops_triage_enabled": "true",
            "local_llm_api_url": "https://api.example.com/v1",  # non-local
        })
        app = _build_app(site_cfg=site_cfg)
        app.dependency_overrides[verify_api_token] = lambda: "test-token"
        set_model_router_for_tests(_stub_router_factory())

        async def _denied_preflight(self, _estimate):
            raise CostGuardExhausted(
                "daily budget exhausted",
                scope="daily",
                spent_usd=10.0,
                limit_usd=10.0,
                provider="ops_triage",
                model="ops_triage",
            )

        monkeypatch.setattr(
            "services.cost_guard.CostGuard.preflight",
            _denied_preflight,
        )

        client = TestClient(app)
        resp = client.post("/api/triage", json=_post_payload())
        assert resp.status_code == 402
        body = resp.json()
        assert body["detail"]["code"] == "cost_guarded"
        assert "budget" in body["detail"]["message"].lower()


# ---------------------------------------------------------------------------
# 200 + empty diagnosis
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestEmptyDiagnosis:
    def test_empty_llm_returns_200_with_empty_diagnosis(self):
        app = _build_app()
        app.dependency_overrides[verify_api_token] = lambda: "test-token"

        # Router returns empty text — firefighter_service.run_triage will
        # produce diagnosis="". The route MUST return 200 (not 5xx).
        set_model_router_for_tests(_stub_router_factory(text=""))

        client = TestClient(app)
        resp = client.post("/api/triage", json=_post_payload())
        assert resp.status_code == 200
        body = resp.json()
        assert body["diagnosis"] == ""
        assert "model" in body and "tokens" in body and "ms" in body

    def test_router_raises_returns_200_with_empty_diagnosis(self):
        # firefighter_service.run_triage swallows router exceptions and
        # returns diagnosis="" — the route must propagate that as 200.
        app = _build_app()
        app.dependency_overrides[verify_api_token] = lambda: "test-token"

        def _factory(_cfg):
            router_obj = MagicMock()
            router_obj.invoke = AsyncMock(side_effect=RuntimeError("ollama down"))
            return router_obj

        set_model_router_for_tests(_factory)

        client = TestClient(app)
        resp = client.post("/api/triage", json=_post_payload())
        assert resp.status_code == 200
        assert resp.json()["diagnosis"] == ""


# ---------------------------------------------------------------------------
# Happy path basics
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestHappyPath:
    def test_returns_diagnosis_payload(self):
        app = _build_app()
        app.dependency_overrides[verify_api_token] = lambda: "test-token"
        set_model_router_for_tests(_stub_router_factory(
            text="Likely a probe timeout — the worker /api/health is unreachable.",
            model="ollama/glm-4.7-5090", tokens=128,
        ))
        client = TestClient(app)
        resp = client.post("/api/triage", json=_post_payload())
        assert resp.status_code == 200
        body = resp.json()
        assert "Likely a probe timeout" in body["diagnosis"]
        assert body["model"] == "ollama/glm-4.7-5090"
        assert body["tokens"] == 128
        assert body["ms"] >= 0
        assert body["cached"] is False
