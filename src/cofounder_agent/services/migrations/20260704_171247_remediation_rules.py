"""Migration: remediation_rules — deterministic self-healing firefighter policy.

Declarative alert->action rules the brain's alert_dispatcher consults before
paging. Same data-plane shape as external_taps / qa_gates: enabled rows drive a
handler (the brain/remediation action registry). Ships EMPTY — an enabled
firefighter with no rows is a safe no-op. Operators seed rows per
docs/operations/self-healing.md.

stdlib-only so migrations-smoke applies it without a full app boot.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_DDL = """
CREATE TABLE IF NOT EXISTS remediation_rules (
    id                        SERIAL PRIMARY KEY,
    alertname                 TEXT,
    match_regex               TEXT,
    action_name               TEXT NOT NULL,
    params                    JSONB NOT NULL DEFAULT '{}'::jsonb,
    enabled                   BOOLEAN NOT NULL DEFAULT TRUE,
    max_attempts_per_window   INTEGER,
    window_minutes            INTEGER,
    verify_after_seconds      INTEGER,
    description               TEXT NOT NULL DEFAULT '',
    created_at                TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at                TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT remediation_rules_match_present
        CHECK (alertname IS NOT NULL OR match_regex IS NOT NULL)
);
CREATE INDEX IF NOT EXISTS idx_remediation_rules_enabled
    ON remediation_rules (enabled) WHERE enabled;
CREATE UNIQUE INDEX IF NOT EXISTS idx_remediation_rules_alertname
    ON remediation_rules (alertname) WHERE alertname IS NOT NULL;
"""


async def up(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(_DDL)
    logger.info("remediation_rules up: table + indexes created (empty)")


async def down(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute("DROP TABLE IF EXISTS remediation_rules")
    logger.info("remediation_rules down: table dropped")
