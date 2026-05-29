"""Integration tests for the Phase 1 experiments harness foundation migration.

Asserts the schema delta from
``20260529_000342_phase1_experiments_harness_foundation.py`` lands
correctly and behaves as the design doc promises:

1. The full migration sequence (baseline + post-baseline + this one)
   applies cleanly against a fresh disposable test DB.
2. One-active-per-niche is enforced at the SQL layer (partial unique
   index → clean UNIQUE violation on the second transition).
3. ``ON DELETE CASCADE`` on experiment_variants.experiment_id deletes
   children when the parent experiment row is removed.
4. ``ON DELETE SET NULL`` on capability_outcomes.variant_id preserves
   historical outcome rows when the variant row is cleaned up.
5. ``lab_outcomes_v1`` still returns the existing rows + has the new
   variant/experiment columns (NULL for the historical rows since the
   FK was just added).
6. ``experiment_variant_scorecard_v1`` returns one row per
   (experiment, variant) even when the variant has zero outcomes.
7. The scorecard rolls up correctly when one variant has tagged
   outcomes and another doesn't.

Run with:

    cd src/cofounder_agent && \\
      poetry run pytest tests/integration_db/test_phase1_experiments_foundation.py -v

The fixtures are inherited from ``tests/integration_db/conftest.py``;
the whole tier skips cleanly when no live Postgres is reachable.
"""

from __future__ import annotations

import uuid

import asyncpg
import pytest

pytestmark = [
    pytest.mark.integration_db,
    pytest.mark.asyncio(loop_scope="session"),
]


# ---------------------------------------------------------------------------
# 1. Migration applies cleanly on a fresh DB.
#
# The session-scoped ``test_pool`` fixture in conftest already runs the
# full migration tree against the disposable DB before yielding the
# pool. If the migration raised, the fixture would have failed earlier
# — but we make the contract explicit here by asserting both new tables
# + both views exist post-fixture.
# ---------------------------------------------------------------------------
async def test_migration_applied_cleanly(test_pool) -> None:
    async with test_pool.acquire() as conn:
        tables = {
            r["tablename"]
            for r in await conn.fetch(
                "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
            )
        }
        views = {
            r["viewname"]
            for r in await conn.fetch(
                "SELECT viewname FROM pg_views WHERE schemaname = 'public'"
            )
        }

    assert "experiments" in tables, "experiments table missing post-migration"
    assert "experiment_variants" in tables, (
        "experiment_variants table missing post-migration"
    )
    assert "lab_outcomes_v1" in views, (
        "lab_outcomes_v1 view missing post-migration (Phase 0 dependency)"
    )
    assert "experiment_variant_scorecard_v1" in views, (
        "experiment_variant_scorecard_v1 view missing post-migration"
    )


# ---------------------------------------------------------------------------
# 2. One-active-per-niche constraint.
#
# Two experiments on the same niche can BOTH be 'draft' (and 'paused',
# 'concluded') — the partial unique index only fires for status='active'.
# The second UPDATE to active must raise UniqueViolationError.
#
# Uses a transaction so the inserted rows roll back at teardown (the
# violation rolls back in any case but we set this up explicitly).
# ---------------------------------------------------------------------------
async def test_one_active_per_niche_enforced(test_txn) -> None:
    # Two drafts on the same niche — should both succeed
    await test_txn.execute(
        """
        INSERT INTO experiments (key, niche_slug, status)
        VALUES ($1, 'phase1-test-niche', 'draft'),
               ($2, 'phase1-test-niche', 'draft')
        """,
        f"phase1-test/exp-a-{uuid.uuid4().hex[:8]}",
        f"phase1-test/exp-b-{uuid.uuid4().hex[:8]}",
    )

    # First → active is fine
    await test_txn.execute(
        """
        UPDATE experiments
        SET status = 'active', activated_at = now()
        WHERE niche_slug = 'phase1-test-niche'
          AND id = (
            SELECT id FROM experiments
            WHERE niche_slug = 'phase1-test-niche'
            ORDER BY created_at LIMIT 1
          )
        """
    )

    # Second → active must violate the partial unique index
    with pytest.raises(asyncpg.exceptions.UniqueViolationError):
        await test_txn.execute(
            """
            UPDATE experiments
            SET status = 'active', activated_at = now()
            WHERE niche_slug = 'phase1-test-niche'
              AND status = 'draft'
            """
        )


