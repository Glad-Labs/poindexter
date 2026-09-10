"""Create ``finance_accounts`` + ``finance_transactions`` tables.

FinanceModule F2 migration (2026-05-13, Glad-Labs/poindexter#490).
Per-module migration — runs via Phase 2's per-module runner.

Schema mirrors what ``MercuryClient`` returns so the polling job
(F2b) can do straight upserts:

- ``finance_accounts``: latest known balance per account. Primary
  key is Mercury's own account ID — stable across pulls so upserts
  on conflict update the balance in place.
- ``finance_transactions``: append-only ledger. Primary key is
  Mercury's transaction ID. Status can flip (pending → posted), so
  ON CONFLICT DO UPDATE rewrites the status + amount on a refetch.
- ``finance_poll_runs``: audit trail of every Mercury poll so we
  can spot stalls in Grafana + correlate with errors. One row per
  scheduler tick.

USD-only for F2 — Mercury currently only supports USD on the
business banking API. If they ever ship multi-currency, add a
``currency`` column + backfill 'USD' for historical rows.

Numeric precision: NUMERIC(14, 2) gives us up to $999,999,999,999.99
which is more than enough headroom and avoids the float-comparison
foot-guns of REAL/DOUBLE PRECISION on monetary values.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


_UP_SQL = """
CREATE TABLE IF NOT EXISTS finance_accounts (
    id                   TEXT PRIMARY KEY,
    name                 TEXT NOT NULL,
    type                 TEXT NOT NULL,
    kind                 TEXT NOT NULL,
    current_balance      NUMERIC(14, 2) NOT NULL,
    available_balance    NUMERIC(14, 2) NOT NULL,
    first_seen_at        TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    last_refreshed_at    TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS finance_transactions (
    id                   TEXT PRIMARY KEY,
    account_id           TEXT NOT NULL REFERENCES finance_accounts(id)
                              ON DELETE CASCADE,
    amount               NUMERIC(14, 2) NOT NULL,
    posted_at            TIMESTAMP WITH TIME ZONE,
    counterparty         TEXT NOT NULL DEFAULT '',
    status               TEXT NOT NULL DEFAULT 'unknown',
    first_seen_at        TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    last_refreshed_at    TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS finance_transactions_account_idx
    ON finance_transactions (account_id, posted_at DESC);
CREATE INDEX IF NOT EXISTS finance_transactions_posted_at_idx
    ON finance_transactions (posted_at DESC);

CREATE TABLE IF NOT EXISTS finance_poll_runs (
    id                   BIGSERIAL PRIMARY KEY,
    started_at           TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    finished_at          TIMESTAMP WITH TIME ZONE,
    accounts_seen        INT,
    transactions_new     INT,
    transactions_updated INT,
    status               TEXT NOT NULL DEFAULT 'running',
    error_message        TEXT
);

CREATE INDEX IF NOT EXISTS finance_poll_runs_started_idx
    ON finance_poll_runs (started_at DESC);
"""

_DOWN_SQL = """
DROP TABLE IF EXISTS finance_poll_runs;
DROP TABLE IF EXISTS finance_transactions;
DROP TABLE IF EXISTS finance_accounts;
"""


async def up(pool) -> None:
    """Apply the migration. Idempotent via IF NOT EXISTS."""
    async with pool.acquire() as conn:
        await conn.execute(_UP_SQL)
        logger.info("FinanceModule F2: created finance_accounts + transactions + poll_runs")


async def down(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(_DOWN_SQL)
        logger.info("FinanceModule F2: dropped finance_* tables")
