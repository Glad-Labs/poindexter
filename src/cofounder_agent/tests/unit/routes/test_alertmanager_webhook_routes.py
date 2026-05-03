"""Unit tests for ``routes/alertmanager_webhook_routes.py``.

We stub out the asyncpg pool so each test can assert on inserts,
pageable counts, and remediation lookups without spinning up
Postgres. The handler's goal is to be robust: malformed alerts,
missing labels, and failing sub-steps must never 5xx — the webhook
is a hot path Alertmanager will retry.

Post-unification (Glad-Labs/poindexter#340 prep): the webhook NO
LONGER dispatches to Telegram/Discord. That responsibility moved to
the brain daemon's alert_dispatcher loop. Tests below assert that
``notify_operator`` is NOT called, which is the new contract.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from routes.alertmanager_webhook_routes import (
    _format_alert_message,
    _should_page_operator,
    router,
    verify_alertmanager_token,
)
from utils.route_utils import get_database_dependency

# ---------------------------------------------------------------------------
# Fake asyncpg pool — records every SQL + args; fetchval returns scripted values.
# ---------------------------------------------------------------------------


class _FakeConn:
    def __init__(self, parent: _FakePool):
        self._parent = parent

    async def execute(self, sql: str, *args: Any) -> str:
        self._parent.executes.append((sql, args))
        return "OK"

    async def fetchval(self, _sql: str, *args: Any) -> Any:
        key = args[0] if args else None
        return self._parent.settings.get(key)


class _FakePoolCtx:
    def __init__(self, parent: _FakePool):
        self._parent = parent

    async def __aenter__(self) -> _FakeConn:
        return _FakeConn(self._parent)

    async def __aexit__(self, *_exc: Any) -> None:
        return None


class _FakePool:
    def __init__(self, settings: dict[str, str] | None = None):
        self.executes: list[tuple[str, tuple[Any, ...]]] = []
        self.settings: dict[str, str] = settings or {}

    def acquire(self) -> _FakePoolCtx:
        return _FakePoolCtx(self)


class _FakeDb:
    def __init__(self, pool: _FakePool):
        self.pool = pool


def _build_app(pool: _FakePool) -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_database_dependency] = lambda: _FakeDb(pool)
    # Phase-D4 auth hardening (commit 05f828ea) gated the webhook on a
    # Bearer token from app_settings. These tests don't care about auth
    # — they exercise the persistence + notify + remediation paths —
    # so we override the dependency to a no-op. Tests that DO care
    # about auth live in test_alertmanager_webhook_auth.py.
    app.dependency_overrides[verify_alertmanager_token] = lambda: None
    return app


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


class TestShouldPageOperator:
    def test_critical_pages(self):
        assert _should_page_operator({
            "status": "firing",
            "labels": {"severity": "critical", "category": "business"},
        })

    def test_infrastructure_pages_even_when_warning(self):
        assert _should_page_operator({
            "status": "firing",
            "labels": {"severity": "warning", "category": "infrastructure"},
        })

    def test_resolved_does_not_page(self):
        assert not _should_page_operator({
            "status": "resolved",
            "labels": {"severity": "critical", "category": "infrastructure"},
        })

    def test_info_content_does_not_page(self):
        assert not _should_page_operator({
            "status": "firing",
            "labels": {"severity": "info", "category": "content"},
        })


class TestFormatAlertMessage:
    def test_includes_header_and_summary(self):
        msg = _format_alert_message({
            "status": "firing",
            "labels": {"alertname": "Foo", "severity": "critical"},
            "annotations": {"summary": "short summary"},
        })
        assert "[FIRING · critical]" in msg
        assert "Foo" in msg
        assert "short summary" in msg

    def test_appends_description(self):
        msg = _format_alert_message({
            "status": "firing",
            "labels": {"alertname": "Foo", "severity": "warning"},
            "annotations": {"summary": "s", "description": "long desc"},
        })
        assert "long desc" in msg


# ---------------------------------------------------------------------------
# Endpoint behavior
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestWebhookEndpoint:
    def _post(self, client: TestClient, payload: dict[str, Any]):
        return client.post("/api/webhooks/alertmanager", json=payload)

    def test_persists_each_alert(self):
        pool = _FakePool()
        with patch(
            "services.integrations.operator_notify.notify_operator",
            new=AsyncMock(return_value=None),
        ):
            client = TestClient(_build_app(pool))
            resp = self._post(client, {
                "status": "firing",
                "alerts": [
                    {
                        "status": "firing",
                        "labels": {"alertname": "A", "severity": "info"},
                        "annotations": {},
                        "startsAt": "2026-04-19T17:00:00Z",
                        "endsAt": "0001-01-01T00:00:00Z",
                        "fingerprint": "abc",
                    }
                ],
            })
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["count"] == 1
        assert data["persisted"] == 1
        # One CREATE TABLE + one INSERT in execute log
        sql_fragments = [sql for sql, _ in pool.executes]
        assert any("CREATE TABLE" in s for s in sql_fragments)
        assert any("INSERT INTO alert_events" in s for s in sql_fragments)

    def test_critical_marked_pageable_but_not_dispatched(self):
        """Critical alerts increment ``pageable`` but DO NOT dispatch.

        Dispatch is the brain daemon's job (brain/alert_dispatcher.py).
        The webhook persists + counts; it must not call notify_operator.
        """
        pool = _FakePool()
        mock_notify = AsyncMock(return_value=None)
        with patch(
            "services.integrations.operator_notify.notify_operator",
            new=mock_notify,
        ):
            client = TestClient(_build_app(pool))
            resp = self._post(client, {
                "alerts": [
                    {
                        "status": "firing",
                        "labels": {
                            "alertname": "PoindexterPostgresDown",
                            "severity": "critical",
                            "category": "infrastructure",
                        },
                        "annotations": {"summary": "pg down"},
                    }
                ],
            })
        assert resp.status_code == 200
        body = resp.json()
        # Pageable count surfaces routing intent without actually paging.
        assert body["paged"] == 1  # backwards-compat alias
        assert body["pageable"] == 1
        # Webhook MUST NOT call notify_operator — that's the brain's job now.
        mock_notify.assert_not_awaited()

    def test_resolved_alert_does_not_count_as_pageable(self):
        pool = _FakePool()
        mock_notify = AsyncMock(return_value=None)
        with patch(
            "services.integrations.operator_notify.notify_operator",
            new=mock_notify,
        ):
            client = TestClient(_build_app(pool))
            resp = self._post(client, {
                "alerts": [
                    {
                        "status": "resolved",
                        "labels": {"alertname": "A", "severity": "critical",
                                   "category": "infrastructure"},
                        "annotations": {},
                    }
                ],
            })
        assert resp.status_code == 200
        body = resp.json()
        assert body["paged"] == 0
        assert body["pageable"] == 0
        mock_notify.assert_not_awaited()

    def test_dispatch_helper_was_removed(self):
        """Ensure the legacy ``_dispatch_to_operator`` helper is gone.

        Hard-fails if a future commit re-introduces inline dispatch in
        the route — keeping this contract enforced via test rather than
        comment-only.
        """
        from routes import alertmanager_webhook_routes as m
        assert not hasattr(m, "_dispatch_to_operator"), (
            "_dispatch_to_operator must stay deleted — dispatch is the "
            "brain daemon's responsibility (brain/alert_dispatcher.py)."
        )

    def test_remediation_scaffold_fires_when_configured(self):
        pool = _FakePool(settings={
            "plugin.remediation.PoindexterOllamaDown": json.dumps({
                "enabled": True,
                "action": "restart_container",
                "params": {"container": "ollama"},
            }),
        })
        mock_notify = AsyncMock(return_value=None)
        with patch(
            "services.integrations.operator_notify.notify_operator",
            new=mock_notify,
        ):
            client = TestClient(_build_app(pool))
            resp = self._post(client, {
                "alerts": [
                    {
                        "status": "firing",
                        "labels": {"alertname": "PoindexterOllamaDown",
                                   "severity": "warning",
                                   "category": "infrastructure"},
                        "annotations": {},
                    }
                ],
            })
        assert resp.status_code == 200
        assert resp.json()["remediated"] == 1

    def test_remediation_skipped_when_disabled(self):
        pool = _FakePool(settings={
            "plugin.remediation.PoindexterOllamaDown": json.dumps({"enabled": False}),
        })
        with patch(
            "services.integrations.operator_notify.notify_operator",
            new=AsyncMock(return_value=None),
        ):
            client = TestClient(_build_app(pool))
            resp = self._post(client, {
                "alerts": [
                    {
                        "status": "firing",
                        "labels": {"alertname": "PoindexterOllamaDown",
                                   "severity": "warning",
                                   "category": "infrastructure"},
                        "annotations": {},
                    }
                ],
            })
        assert resp.json()["remediated"] == 0

    def test_tolerates_malformed_alerts_entry(self):
        pool = _FakePool()
        with patch(
            "services.integrations.operator_notify.notify_operator",
            new=AsyncMock(return_value=None),
        ):
            client = TestClient(_build_app(pool))
            # alerts not a list
            resp = self._post(client, {"alerts": "nope"})
        assert resp.status_code == 200
        assert resp.json()["ok"] is False

    def test_empty_alerts_list_is_ok(self):
        pool = _FakePool()
        client = TestClient(_build_app(pool))
        resp = self._post(client, {"alerts": []})
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 0
        assert data["persisted"] == 0
        assert data["paged"] == 0