# ---------------------------------------------------------------------------
# 3. ON DELETE CASCADE on experiment_variants.experiment_id.
#
# Deleting the parent experiment must take its variants with it. The
# design doc + task spec both lock CASCADE here (variants are
# meaningless without their experiment).
# ---------------------------------------------------------------------------
async def test_variants_cascade_on_experiment_delete(test_txn) -> None:
    exp_id = await test_txn.fetchval(
        """
        INSERT INTO experiments (key, niche_slug, status)
        VALUES ($1, 'phase1-cascade-niche', 'draft')
        RETURNING id
        """,
        f"phase1-cascade/exp-{uuid.uuid4().hex[:8]}",
    )

    await test_txn.execute(
        """
        INSERT INTO experiment_variants (experiment_id, label)
        VALUES ($1, 'A'), ($1, 'B')
        """,
        exp_id,
    )

    pre_count = await test_txn.fetchval(
        "SELECT COUNT(*) FROM experiment_variants WHERE experiment_id = $1",
        exp_id,
    )
    assert pre_count == 2, f"expected 2 variants pre-delete, got {pre_count}"

    await test_txn.execute("DELETE FROM experiments WHERE id = $1", exp_id)

    post_count = await test_txn.fetchval(
        "SELECT COUNT(*) FROM experiment_variants WHERE experiment_id = $1",
        exp_id,
    )
    assert post_count == 0, (
        f"variants did not CASCADE-delete with parent experiment: "
        f"{post_count} rows remain"
    )


# ---------------------------------------------------------------------------
# 4. ON DELETE SET NULL on capability_outcomes.variant_id.
#
# Deleting a variant must NOT delete the historical capability_outcomes
# rows tagged to it — we keep them for posterity (rolled up into the
# "unattributed" bucket once variant_id is NULL).
# ---------------------------------------------------------------------------
async def test_capability_outcomes_set_null_on_variant_delete(test_txn) -> None:
    exp_id = await test_txn.fetchval(
        """
        INSERT INTO experiments (key, niche_slug, status)
        VALUES ($1, 'phase1-setnull-niche', 'draft')
        RETURNING id
        """,
        f"phase1-setnull/exp-{uuid.uuid4().hex[:8]}",
    )
    variant_id = await test_txn.fetchval(
        """
        INSERT INTO experiment_variants (experiment_id, label)
        VALUES ($1, 'A')
        RETURNING id
        """,
        exp_id,
    )

    # Insert a minimal capability_outcomes row tagged to this variant.
    # Required NOT NULL columns are template_slug, node_name, ok — id
    # comes from the sequence default.
    task_id = f"phase1-task-{uuid.uuid4().hex[:8]}"
    co_id = await test_txn.fetchval(
        """
        INSERT INTO capability_outcomes (
            task_id, template_slug, node_name, ok, variant_id
        )
        VALUES ($1, 'canonical_blog', 'generate_content', TRUE, $2)
        RETURNING id
        """,
        task_id,
        variant_id,
    )

    # Delete the variant — the FK action must SET NULL, not cascade.
    await test_txn.execute(
        "DELETE FROM experiment_variants WHERE id = $1", variant_id
    )

    surviving = await test_txn.fetchrow(
        "SELECT id, variant_id FROM capability_outcomes WHERE id = $1",
        co_id,
    )
    assert surviving is not None, (
        "capability_outcomes row was deleted with variant — expected SET NULL"
    )
    assert surviving["variant_id"] is None, (
        "variant_id was not SET NULL after parent variant delete: "
        f"got {surviving['variant_id']!r}"
    )


# ---------------------------------------------------------------------------
# 5. lab_outcomes_v1 returns the new columns (NULL for un-tagged rows).
#
# Insert a capability_outcomes row WITHOUT a variant_id and verify the
# view returns it with the five new columns all NULL. This is the
# historical-rows case — every row that landed before this migration
# has variant_id=NULL and should still flow through the view.
# ---------------------------------------------------------------------------
async def test_lab_outcomes_v1_returns_new_columns_null_for_untagged_rows(
    test_txn,
) -> None:
    task_id = f"phase1-historical-{uuid.uuid4().hex[:8]}"
    await test_txn.execute(
        """
        INSERT INTO capability_outcomes (task_id, template_slug, node_name, ok)
        VALUES ($1, 'canonical_blog', 'generate_content', TRUE)
        """,
        task_id,
    )

    row = await test_txn.fetchrow(
        "SELECT * FROM lab_outcomes_v1 WHERE task_id = $1",
        task_id,
    )
    assert row is not None, (
        "lab_outcomes_v1 dropped the un-tagged row — view contract broken "
        "(historical rows must still appear with NULL variant context)"
    )
    # The five new columns must exist on the view and be NULL here.
    for col in (
        "variant_label",
        "variant_id",
        "experiment_key",
        "experiment_status",
        "experiment_objective_function",
    ):
        assert col in row, f"lab_outcomes_v1 missing new column: {col!r}"
        assert row[col] is None, (
            f"lab_outcomes_v1 column {col!r} should be NULL for un-tagged "
            f"capability_outcomes row, got {row[col]!r}"
        )


