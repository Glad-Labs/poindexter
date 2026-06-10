"""Migration: create retention summary tables.

Two ``retention_policies`` rows are seeded ``enabled=true`` with handler
``summarize_to_table`` (``audit_log`` and ``brain_decisions``). They compress
old rows into per-day summary rows + an LLM paragraph — but the destination
tables they INSERT into (``audit_log_summaries`` / ``brain_decision_summaries``)
were never created by any migration.

Consequences (Glad-Labs/poindexter#694):

- Every pass rolls back: the INSERT into a nonexistent table aborts the txn
  before the DELETE, so there is no *immediate* data loss — but the policy is
  a permanent no-op and starts erroring once the first 90-day bucket comes due.
- ``RetentionJanitor`` runs a SEPARATE hard delete on the same SOURCE tables
  (``audit_log`` @180d, ``brain_decisions`` @365d). Rows that should have been
  compressed first are instead destroyed once they age past the hard-delete
  window — history lost that was supposed to be summarized.
- Fresh installs are equally broken: the baseline seeds the enabled policies
  without ever shipping the DDL.

This creates both tables with the exact column set the
``retention.summarize_to_table`` handler INSERTs, derived from each policy's
seeded ``config``:

- ``audit_log_summaries``      — count_columns [event_type, severity] →
  ``event_type_counts`` / ``severity_counts`` (jsonb); top_source_column source
  → ``top_sources`` (jsonb); excerpts_column ``error_excerpts`` (jsonb).
- ``brain_decision_summaries`` — count_columns [outcome] → ``outcome_counts``
  (jsonb); confidence_column confidence → ``avg_confidence`` (double precision);
  excerpts_column ``decision_excerpts`` (jsonb).

Columns the handler does not supply (``id``, ``created_at``) are
auto-generated, so the INSERT (which names only its own columns) succeeds.

Idempotent — CREATE TABLE / INDEX IF NOT EXISTS. Imports only stdlib +
logging (migrations-smoke light-env rule).
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_CREATE_AUDIT_LOG_SUMMARIES = """
CREATE TABLE IF NOT EXISTS audit_log_summaries (
    id                 BIGSERIAL PRIMARY KEY,
    bucket_start       TIMESTAMPTZ NOT NULL,
    bucket_end         TIMESTAMPTZ NOT NULL,
    row_count          INTEGER NOT NULL,
    event_type_counts  JSONB NOT NULL DEFAULT '{}'::jsonb,
    severity_counts    JSONB NOT NULL DEFAULT '{}'::jsonb,
    top_sources        JSONB,
    error_excerpts     JSONB,
    summary_text       TEXT,
    summary_method     TEXT,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""

_CREATE_BRAIN_DECISION_SUMMARIES = """
CREATE TABLE IF NOT EXISTS brain_decision_summaries (
    id                 BIGSERIAL PRIMARY KEY,
    bucket_start       TIMESTAMPTZ NOT NULL,
    bucket_end         TIMESTAMPTZ NOT NULL,
    row_count          INTEGER NOT NULL,
    outcome_counts     JSONB NOT NULL DEFAULT '{}'::jsonb,
    avg_confidence     DOUBLE PRECISION,
    decision_excerpts  JSONB,
    summary_text       TEXT,
    summary_method     TEXT,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""

_CREATE_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_audit_log_summaries_bucket_start"
    + " ON audit_log_summaries (bucket_start);",
    "CREATE INDEX IF NOT EXISTS idx_brain_decision_summaries_bucket_start"
    + " ON brain_decision_summaries (bucket_start);",
]


async def up(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(_CREATE_AUDIT_LOG_SUMMARIES)
        await conn.execute(_CREATE_BRAIN_DECISION_SUMMARIES)
        for idx_sql in _CREATE_INDEXES:
            await conn.execute(idx_sql)
    logger.info(
        "Migration create_retention_summary_tables: 2 tables + %d indexes",
        len(_CREATE_INDEXES),
    )


async def down(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute("DROP TABLE IF EXISTS audit_log_summaries")
        await conn.execute("DROP TABLE IF EXISTS brain_decision_summaries")
    logger.info("Migration create_retention_summary_tables down: tables removed")
