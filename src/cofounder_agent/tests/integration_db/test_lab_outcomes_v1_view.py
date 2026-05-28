"""Integration tests for the ``lab_outcomes_v1`` view + the additive
columns introduced by migration ``20260528_204250``.

Phase 0 of the content R&D lab (Glad-Labs/glad-labs-stack). The view is
the **single read surface** every later phase consumes — bandit, variant
experiments, learnings digest. Get the joins right here and downstream
phases stop fighting schema.

Runs against the disposable test DB harness in
``tests/integration_db/conftest.py`` (skipped automatically when no
Postgres is reachable). The session-scoped fixtures apply every
migration in services/migrations/ in lex order, so by the time these
tests run the columns + view are already in place.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

pytestmark = [
    pytest.mark.integration_db,
    pytest.mark.asyncio(loop_scope="session"),
]


# ---------------------------------------------------------------------------
# Schema introspection — confirms the migration actually landed
# ---------------------------------------------------------------------------


async def test_capability_outcomes_has_lab_columns(test_pool):
    """The three additive columns must be present on capability_outcomes."""
    async with test_pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT column_name, data_type
              FROM information_schema.columns
             WHERE table_name = 'capability_outcomes'
               AND column_name IN
                   ('niche_slug', 'prompt_template_key',
                    'prompt_template_version')
             ORDER BY column_name
            """
        )
    names = {r["column_name"] for r in rows}
    assert names == {
        "niche_slug",
        "prompt_template_key",
        "prompt_template_version",
    }


async def test_routing_outcomes_has_lab_columns(test_pool):
    """The three additive columns must be present on routing_outcomes."""
    async with test_pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT column_name
              FROM information_schema.columns
             WHERE table_name = 'routing_outcomes'
               AND column_name IN
                   ('niche_slug', 'prompt_template_key',
                    'prompt_template_version')
            """
        )
    names = {r["column_name"] for r in rows}
    assert names == {
        "niche_slug",
        "prompt_template_key",
        "prompt_template_version",
    }


async def test_published_post_edit_metrics_has_lab_columns(test_pool):
    """published_post_edit_metrics gets model + prompt provenance."""
    async with test_pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT column_name
              FROM information_schema.columns
             WHERE table_name = 'published_post_edit_metrics'
               AND column_name IN
                   ('model_used', 'prompt_template_key',
                    'prompt_template_version')
            """
        )
    names = {r["column_name"] for r in rows}
    assert names == {
        "model_used",
        "prompt_template_key",
        "prompt_template_version",
    }


async def test_lab_outcomes_v1_view_exists(test_pool):
    """The view must exist after the migration applies."""
    async with test_pool.acquire() as conn:
        relkind = await conn.fetchval(
            "SELECT relkind::text FROM pg_class WHERE relname = 'lab_outcomes_v1'"
        )
    # 'v' is the relkind for a regular view. Cast to text in SQL so
    # asyncpg returns a str (the raw "char" type comes back as bytes).
    assert relkind == "v"


async def test_partial_index_present(test_pool):
    """The composite partial index on capability_outcomes must exist."""
    async with test_pool.acquire() as conn:
        idx = await conn.fetchval(
            """
            SELECT indexname FROM pg_indexes
             WHERE tablename = 'capability_outcomes'
               AND indexname = 'idx_capability_outcomes_niche_template'
            """
        )
    assert idx == "idx_capability_outcomes_niche_template"


# ---------------------------------------------------------------------------
# End-to-end join — synthetic data through the full view
# ---------------------------------------------------------------------------


