"""``finance`` module — operator-only read-only HTTP routes.

Module v1 Phase 4 wiring (Glad-Labs/poindexter#490). Surfaces the
``MercuryClient`` data to the operator via three endpoints under
``/api/finance/*``:

- ``GET  /api/finance/healthcheck``   — Mercury reachability + token
                                        sanity (does NOT page Mercury
                                        when ``mercury_enabled=false``;
                                        reports config status only).
- ``GET  /api/finance/balances``      — every account visible to the
                                        token, with current + available
                                        balances.
- ``GET  /api/finance/transactions``  — recent transactions, defaults to
                                        the 14-day lookback that mirrors
                                        ``PollMercuryJob``.

Authentication: ``Depends(verify_api_token)`` — the same OAuth-JWT path
the rest of the operator surface uses (see
``middleware/api_token_auth.py`` + ``services/auth/oauth_issuer.py``).
The legacy static-Bearer fallback was removed in Phase 3 #249, so every
caller mints a JWT through ``POST /token``.

Why ``visibility="private"`` matters here: this entire file ships in
the glad-labs-stack overlay only. The sync filter that builds the
public ``Glad-Labs/poindexter`` mirror strips ``modules/finance/`` and
all of its descendants — including this routes file. Operators who
clone the public mirror never see ``/api/finance/*``.

Failure posture: every endpoint distinguishes config-missing (Mercury
disabled / no token in app_settings) from upstream-error (Mercury API
401/5xx) so the operator can tell apart "I haven't filled in the token
yet" from "Mercury is down right now". Per
``feedback_no_silent_defaults`` the routes fail loud — a missing token
returns 503 with the exact ``poindexter settings set`` command to fix
it, never a stale cached zero.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from middleware.api_token_auth import verify_api_token
from modules.finance.mercury_client import (
    MercuryAPIError,
    MercuryAuthError,
    MercuryClient,
)
from services.database_service import DatabaseService
from services.logger_config import get_logger
from utils.route_utils import get_database_dependency

logger = get_logger(__name__)

# Mounted by ``FinanceModule.register_routes`` via the route auto-
# discovery in ``utils/route_registration.register_all_routes``. The
# ``finance`` tag groups these in the OpenAPI / Swagger view so the
# operator can spot them at a glance.
router = APIRouter(prefix="/api/finance", tags=["finance"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TRUTHY = {"true", "1", "yes", "on"}


def _truthy(value: str | None) -> bool:
    """Match ``modules.finance.jobs.poll_mercury._truthy``. Kept inline
    rather than imported so the routes file has zero non-Module v1
    cross-imports (only stdlib + sibling Module files)."""
    return (value or "").strip().lower() in _TRUTHY


async def _read_mercury_config(
    pool: Any,
) -> tuple[bool, str | None]:
    """Single round-trip to fetch ``(mercury_enabled, mercury_api_token)``.

    Returns ``(enabled, token)`` where ``token`` is ``None`` when the
    row is missing or empty AND already decrypted when ``is_secret=true``.
    Lazy import of ``plugins.secrets.get_secret`` mirrors the pattern in
    ``PollMercuryJob.run`` — keeps this route file importable even when
    asyncpg/pgcrypto wiring is degraded in tests.
    """
    from plugins.secrets import get_secret

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT value FROM app_settings WHERE key = 'mercury_enabled'"
        )
        enabled = _truthy(row["value"] if row else None)
        token = await get_secret(conn, "mercury_api_token")
    return enabled, token


def _require_token(enabled: bool, token: str | None) -> str:
    """Raise the right HTTPException when Mercury access isn't usable.

    ``feedback_no_silent_defaults``: we never return a placeholder
    "0 balance" — operators need to see the actual config gap. The
    503 body is the exact CLI command to fix the row so the operator
    can copy-paste it back into their shell.
    """
    if not enabled:
        raise HTTPException(
            status_code=503,
            detail=(
                "Mercury integration is disabled — enable it via "
                "`poindexter settings set mercury_enabled true`"
            ),
        )
    if not token:
        raise HTTPException(
            status_code=503,
            detail=(
                "mercury_api_token is empty — set it via "
                "`poindexter settings set mercury_api_token <token> --secret` "
                "(Mercury dashboard → Settings → API → Read-Only)"
            ),
        )
    return token


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get(
    "/healthcheck",
    summary="Mercury API connectivity + token sanity check",
    response_model=dict[str, Any],
    status_code=200,
)
async def finance_healthcheck(
    _token: str = Depends(verify_api_token),
    db_service: DatabaseService = Depends(get_database_dependency),
) -> dict[str, Any]:
    """Return Mercury reachability info.

    Distinct outcomes (each maps to one ``status`` value the operator
    dashboard can rule on):

    - ``"disabled"``        — ``mercury_enabled=false``; no network call.
    - ``"unconfigured"``    — enabled but token row empty.
    - ``"ok"``              — token validated against ``GET /accounts``.
    - ``"auth_failed"``     — Mercury returned 401/403 (token revoked /
                              wrong scope).
    - ``"upstream_error"``  — Mercury returned 5xx or threw.

    The dashboard surfaces this as the single Mercury status indicator
    so the operator never has to read worker logs to know if the token
    expired. Returns 200 in every case — the body's ``status`` field
    is the failure signal, NOT the HTTP status. (4xx/5xx here would
    confuse uptime monitors that ping this route as a liveness probe.)
    """
    enabled, token = await _read_mercury_config(db_service.pool)

    if not enabled:
        return {
            "status": "disabled",
            "enabled": False,
            "has_token": bool(token),
            "detail": "mercury_enabled=false; no Mercury API call made",
        }

    if not token:
        return {
            "status": "unconfigured",
            "enabled": True,
            "has_token": False,
            "detail": (
                "mercury_api_token row is empty; set via "
                "`poindexter settings set mercury_api_token <t> --secret`"
            ),
        }

    # Real network call — list_accounts() is the cheapest authenticated
    # endpoint Mercury exposes (no params, returns the visible account
    # set). 401/403 → token problem; anything else → upstream problem.
    try:
        async with MercuryClient(token=token) as m:
            accounts = await m.list_accounts()
    except MercuryAuthError as e:
        logger.warning("finance.healthcheck: Mercury auth failed: %s", e)
        return {
            "status": "auth_failed",
            "enabled": True,
            "has_token": True,
            "detail": str(e),
        }
    except MercuryAPIError as e:
        logger.warning("finance.healthcheck: Mercury API error: %s", e)
        return {
            "status": "upstream_error",
            "enabled": True,
            "has_token": True,
            "detail": str(e),
        }
    except Exception as e:  # pragma: no cover — defensive only
        logger.warning("finance.healthcheck: unexpected error: %s", e, exc_info=True)
        return {
            "status": "upstream_error",
            "enabled": True,
            "has_token": True,
            "detail": f"{type(e).__name__}: {e}",
        }

    return {
        "status": "ok",
        "enabled": True,
        "has_token": True,
        "account_count": len(accounts),
        "detail": "Mercury reachable; token authenticated",
    }


@router.get(
    "/balances",
    summary="Current Mercury account balances (operator-only)",
    response_model=dict[str, Any],
    status_code=200,
)
async def finance_balances(
    _token: str = Depends(verify_api_token),
    db_service: DatabaseService = Depends(get_database_dependency),
) -> dict[str, Any]:
    """List every Mercury account + its current/available balance.

    Returns shape::

        {
            "accounts": [
                {
                    "id": "acc-...",
                    "name": "Glad Labs Checking",
                    "type": "checking",
                    "kind": "businessChecking",
                    "current_balance": 12345.67,
                    "available_balance": 12300.00,
                },
                ...
            ],
            "total_current_balance": 24690.0,
            "total_available_balance": 24600.0,
            "account_count": 2,
        }

    Totals are summed in-process (Python float). Mercury currently
    returns USD only; if multi-currency support lands we'll have to
    bucket-by-currency here rather than summing across.
    """
    enabled, token = await _read_mercury_config(db_service.pool)
    token = _require_token(enabled, token)

    try:
        async with MercuryClient(token=token) as m:
            accounts = await m.list_accounts()
    except MercuryAuthError as e:
        # 401 to the operator means "go re-mint the token" — same code
        # the OAuth middleware uses for the upstream-equivalent.
        raise HTTPException(status_code=401, detail=str(e)) from e
    except MercuryAPIError as e:
        # 502 is the correct mapping: a downstream service we proxy
        # returned an error response. Distinguishes from 503 (us
        # refusing to call due to config gap).
        raise HTTPException(status_code=502, detail=str(e)) from e

    total_current = sum(a.current_balance for a in accounts)
    total_available = sum(a.available_balance for a in accounts)

    return {
        "accounts": [
            {
                "id": a.id,
                "name": a.name,
                "type": a.type,
                "kind": a.kind,
                "current_balance": a.current_balance,
                "available_balance": a.available_balance,
            }
            for a in accounts
        ],
        "total_current_balance": total_current,
        "total_available_balance": total_available,
        "account_count": len(accounts),
    }


@router.get(
    "/transactions",
    summary="Recent Mercury transactions (operator-only)",
    response_model=dict[str, Any],
    status_code=200,
)
async def finance_transactions(
    _token: str = Depends(verify_api_token),
    db_service: DatabaseService = Depends(get_database_dependency),
    limit: int = Query(
        default=100,
        ge=1,
        le=500,
        description="Max transactions per account in the response (Mercury page size).",
    ),
    lookback_days: int = Query(
        default=14,
        ge=1,
        le=365,
        description=(
            "Number of days back from today to include. 14 matches "
            "PollMercuryJob's lookback (Mercury sometimes backdates "
            "pending → posted transitions)."
        ),
    ),
    account_id: str | None = Query(
        default=None,
        description=(
            "Optional Mercury account id. When omitted, transactions "
            "from every visible account are merged into one response."
        ),
    ),
) -> dict[str, Any]:
    """Recent transactions across one or every account.

    F1-style minimal pagination: ``limit`` is per-account, no cursor.
    The polling job is the durable record — this route is a synchronous
    operator window into Mercury, not the primary data plane.

    Shape::

        {
            "transactions": [
                {
                    "id": "txn-...",
                    "account_id": "acc-...",
                    "amount": -123.45,
                    "posted_at": "2026-05-15T18:00:00Z",
                    "counterparty": "AWS Cloud",
                    "status": "posted",
                },
                ...
            ],
            "count": 42,
            "lookback_days": 14,
            "start_date": "2026-05-02",
        }
    """
    enabled, token = await _read_mercury_config(db_service.pool)
    token = _require_token(enabled, token)

    start_d = date.today() - timedelta(days=lookback_days)

    try:
        async with MercuryClient(token=token) as m:
            if account_id is not None:
                # Single-account path — no need to call list_accounts first.
                txns = await m.list_transactions(
                    account_id, start=start_d, limit=limit,
                )
            else:
                # Multi-account: list accounts, then fan out per-account.
                accounts = await m.list_accounts()
                txns = []
                for a in accounts:
                    txns.extend(
                        await m.list_transactions(
                            a.id, start=start_d, limit=limit,
                        )
                    )
    except MercuryAuthError as e:
        raise HTTPException(status_code=401, detail=str(e)) from e
    except MercuryAPIError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e

    # Sort descending by posted_at so the operator sees most-recent
    # first. Sort key uses the ISO 8601 timestamp string — lexical
    # ordering is correct for ISO 8601 strings, so we don't need to
    # parse the date here.
    txns.sort(key=lambda t: t.posted_at, reverse=True)

    return {
        "transactions": [
            {
                "id": t.id,
                "account_id": t.account_id,
                "amount": t.amount,
                "posted_at": t.posted_at,
                "counterparty": t.counterparty,
                "status": t.status,
            }
            for t in txns
        ],
        "count": len(txns),
        "lookback_days": lookback_days,
        "start_date": start_d.isoformat(),
    }


__all__ = ["router"]
