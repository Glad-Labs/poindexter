"""Migration 20260909_210917: allow a 'telemetry' internal source_kind.

ISSUE: benchmark-findings topics could never win a batch slot (2026-09-09).

``topic_pool`` rows are split into internal/external by
``services/topic_pool.py::read_pooled``, which treated ONLY ``internal_rag`` as
internal and everything else as external. ``benchmark_findings`` — topics
generated from our OWN ``cost_logs`` telemetry — therefore competed in the
consumer-topic bucket: pre-ranking keeps the top 5 external candidates by
goal-similarity, and one never-expiring row was up against ~86 constantly
refreshed HackerNews / RSS / dev.to candidates.

Measured: in 7 days and 12 resolved batches it never once became a
``topic_candidates`` row. Not a bug in the source — it loses on merit, every
sweep, because it is in the wrong bucket.

Moving it to the internal bucket changes the odds from 1-of-87 to 1-of-21 for
the same 5 pre-rank slots. But ``internal_topic_candidates.source_kind`` is a
CHECK-constrained enum of knowledge-corpus origins (claude_session,
brain_knowledge, audit_event, git_commit, decision_log, memory_file,
post_history) and none of them honestly describes "medians over our own
inference telemetry". Rather than mislabel it as ``audit_event``, this adds
``telemetry`` — the set is deliberately enumerated, so extending it is the
honest move.

Idempotent: drops the constraint if present and re-adds the widened one.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_ALLOWED = (
    "claude_session", "brain_knowledge", "audit_event", "git_commit",
    "decision_log", "memory_file", "post_history", "telemetry",
)


async def up(pool) -> None:
    values = ", ".join(f"'{v}'" for v in _ALLOWED)
    async with pool.acquire() as conn:
        await conn.execute(
            "ALTER TABLE internal_topic_candidates "
            "DROP CONSTRAINT IF EXISTS internal_topic_candidates_source_kind_check"
        )
        await conn.execute(
            "ALTER TABLE internal_topic_candidates "
            "ADD CONSTRAINT internal_topic_candidates_source_kind_check "
            f"CHECK (source_kind = ANY (ARRAY[{values}]))"
        )
    logger.info("allow_telemetry_source_kind: applied (%d allowed kinds)", len(_ALLOWED))


async def down(pool) -> None:
    """Narrow the enum back, but only if no row uses the new value.

    Dropping a value a row still holds would leave the table failing its own
    constraint on the next write, so this refuses rather than corrupting it.
    """
    async with pool.acquire() as conn:
        in_use = await conn.fetchval(
            "SELECT count(*) FROM internal_topic_candidates WHERE source_kind = 'telemetry'"
        )
        if in_use:
            logger.warning(
                "allow_telemetry_source_kind down: %d row(s) still use "
                "'telemetry' — leaving the widened constraint in place", in_use,
            )
            return
        values = ", ".join(f"'{v}'" for v in _ALLOWED if v != "telemetry")
        await conn.execute(
            "ALTER TABLE internal_topic_candidates "
            "DROP CONSTRAINT IF EXISTS internal_topic_candidates_source_kind_check"
        )
        await conn.execute(
            "ALTER TABLE internal_topic_candidates "
            "ADD CONSTRAINT internal_topic_candidates_source_kind_check "
            f"CHECK (source_kind = ANY (ARRAY[{values}]))"
        )
    logger.info("allow_telemetry_source_kind: reverted")
