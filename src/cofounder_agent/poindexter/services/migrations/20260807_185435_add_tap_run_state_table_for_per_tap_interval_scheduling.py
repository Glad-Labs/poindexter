"""Migration 20260807_185435: tap_run_state for per-tap interval scheduling.

ISSUE: Glad-Labs/poindexter#998 — per-tap intervals were declared but never
enforced.

Every Tap declares ``interval_seconds`` (memory 3600, claude_code_sessions
7200, github_issues 21600, …) and ``PluginConfig`` parses an operator
override out of ``app_settings``. Nothing read either: the runner walked
every enabled tap on every invocation, so a 6-hourly tap ran hourly and
``HelloTap``'s ``interval_seconds = 0`` ("on-demand only; don't
auto-schedule") was silently ignored. ``services/jobs/run_taps.py`` called
respecting it "a future refinement".

Worse, the runner has TWO callers — the hourly ``RunTapsJob`` in the worker
and the hourly auto-embed sidecar loop — so every tap actually ran twice an
hour from two processes. Shared state in the DB (rather than per-process
memory) is what lets them agree.

Table shape deliberately mirrors ``job_run_state``, which solves the same
problem for scheduled jobs: primary key on the name, plus last-run time and
status. Keeping the two parallel means one mental model and one set of
Grafana queries.

Rows are created lazily on first run — no seeding. An unknown tap is
"never run", hence due, which is the correct default for a newly installed
plugin.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def up(pool) -> None:
    """Apply the migration."""
    async with pool.acquire() as conn:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS tap_run_state (
                tap_name    TEXT PRIMARY KEY,
                last_run_at TIMESTAMPTZ,
                last_status TEXT,
                updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
        logger.info("Migration %s: tap_run_state ready.", __name__)


async def down(pool) -> None:
    """Drop the table. Safe: it is derived scheduling state, not data.

    Dropping it makes every tap read as "never run" → due on the next
    cycle, which is exactly the pre-migration behaviour.
    """
    async with pool.acquire() as conn:
        await conn.execute("DROP TABLE IF EXISTS tap_run_state")
