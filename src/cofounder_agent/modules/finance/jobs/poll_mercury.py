"""``PollMercuryJob`` — periodic Mercury snapshot into Postgres.

FinanceModule F2 (2026-05-13, Glad-Labs/poindexter#490). Runs
hourly via the PluginScheduler. Pulls every Mercury account +
its recent transactions, upserts into ``finance_accounts`` +
``finance_transactions``, and records the run in
``finance_poll_runs`` so Grafana can chart polling health.

Gate: ``app_settings.mercury_enabled``. The job is a no-op when
this is anything other than truthy ('true', '1', 'yes', 'on').
This is the safe default — partial deployments without a token
configured shouldn't 401 Mercury every hour and burn through the
operator's rate-limit budget.

Transaction lookback: F2 pulls the last 14 days every run. That's
deliberately wider than the 1-hour interval — Mercury sometimes
backdates pending → posted transitions, and we want the status
flip to land in the next refresh rather than going stale. The
``ON CONFLICT DO UPDATE`` upsert handles the status flip without
double-counting.

Failure posture: a single Mercury API call failure logs +
records a 'failed' poll_run row, but does NOT raise. The
scheduler will try again next hour. The audit row gives Grafana
a signal to alert on consecutive failures (added in F3).
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any

from plugins.job import JobResult

logger = logging.getLogger(__name__)


_TRUTHY = {"true", "1", "yes", "on"}


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in _TRUTHY


async def _is_enabled(conn: Any) -> bool:
    row = await conn.fetchrow(
        "SELECT value FROM app_settings WHERE key = 'mercury_enabled'"
    )
    return _truthy(row["value"] if row else None)


async def _start_run(conn: Any) -> int:
    return await conn.fetchval(
        "INSERT INTO finance_poll_runs (status) VALUES ('running') RETURNING id"
    )


async def _finish_run(
    conn: Any,
    run_id: int,
    *,
    status: str,
    accounts_seen: int = 0,
    transactions_new: int = 0,
    transactions_updated: int = 0,
    error: str | None = None,
) -> None:
    await conn.execute(
        """
        UPDATE finance_poll_runs SET
            finished_at = NOW(),
            accounts_seen = $2,
            transactions_new = $3,
            transactions_updated = $4,
            status = $5,
            error_message = $6
        WHERE id = $1
        """,
        run_id,
        accounts_seen,
        transactions_new,
        transactions_updated,
        status,
        error,
    )


async def _upsert_account(conn: Any, acc) -> None:
    await conn.execute(
        """
        INSERT INTO finance_accounts
            (id, name, type, kind, current_balance, available_balance,
             first_seen_at, last_refreshed_at)
        VALUES ($1, $2, $3, $4, $5, $6, NOW(), NOW())
        ON CONFLICT (id) DO UPDATE SET
            name              = EXCLUDED.name,
            type              = EXCLUDED.type,
            kind              = EXCLUDED.kind,
            current_balance   = EXCLUDED.current_balance,
            available_balance = EXCLUDED.available_balance,
            last_refreshed_at = NOW()
        """,
        acc.id, acc.name, acc.type, acc.kind,
        acc.current_balance, acc.available_balance,
    )


async def _upsert_transaction(conn: Any, t) -> bool:
    """Return True if this was a brand-new row (first_seen_at = NOW()).

    Uses ``RETURNING (xmax = 0) AS was_new`` to distinguish fresh INSERT
    from ON CONFLICT DO UPDATE — the asyncpg command-tag trick
    (``status.endswith(" 1")``) returns 1 for BOTH branches in a single
    INSERT..ON CONFLICT DO UPDATE statement, so it over-counts new rows
    by including every conflict-update. Postgres's hidden ``xmax``
    column is 0 only for rows just inserted in the current transaction;
    an ON CONFLICT DO UPDATE bumps xmax to the deleting transaction
    ID — so ``xmax = 0`` cleanly partitions the two branches.
    """
    return await conn.fetchval(
        """
        INSERT INTO finance_transactions
            (id, account_id, amount, posted_at, counterparty, status,
             first_seen_at, last_refreshed_at)
        VALUES ($1, $2, $3, NULLIF($4, '')::timestamptz, $5, $6, NOW(), NOW())
        ON CONFLICT (id) DO UPDATE SET
            amount            = EXCLUDED.amount,
            posted_at         = COALESCE(EXCLUDED.posted_at, finance_transactions.posted_at),
            counterparty      = EXCLUDED.counterparty,
            status            = EXCLUDED.status,
            last_refreshed_at = NOW()
        RETURNING (xmax = 0) AS was_new
        """,
        t.id, t.account_id, t.amount, t.posted_at or "",
        t.counterparty, t.status,
    )


class PollMercuryJob:
    """Plugin-scheduler job. Idempotent — safe to run more often if
    the operator dials the cadence down (1h is the default)."""

    name = "poll_mercury"
    description = (
        "Snapshot Mercury accounts + recent transactions into Postgres "
        "every hour. No-op when mercury_enabled is false."
    )
    schedule = "every 1 hour"
    idempotent = True

    async def run(self, pool: Any, config: dict[str, Any]) -> JobResult:
        # Avoid circular import — plugins.secrets imports asyncpg lazily
        from modules.finance.mercury_client import (
            MercuryAPIError,
            MercuryAuthError,
            MercuryClient,
        )
        from plugins.secrets import get_secret

        lookback_days = int(config.get("lookback_days", 14))
        transactions_per_account = int(config.get("transactions_per_account", 250))

        # Cheap gate check before we open the run row + Mercury client
        async with pool.acquire() as conn:
            if not await _is_enabled(conn):
                logger.debug(
                    "PollMercuryJob: mercury_enabled is false — skipping",
                )
                return JobResult(
                    ok=True,
                    detail="mercury_enabled=false; no-op",
                    changes_made=0,
                    metrics={"skipped_disabled": 1},
                )
            token = await get_secret(conn, "mercury_api_token")

        if not token:
            return JobResult(
                ok=False,
                detail="mercury_api_token is empty — fill via "
                       "`poindexter settings set mercury_api_token <t> --secret`",
                changes_made=0,
                metrics={"missing_token": 1},
            )

        # Open the run row + try the pull. Single-conn for upserts within
        # this run; pool.acquire pairs perfectly with the runner contract.
        async with pool.acquire() as conn:
            run_id = await _start_run(conn)

            try:
                async with MercuryClient(token=token) as m:
                    accounts = await m.list_accounts()
                    for a in accounts:
                        await _upsert_account(conn, a)

                    txn_new = 0
                    txn_updated = 0
                    start_d = date.today() - timedelta(days=lookback_days)
                    for a in accounts:
                        txns = await m.list_transactions(
                            a.id,
                            start=start_d,
                            limit=transactions_per_account,
                        )
                        for t in txns:
                            was_new = await _upsert_transaction(conn, t)
                            if was_new:
                                txn_new += 1
                            else:
                                txn_updated += 1

                await _finish_run(
                    conn, run_id, status="ok",
                    accounts_seen=len(accounts),
                    transactions_new=txn_new,
                    transactions_updated=txn_updated,
                )
                return JobResult(
                    ok=True,
                    detail=(
                        f"polled {len(accounts)} account(s); "
                        f"{txn_new} new + {txn_updated} updated txn(s)"
                    ),
                    changes_made=txn_new + txn_updated,
                    metrics={
                        "accounts_seen": len(accounts),
                        "transactions_new": txn_new,
                        "transactions_updated": txn_updated,
                    },
                )

            except MercuryAuthError as e:
                logger.error("PollMercuryJob: auth failed — %s", e)
                await _finish_run(
                    conn, run_id, status="auth_failed",
                    error=str(e),
                )
                return JobResult(
                    ok=False,
                    detail=f"Mercury auth: {e}",
                    changes_made=0,
                    metrics={"auth_failed": 1},
                )

            except MercuryAPIError as e:
                logger.warning("PollMercuryJob: API error — %s", e)
                await _finish_run(
                    conn, run_id, status="api_error",
                    error=str(e),
                )
                return JobResult(
                    ok=False,
                    detail=f"Mercury API: {e}",
                    changes_made=0,
                    metrics={"api_error": 1},
                )

            except Exception as e:
                logger.exception("PollMercuryJob: unexpected failure")
                await _finish_run(
                    conn, run_id, status="exception",
                    error=str(e),
                )
                return JobResult(
                    ok=False,
                    detail=f"unexpected: {type(e).__name__}: {e}",
                    changes_made=0,
                    metrics={"exception": 1},
                )