async def test_view_joins_capability_routing_and_edit_metrics(test_pool):
    """Insert a synthetic task across all three source tables, then
    SELECT from the view and verify the joined row carries every field
    populated by exactly one underlying table."""
    task_id = str(uuid4())
    niche = "test-niche-lab-phase0"
    prompt_key = "atoms.test_writer.system_prompt"
    prompt_version = 7
    model = "test-model:42b"
    approver = "test-operator"

    try:
        async with test_pool.acquire() as conn:
            # capability_outcomes row — the writer atom's run
            await conn.execute(
                """
                INSERT INTO capability_outcomes
                  (task_id, template_slug, node_name, atom_name,
                   capability_tier, model_used,
                   ok, halted, elapsed_ms, quality_score, metrics,
                   niche_slug, prompt_template_key,
                   prompt_template_version)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10,
                        $11::jsonb, $12, $13, $14)
                """,
                task_id, "canonical_blog", "atoms.test_writer",
                "atoms.test_writer", "standard_writer", model,
                True, False, 1234, 87.5,
                json.dumps({"foo": "bar"}),
                niche, prompt_key, prompt_version,
            )

            # routing_outcomes row — the LLM dispatcher's row
            await conn.execute(
                """
                INSERT INTO routing_outcomes
                  (task_id, task_type, task_category, worker_id,
                   model_used, compute_tier, estimated_cost, actual_cost,
                   quality_score, duration_ms, success,
                   niche_slug, prompt_template_key,
                   prompt_template_version)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11,
                        $12, $13, $14)
                """,
                task_id, "content_generation", "tech", "test-worker",
                model, "standard", 0.0042, 0.0039,
                87.5, 1500, True,
                niche, prompt_key, prompt_version,
            )

            # published_post_edit_metrics row — the operator approval
            approved_at = datetime.now(timezone.utc)
            await conn.execute(
                """
                INSERT INTO published_post_edit_metrics
                  (task_id, niche_slug, category, approver,
                   pre_approve_hash, post_approve_hash,
                   char_diff_count, line_diff_count,
                   pre_approve_len, post_approve_len,
                   approve_method, approved_at, metrics,
                   model_used, prompt_template_key,
                   prompt_template_version)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11,
                        $12, $13::jsonb, $14, $15, $16)
                """,
                task_id, niche, "tech", approver,
                "pre-hash-stub", "post-hash-stub",
                25, 2, 1000, 1025,
                "manual", approved_at, json.dumps({}),
                model, prompt_key, prompt_version,
            )

            # Read back through the view
            rows = await conn.fetch(
                "SELECT * FROM lab_outcomes_v1 WHERE task_id = $1",
                task_id,
            )

        assert len(rows) == 1, (
            f"Expected exactly one view row for task {task_id}, got {len(rows)}"
        )
        row = rows[0]

        # capability_outcomes columns
        assert row["niche_slug"] == niche
        assert row["template_slug"] == "canonical_blog"
        assert row["atom_name"] == "atoms.test_writer"
        assert row["model_used"] == model
        assert row["prompt_template_key"] == prompt_key
        assert row["prompt_template_version"] == prompt_version
        assert row["atom_ok"] is True
        assert row["atom_halted"] is False
        assert float(row["atom_quality_score"]) == pytest.approx(87.5)
        assert row["elapsed_ms"] == 1234

        # routing_outcomes columns
        assert float(row["actual_cost"]) == pytest.approx(0.0039)
        assert float(row["estimated_cost"]) == pytest.approx(0.0042)
        assert row["compute_tier"] == "standard"
        assert row["routing_success"] is True

        # published_post_edit_metrics columns
        assert row["approver"] == approver
        assert row["char_diff_count"] == 25
        assert row["line_diff_count"] == 2
        assert row["pre_approve_len"] == 1000
        assert row["post_approve_len"] == 1025
        assert row["approve_method"] == "manual"
        assert row["approved_at"] is not None

        # page_views columns — NULL/0 until the beacon fix lands.
        # post_id is NULL above so the LATERAL filters down to zero rows.
        assert row["views_24h_post_publish"] in (None, 0)
        assert row["views_7d_post_publish"] in (None, 0)
    finally:
        async with test_pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM published_post_edit_metrics WHERE task_id = $1",
                task_id,
            )
            await conn.execute(
                "DELETE FROM routing_outcomes WHERE task_id = $1",
                task_id,
            )
            await conn.execute(
                "DELETE FROM capability_outcomes WHERE task_id = $1",
                task_id,
            )


async def test_view_returns_capability_row_when_other_tables_empty(test_pool):
    """A capability_outcomes row without matching routing/edit rows
    must still appear in the view (LEFT JOIN semantics)."""
    task_id = str(uuid4())
    try:
        async with test_pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO capability_outcomes
                  (task_id, template_slug, node_name, atom_name,
                   capability_tier, model_used,
                   ok, halted, elapsed_ms, quality_score, metrics,
                   niche_slug)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10,
                        $11::jsonb, $12)
                """,
                task_id, "canonical_blog", "verify_task", "stage.verify_task",
                None, None,
                True, False, 50, None,
                json.dumps({}),
                "test-niche-isolated",
            )
            rows = await conn.fetch(
                "SELECT task_id, niche_slug, approver, actual_cost "
                "FROM lab_outcomes_v1 WHERE task_id = $1",
                task_id,
            )
        assert len(rows) == 1
        assert rows[0]["niche_slug"] == "test-niche-isolated"
        # No routing / edit metric rows for this task → NULLs on those fields
        assert rows[0]["approver"] is None
        assert rows[0]["actual_cost"] is None
    finally:
        async with test_pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM capability_outcomes WHERE task_id = $1",
                task_id,
            )


async def test_view_respects_90_day_window(test_pool):
    """The view's WHERE co.created_at > NOW() - INTERVAL '90 days' clause
    must exclude old rows. Insert a row with a 100-day-old created_at
    via an explicit override and confirm the view skips it.

    Uses a direct UPDATE to set created_at because the default is NOW()
    and capability_outcomes' INSERT contract doesn't expose created_at.
    """
    task_id = str(uuid4())
    try:
        async with test_pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO capability_outcomes
                  (task_id, template_slug, node_name, atom_name,
                   capability_tier, model_used,
                   ok, halted, elapsed_ms, quality_score, metrics)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11::jsonb)
                """,
                task_id, "canonical_blog", "test_old", "test_old",
                None, None, True, False, 1, None, json.dumps({}),
            )
            # Push it past the 90-day window
            old_ts = datetime.now(timezone.utc) - timedelta(days=100)
            await conn.execute(
                "UPDATE capability_outcomes SET created_at = $2 WHERE task_id = $1",
                task_id, old_ts,
            )
            rows = await conn.fetch(
                "SELECT task_id FROM lab_outcomes_v1 WHERE task_id = $1",
                task_id,
            )
        # The 90-day filter must drop this row.
        assert len(rows) == 0
    finally:
        async with test_pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM capability_outcomes WHERE task_id = $1",
                task_id,
            )
