"""Unit tests for ``modules.finance.routes``.

Module v1 Phase 4 wiring (Glad-Labs/poindexter#490). Pins the
operator-only ``/api/finance/*`` route shapes against the
MercuryClient surface. No real network calls — the client is patched
via ``modules.finance.routes.MercuryClient`` so we exercise the route
handler logic (auth, config gating, response shape) end-to-end.

Tests cover:
- ``GET /api/finance/healthcheck`` — disabled, unconfigured, ok,
  auth_failed, upstream_error paths
- ``GET /api/finance/balances`` — happy path + 503 (disabled) +
  503 (no token) + 401 + 502
- ``GET /api/finance/transactions`` — happy path, account_id filter,
  lookback_days bound check
- ``FinanceModule.register_routes`` — adds the router to a real
  FastAPI app + fails loud on a non-FastAPI argument
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from middleware.api_token_auth import verify_api_token
from modules.finance.mercury_client import (
    MercuryAccount,
    MercuryAPIError,
    MercuryAuthError,
    MercuryTransaction,
)
from modules.finance.routes import router as finance_router
from utils.route_utils import get_database_dependency


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _FakeConn:
    """Minimal async-context-manager-compatible asyncpg connection stub.

    The route reads two things via ``pool.acquire()``:
    1) ``SELECT value FROM app_settings WHERE key = 'mercury_enabled'``
    2) ``get_secret(conn, 'mercury_api_token')`` — which reads
       ``SELECT value, is_secret FROM app_settings WHERE key = $1``

    We synthesize both with ``fetchrow`` so the lazy-imported
    ``plugins.secrets.get_secret`` is satisfied for the non-secret
    case (is_secret=false → value returned verbatim, no decryption
    needed).
    """

    def __init__(self, enabled: str | None, token: str | None):
        self._enabled = enabled
        self._token = token

    async def fetchrow(self, query: str, *args):
        if "mercury_enabled" in query:
            return None if self._enabled is None else {"value": self._enabled}
        if "is_secret" in query and args and args[0] == "mercury_api_token":
            # The is_secret=false branch returns the value verbatim, so
            # the test doesn't need the pgcrypto plumbing — we just
            # report it as plain.
            if self._token is None:
                return None
            return {"value": self._token, "is_secret": False}
        return None


class _FakePool:
    """Async-context-manager wrapper that yields a fresh ``_FakeConn``."""

    def __init__(self, enabled: str | None, token: str | None):
        self._enabled = enabled
        self._token = token

    def acquire(self):
        # ``async with pool.acquire() as conn:`` — return an async ctx mgr.
        @asynccontextmanager
        async def _ctx():
            yield _FakeConn(self._enabled, self._token)

        return _ctx()


def _make_db(enabled: str | None = "true", token: str | None = "tok-abc"):
    """Return a stub DatabaseService exposing ``.pool``."""
    db = MagicMock()
    db.pool = _FakePool(enabled, token)
    return db


def _build_app(db) -> FastAPI:
    """Build a minimal FastAPI app with the finance router + auth/db
    dependencies overridden."""
    app = FastAPI()
    app.include_router(finance_router)
    app.dependency_overrides[verify_api_token] = lambda: "test-token"
    app.dependency_overrides[get_database_dependency] = lambda: db
    return app


def _mock_mercury_client(
    *,
    accounts: list[MercuryAccount] | None = None,
    transactions: dict[str, list[MercuryTransaction]] | None = None,
    list_accounts_exc: Exception | None = None,
    list_txns_exc: Exception | None = None,
):
    """Return a MagicMock that replaces ``MercuryClient`` and yields a
    client whose ``list_accounts`` / ``list_transactions`` return the
    canned data (or raise the canned exception)."""

    instance = MagicMock()

    if list_accounts_exc is not None:
        instance.list_accounts = AsyncMock(side_effect=list_accounts_exc)
    else:
        instance.list_accounts = AsyncMock(return_value=accounts or [])

    txn_map = transactions or {}

    async def _list_txns(account_id, **kwargs):
        if list_txns_exc is not None:
            raise list_txns_exc
        return txn_map.get(account_id, [])

    instance.list_transactions = AsyncMock(side_effect=_list_txns)

    # The route uses ``async with MercuryClient(token=...) as m`` — so
    # the constructor must return an async context manager that yields
    # ``instance``. We use the same MagicMock for both for simplicity.
    cls = MagicMock()
    cls.return_value.__aenter__ = AsyncMock(return_value=instance)
    cls.return_value.__aexit__ = AsyncMock(return_value=None)
    return cls


_SAMPLE_ACCOUNTS = [
    MercuryAccount(
        id="acc-1",
        name="Glad Labs Checking",
        type="checking",
        current_balance=12345.67,
        available_balance=12300.0,
        kind="businessChecking",
    ),
    MercuryAccount(
        id="acc-2",
        name="Glad Labs Savings",
        type="savings",
        current_balance=50000.0,
        available_balance=50000.0,
        kind="businessSavings",
    ),
]


_SAMPLE_TXNS = {
    "acc-1": [
        MercuryTransaction(
            id="txn-1",
            account_id="acc-1",
            amount=-29.99,
            posted_at="2026-05-15T10:00:00Z",
            counterparty="AWS",
            status="posted",
        ),
        MercuryTransaction(
            id="txn-2",
            account_id="acc-1",
            amount=1000.0,
            posted_at="2026-05-14T08:00:00Z",
            counterparty="Stripe Payout",
            status="posted",
        ),
    ],
    "acc-2": [
        MercuryTransaction(
            id="txn-3",
            account_id="acc-2",
            amount=5000.0,
            posted_at="2026-05-13T12:00:00Z",
            counterparty="Internal Transfer",
            status="posted",
        ),
    ],
}


# ---------------------------------------------------------------------------
# /api/finance/healthcheck
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestFinanceHealthcheck:
    def test_disabled_returns_status_disabled(self):
        """When mercury_enabled=false, the route never calls Mercury —
        it short-circuits with status=disabled. Critical: returning 200
        keeps uptime monitors happy (a config gap shouldn't trip an
        external alert; it's surfaced via the body's status field)."""
        db = _make_db(enabled="false", token=None)
        with TestClient(_build_app(db)) as client:
            resp = client.get(
                "/api/finance/healthcheck",
                headers={"Authorization": "Bearer test-token"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "disabled"
        assert body["enabled"] is False

    def test_unconfigured_returns_status_unconfigured(self):
        """Enabled but token row empty — the body's detail spells out
        the exact `poindexter settings set` command to fix it."""
        db = _make_db(enabled="true", token=None)
        with TestClient(_build_app(db)) as client:
            resp = client.get(
                "/api/finance/healthcheck",
                headers={"Authorization": "Bearer test-token"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "unconfigured"
        assert "poindexter settings set" in body["detail"]

    def test_ok_when_mercury_reachable(self):
        db = _make_db(enabled="true", token="tok-abc")
        mock_cls = _mock_mercury_client(accounts=_SAMPLE_ACCOUNTS)

        with patch("modules.finance.routes.MercuryClient", mock_cls):
            with TestClient(_build_app(db)) as client:
                resp = client.get(
                    "/api/finance/healthcheck",
                    headers={"Authorization": "Bearer test-token"},
                )

        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert body["account_count"] == 2

    def test_auth_failed_returns_status_auth_failed(self):
        """Mercury 401/403 → body status=auth_failed, but HTTP 200 so the
        operator dashboard reads the precise status string."""
        db = _make_db(enabled="true", token="tok-abc")
        mock_cls = _mock_mercury_client(
            list_accounts_exc=MercuryAuthError("token revoked"),
        )
        with patch("modules.finance.routes.MercuryClient", mock_cls):
            with TestClient(_build_app(db)) as client:
                resp = client.get(
                    "/api/finance/healthcheck",
                    headers={"Authorization": "Bearer test-token"},
                )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "auth_failed"

    def test_upstream_error_returns_status_upstream_error(self):
        db = _make_db(enabled="true", token="tok-abc")
        mock_cls = _mock_mercury_client(
            list_accounts_exc=MercuryAPIError("Mercury 503"),
        )
        with patch("modules.finance.routes.MercuryClient", mock_cls):
            with TestClient(_build_app(db)) as client:
                resp = client.get(
                    "/api/finance/healthcheck",
                    headers={"Authorization": "Bearer test-token"},
                )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "upstream_error"


# ---------------------------------------------------------------------------
# /api/finance/balances
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestFinanceBalances:
    def test_happy_path_returns_aggregated_balances(self):
        db = _make_db(enabled="true", token="tok-abc")
        mock_cls = _mock_mercury_client(accounts=_SAMPLE_ACCOUNTS)
        with patch("modules.finance.routes.MercuryClient", mock_cls):
            with TestClient(_build_app(db)) as client:
                resp = client.get(
                    "/api/finance/balances",
                    headers={"Authorization": "Bearer test-token"},
                )
        assert resp.status_code == 200
        body = resp.json()
        assert body["account_count"] == 2
        # Sum: 12345.67 + 50000.0 = 62345.67
        assert body["total_current_balance"] == pytest.approx(62345.67)
        assert body["total_available_balance"] == pytest.approx(62300.0)
        assert {a["id"] for a in body["accounts"]} == {"acc-1", "acc-2"}

    def test_disabled_returns_503_with_remediation(self):
        db = _make_db(enabled="false", token=None)
        with TestClient(_build_app(db)) as client:
            resp = client.get(
                "/api/finance/balances",
                headers={"Authorization": "Bearer test-token"},
            )
        assert resp.status_code == 503
        # The detail tells the operator exactly which CLI fixes it.
        assert "mercury_enabled" in resp.json()["detail"]

    def test_no_token_returns_503_with_remediation(self):
        db = _make_db(enabled="true", token=None)
        with TestClient(_build_app(db)) as client:
            resp = client.get(
                "/api/finance/balances",
                headers={"Authorization": "Bearer test-token"},
            )
        assert resp.status_code == 503
        assert "mercury_api_token" in resp.json()["detail"]

    def test_mercury_auth_failure_returns_401(self):
        """Mercury 401 propagates as a route-level 401 so the operator
        retries token rotation, not a generic 502 retry-with-backoff."""
        db = _make_db(enabled="true", token="tok-abc")
        mock_cls = _mock_mercury_client(
            list_accounts_exc=MercuryAuthError("revoked"),
        )
        with patch("modules.finance.routes.MercuryClient", mock_cls):
            with TestClient(_build_app(db)) as client:
                resp = client.get(
                    "/api/finance/balances",
                    headers={"Authorization": "Bearer test-token"},
                )
        assert resp.status_code == 401

    def test_mercury_api_error_returns_502(self):
        db = _make_db(enabled="true", token="tok-abc")
        mock_cls = _mock_mercury_client(
            list_accounts_exc=MercuryAPIError("Mercury 5xx"),
        )
        with patch("modules.finance.routes.MercuryClient", mock_cls):
            with TestClient(_build_app(db)) as client:
                resp = client.get(
                    "/api/finance/balances",
                    headers={"Authorization": "Bearer test-token"},
                )
        assert resp.status_code == 502


# ---------------------------------------------------------------------------
# /api/finance/transactions
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestFinanceTransactions:
    def test_happy_path_merges_all_accounts(self):
        db = _make_db(enabled="true", token="tok-abc")
        mock_cls = _mock_mercury_client(
            accounts=_SAMPLE_ACCOUNTS,
            transactions=_SAMPLE_TXNS,
        )
        with patch("modules.finance.routes.MercuryClient", mock_cls):
            with TestClient(_build_app(db)) as client:
                resp = client.get(
                    "/api/finance/transactions",
                    headers={"Authorization": "Bearer test-token"},
                )
        assert resp.status_code == 200
        body = resp.json()
        assert body["count"] == 3
        # Sorted DESC by posted_at — first row should be the most recent.
        assert body["transactions"][0]["id"] == "txn-1"
        assert body["transactions"][-1]["id"] == "txn-3"
        assert body["lookback_days"] == 14

    def test_account_id_filter_skips_list_accounts(self):
        """When account_id is provided, only list_transactions is called
        — saves one Mercury round-trip."""
        db = _make_db(enabled="true", token="tok-abc")
        mock_cls = _mock_mercury_client(
            accounts=_SAMPLE_ACCOUNTS,
            transactions=_SAMPLE_TXNS,
        )
        with patch("modules.finance.routes.MercuryClient", mock_cls):
            with TestClient(_build_app(db)) as client:
                resp = client.get(
                    "/api/finance/transactions?account_id=acc-1",
                    headers={"Authorization": "Bearer test-token"},
                )
        assert resp.status_code == 200
        body = resp.json()
        # Only acc-1's two transactions
        assert body["count"] == 2
        assert {t["account_id"] for t in body["transactions"]} == {"acc-1"}

    def test_lookback_days_out_of_range_returns_422(self):
        """FastAPI's Query(ge=, le=) validates the bounds. 0 days =
        rejected; 366 days = rejected."""
        db = _make_db(enabled="true", token="tok-abc")
        with TestClient(_build_app(db)) as client:
            resp_low = client.get(
                "/api/finance/transactions?lookback_days=0",
                headers={"Authorization": "Bearer test-token"},
            )
            resp_high = client.get(
                "/api/finance/transactions?lookback_days=366",
                headers={"Authorization": "Bearer test-token"},
            )
        assert resp_low.status_code == 422
        assert resp_high.status_code == 422

    def test_disabled_returns_503(self):
        db = _make_db(enabled="false", token=None)
        with TestClient(_build_app(db)) as client:
            resp = client.get(
                "/api/finance/transactions",
                headers={"Authorization": "Bearer test-token"},
            )
        assert resp.status_code == 503


# ---------------------------------------------------------------------------
# FinanceModule.register_routes
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestFinanceModuleRegisterRoutes:
    def test_register_routes_mounts_finance_router(self):
        """register_routes(app) should mount /api/finance/* on the app.
        We assert via app.routes — the simplest contract check."""
        from modules.finance.finance_module import FinanceModule

        app = FastAPI()
        FinanceModule().register_routes(app)

        finance_paths = [r.path for r in app.routes if r.path.startswith("/api/finance")]
        assert "/api/finance/healthcheck" in finance_paths
        assert "/api/finance/balances" in finance_paths
        assert "/api/finance/transactions" in finance_paths

    def test_register_routes_raises_on_non_fastapi_arg(self):
        """Per feedback_no_silent_defaults — a wrong host object must
        fail loud, not silently no-op."""
        from modules.finance.finance_module import FinanceModule

        with pytest.raises(RuntimeError, match="include_router"):
            FinanceModule().register_routes(object())
