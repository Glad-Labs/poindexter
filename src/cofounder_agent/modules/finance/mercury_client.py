"""Async client for the Mercury Banking API (read-only).

Phase F1 (2026-05-13). Wraps Mercury's REST API for read-only
queries: account list, account balance, transaction history.
Authentication is a single bearer token generated via
Mercury dashboard → Settings → API → "Read-Only" scope.

API surface (Mercury docs at docs.mercury.com/reference):
- ``GET /api/v1/accounts``  — list business accounts
- ``GET /api/v1/account/{id}``  — account detail incl. balance
- ``GET /api/v1/account/{id}/transactions?limit=&offset=&start=&end=``
                            — transaction history

Why ONLY read-only: aligns with the OAuth scope hygiene memory
(``feedback_oauth_scope_hygiene``). The polling job + brain
knowledge integration that consume this client never need to
move money, so granting move-money scope to the operator daemon
would be an unjustified blast-radius expansion.

The token + base URL live in ``app_settings`` (see
``settings_defaults``); the client never reads env vars directly.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

import httpx

from services.logger_config import get_logger

logger = get_logger(__name__)


_DEFAULT_BASE_URL = "https://api.mercury.com/api/v1"
_DEFAULT_TIMEOUT_S = 30.0


class MercuryAuthError(Exception):
    """Raised when Mercury returns 401/403 — surface loudly so the
    operator notices a missing/revoked token, never silently fall
    back to a stale cached balance."""


class MercuryAPIError(Exception):
    """Raised for non-auth Mercury errors (5xx / unexpected shape).
    Distinct from MercuryAuthError so callers can decide whether to
    page the operator or just back off + retry."""


@dataclass(frozen=True)
class MercuryAccount:
    """One row from ``GET /accounts``. Pruned to the fields F1 uses.

    Mercury returns more (nickname, routing/account numbers, etc.);
    we only carry what the operator surface needs. Add fields as
    future features (transfers, statements) require them — keeping
    the dataclass tight makes the type docs self-explanatory.
    """

    id: str
    name: str
    type: str  # "checking" | "savings" | etc.
    current_balance: float  # USD; Mercury returns USD only at present
    available_balance: float
    kind: str  # "businessChecking" / "businessSavings" / ...


@dataclass(frozen=True)
class MercuryTransaction:
    """One row from ``GET /account/{id}/transactions``."""

    id: str
    account_id: str
    amount: float  # signed: negative = outgoing, positive = incoming
    posted_at: str  # ISO 8601 timestamp string from Mercury
    counterparty: str  # description / counterparty name
    status: str  # "posted" | "pending" | "failed" | ...


class MercuryClient:
    """Read-only Mercury API client. Construct with an explicit token
    (do NOT pull from env vars — the caller passes whatever
    ``site_config.get_secret('mercury_api_token')`` returned).

    Usage::

        async with MercuryClient(token=tok) as m:
            accounts = await m.list_accounts()
            for a in accounts:
                print(a.name, a.current_balance)
    """

    def __init__(
        self,
        token: str,
        base_url: str = _DEFAULT_BASE_URL,
        timeout_s: float = _DEFAULT_TIMEOUT_S,
    ):
        if not token:
            # Fail loud per feedback_no_silent_defaults. A blank token
            # means the operator hasn't completed setup; we'd rather
            # crash here than silently call Mercury with no auth.
            raise MercuryAuthError(
                "MercuryClient requires a non-empty API token — "
                "set app_settings.mercury_api_token first "
                "(Mercury dashboard → Settings → API → Read-Only)"
            )
        self._token = token
        self._base_url = base_url.rstrip("/")
        self._timeout_s = timeout_s
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> MercuryClient:
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            timeout=self._timeout_s,
            headers={
                "Authorization": f"Bearer {self._token}",
                "Accept": "application/json",
            },
        )
        return self

    async def __aexit__(self, *_) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        if self._client is None:
            raise RuntimeError(
                "MercuryClient must be used as an async context manager"
            )
        resp = await self._client.get(path, params=params or {})
        if resp.status_code in (401, 403):
            raise MercuryAuthError(
                f"Mercury auth failed ({resp.status_code}) — "
                "token may be revoked or wrong scope"
            )
        if resp.status_code >= 400:
            raise MercuryAPIError(
                f"Mercury API error {resp.status_code} on {path}: "
                f"{resp.text[:200]}"
            )
        return resp.json()

    async def list_accounts(self) -> list[MercuryAccount]:
        """Return every account visible to this token. Mercury's
        response shape: ``{"accounts": [...]}``."""
        data = await self._get("/accounts")
        accounts_raw = data.get("accounts", []) if isinstance(data, dict) else data
        return [
            MercuryAccount(
                id=a["id"],
                name=a.get("name", ""),
                type=a.get("type", "unknown"),
                current_balance=float(a.get("currentBalance", 0.0)),
                available_balance=float(a.get("availableBalance", 0.0)),
                kind=a.get("kind", "unknown"),
            )
            for a in accounts_raw
        ]

    async def get_account(self, account_id: str) -> MercuryAccount:
        """Single account detail — useful when you only care about
        one balance + don't want to pay the list call."""
        a = await self._get(f"/account/{account_id}")
        return MercuryAccount(
            id=a["id"],
            name=a.get("name", ""),
            type=a.get("type", "unknown"),
            current_balance=float(a.get("currentBalance", 0.0)),
            available_balance=float(a.get("availableBalance", 0.0)),
            kind=a.get("kind", "unknown"),
        )

    async def list_transactions(
        self,
        account_id: str,
        *,
        start: date | None = None,
        end: date | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[MercuryTransaction]:
        """Transactions for one account, optionally bounded by date.

        F1 returns at most ``limit`` rows in one call — pagination is
        the caller's job until F2 builds a daily polling loop that
        walks pages until empty.
        """
        params: dict[str, Any] = {"limit": limit, "offset": offset}
        if start is not None:
            params["start"] = start.isoformat()
        if end is not None:
            params["end"] = end.isoformat()

        data = await self._get(f"/account/{account_id}/transactions", params=params)
        txns_raw = (
            data.get("transactions", []) if isinstance(data, dict) else data
        )
        return [
            MercuryTransaction(
                id=t["id"],
                account_id=account_id,
                amount=float(t.get("amount", 0.0)),
                posted_at=t.get("postedAt") or t.get("createdAt") or "",
                counterparty=t.get("counterpartyName", "")
                    or t.get("note", "")
                    or t.get("bankDescription", ""),
                status=t.get("status", "unknown"),
            )
            for t in txns_raw
        ]


__all__ = [
    "MercuryAccount",
    "MercuryAPIError",
    "MercuryAuthError",
    "MercuryClient",
    "MercuryTransaction",
]
