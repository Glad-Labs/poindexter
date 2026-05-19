"""Tests for the ``20260519_211809_niches_default_template_slug`` migration.

The migration:

1. Adds ``niches.default_template_slug text`` (nullable).
2. Seeds ``dev_diary`` → ``'dev_diary'`` and ``glad-labs`` →
   ``'canonical_blog'`` (when the rows exist + the column is still NULL,
   so operator overrides survive a replay).
3. Backfills legacy ``pipeline_tasks`` rows whose ``template_slug`` is
   NULL or empty string: ``dev_diary`` niche → ``'dev_diary'``,
   everything else → ``'canonical_blog'``.

This test runs against the real Postgres test DB (``db_pool`` fixture).
The fixture already runs the migration as part of session setup, so we
verify the post-state directly rather than re-running the migration.
"""

from uuid import uuid4

import pytest

pytestmark = pytest.mark.asyncio(loop_scope="session")


@pytest.mark.unit
class TestNichesDefaultTemplateSlugColumn:
    """Column structure + dev_diary seed verification."""

    async def test_column_exists_and_is_nullable(self, db_pool):
        async with db_pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT data_type, is_nullable
                  FROM information_schema.columns
                 WHERE table_name = 'niches'
                   AND column_name = 'default_template_slug'
                """
            )
        assert row is not None, "column not created by migration"
        assert row["data_type"] == "text"
        assert row["is_nullable"] == "YES", (
            "column must be nullable so niches without a per-niche "
            "preference fall through to app_settings"
        )

    async def test_dev_diary_seed_value(self, db_pool):
        """The baseline-seeded dev_diary row should get
        default_template_slug='dev_diary' after the migration runs.
        """
        async with db_pool.acquire() as conn:
            value = await conn.fetchval(
                "SELECT default_template_slug FROM niches "
                "WHERE slug = 'dev_diary'"
            )
        assert value == "dev_diary"


@pytest.mark.unit
class TestPipelineTasksBackfill:
    """Backfill behaviour for pre-existing rows. The migration ran at
    fixture setup time before any test-inserted rows existed, so we
    re-seed rows here and verify the *forward-going* invariant the
    fix enforces: a fresh INSERT that explicitly sets template_slug
    keeps that value, and the operator can fix legacy rows by hand
    via the same UPDATE pattern the migration uses.
    """

    async def test_backfill_pattern_targets_null_and_empty(self, db_pool):
        """Mirror the migration's WHERE clause against test rows so we
        confirm the same SQL idempotently backfills both NULL and ''
        without stomping non-empty values.
        """
        # Seed a dev_diary row with NULL slug, a glad-labs row with
        # empty-string slug, and a row that already has a value
        # (must survive untouched).
        rows = [
            (str(uuid4()), "dev_diary", None),
            (str(uuid4()), "glad-labs", ""),
            (str(uuid4()), "glad-labs", "dev_diary"),  # operator override
            (str(uuid4()), None, None),               # niche-less legacy row
        ]
        async with db_pool.acquire() as conn:
            for task_id, niche, slug in rows:
                await conn.execute(
                    """
                    INSERT INTO pipeline_tasks
                      (task_id, task_type, topic, status, stage,
                       niche_slug, template_slug)
                    VALUES ($1, 'blog_post', 'test topic', 'pending',
                            'pending', $2, $3)
                    """,
                    task_id, niche, slug,
                )

            # Run the same UPDATE statements the migration uses (we
            # can't replay the migration cleanly, but the SQL is
            # one-shot idempotent — running it against the
            # already-migrated DB just re-applies the rules to any
            # rows we just inserted).
            await conn.execute(
                """
                UPDATE pipeline_tasks
                   SET template_slug = 'dev_diary'
                 WHERE niche_slug = 'dev_diary'
                   AND (template_slug IS NULL OR template_slug = '')
                """
            )
            await conn.execute(
                """
                UPDATE pipeline_tasks
                   SET template_slug = 'canonical_blog'
                 WHERE (niche_slug IS DISTINCT FROM 'dev_diary')
                   AND (template_slug IS NULL OR template_slug = '')
                """
            )

            # Verify each row landed in the expected state.
            result = {
                r["task_id"]: r["template_slug"]
                for r in await conn.fetch(
                    "SELECT task_id, template_slug FROM pipeline_tasks "
                    "WHERE task_id = ANY($1::text[])",
                    [r[0] for r in rows],
                )
            }

            assert result[rows[0][0]] == "dev_diary"          # NULL dev_diary
            assert result[rows[1][0]] == "canonical_blog"    # '' glad-labs
            assert result[rows[2][0]] == "dev_diary"          # operator override survives
            assert result[rows[3][0]] == "canonical_blog"    # niche-less legacy

            # Cleanup so we don't leave test rows behind for the next
            # test in this session.
            await conn.execute(
                "DELETE FROM pipeline_tasks WHERE task_id = ANY($1::text[])",
                [r[0] for r in rows],
            )

    async def test_seed_is_idempotent_against_operator_override(self, db_pool):
        """Re-running the seed UPDATE with a WHERE
        default_template_slug IS NULL guard must not stomp an
        operator-tuned value. We simulate an operator override on a
        throwaway niche and replay the seed pattern.
        """
        slug = f"test-idempotence-{uuid4().hex[:8]}"
        async with db_pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO niches
                  (slug, name, default_template_slug)
                VALUES ($1, 'Idempotence Test', 'operator_override')
                """,
                slug,
            )

            # Replay the migration's seed-style UPDATE — operator
            # override must survive because the WHERE clause requires
            # the column to be NULL.
            await conn.execute(
                """
                UPDATE niches
                   SET default_template_slug = 'should-not-overwrite'
                 WHERE slug = $1
                   AND default_template_slug IS NULL
                """,
                slug,
            )

            value = await conn.fetchval(
                "SELECT default_template_slug FROM niches WHERE slug = $1",
                slug,
            )
            assert value == "operator_override"