# ---------------------------------------------------------------------------
# 6. Scorecard returns one row per (experiment, variant) even with no outcomes.
#
# LEFT JOIN behavior — a newly-added variant with zero outcomes should
# still surface in the scorecard so operators can see it exists.
# posts_attempted should be 0 (COUNT over zero LEFT-joined rows).
# ---------------------------------------------------------------------------
async def test_scorecard_returns_row_for_variant_with_no_outcomes(test_txn) -> None:
    exp_id = await test_txn.fetchval(
        """
        INSERT INTO experiments (key, niche_slug, status)
        VALUES ($1, 'phase1-empty-niche', 'draft')
        RETURNING id
        """,
        f"phase1-empty/exp-{uuid.uuid4().hex[:8]}",
    )
    await test_txn.execute(
        """
        INSERT INTO experiment_variants (experiment_id, label)
        VALUES ($1, 'A'), ($1, 'B')
        """,
        exp_id,
    )

    rows = await test_txn.fetch(
        """
        SELECT variant_label, posts_attempted, posts_approved
        FROM experiment_variant_scorecard_v1
        WHERE experiment_id = $1
        ORDER BY variant_label
        """,
        exp_id,
    )
    assert len(rows) == 2, (
        f"scorecard should return one row per variant; got {len(rows)} "
        f"for an experiment with 2 variants and no outcomes"
    )
    for r in rows:
        assert r["posts_attempted"] == 0, (
            f"variant {r['variant_label']!r} should show posts_attempted=0 "
            f"with no outcomes, got {r['posts_attempted']}"
        )
        assert r["posts_approved"] == 0, (
            f"variant {r['variant_label']!r} should show posts_approved=0 "
            f"with no outcomes, got {r['posts_approved']}"
        )


# ---------------------------------------------------------------------------
# 7. Scorecard rolls up correctly when one variant has data.
#
# Insert 5 capability_outcomes rows tagged to variant A, none tagged to
# variant B. Assert A shows posts_attempted=5 and B shows 0. Each
# outcome must have a distinct task_id (the view uses COUNT(DISTINCT
# lo.task_id)).
# ---------------------------------------------------------------------------
async def test_scorecard_rolls_up_correctly(test_txn) -> None:
    exp_id = await test_txn.fetchval(
        """
        INSERT INTO experiments (key, niche_slug, status)
        VALUES ($1, 'phase1-rollup-niche', 'draft')
        RETURNING id
        """,
        f"phase1-rollup/exp-{uuid.uuid4().hex[:8]}",
    )
    variant_a_id = await test_txn.fetchval(
        """
        INSERT INTO experiment_variants (experiment_id, label)
        VALUES ($1, 'A')
        RETURNING id
        """,
        exp_id,
    )
    await test_txn.execute(
        """
        INSERT INTO experiment_variants (experiment_id, label)
        VALUES ($1, 'B')
        """,
        exp_id,
    )

    # 5 outcomes tagged to A, distinct task_ids so COUNT(DISTINCT) sees 5.
    for i in range(5):
        await test_txn.execute(
            """
            INSERT INTO capability_outcomes (
                task_id, template_slug, node_name, ok, variant_id
            )
            VALUES ($1, 'canonical_blog', 'generate_content', TRUE, $2)
            """,
            f"phase1-rollup-task-{uuid.uuid4().hex[:8]}-{i}",
            variant_a_id,
        )

    rows = {
        r["variant_label"]: r
        for r in await test_txn.fetch(
            """
            SELECT variant_label, posts_attempted
            FROM experiment_variant_scorecard_v1
            WHERE experiment_id = $1
            """,
            exp_id,
        )
    }

    assert rows["A"]["posts_attempted"] == 5, (
        f"variant A should have rolled up 5 outcomes, "
        f"got {rows['A']['posts_attempted']}"
    )
    assert rows["B"]["posts_attempted"] == 0, (
        f"variant B should have 0 outcomes (none tagged), "
        f"got {rows['B']['posts_attempted']}"
    )
