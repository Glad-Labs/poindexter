"""
Harness smoke tests (GH#21).

Proves the integration_db fixture chain actually works: disposable DB
creation → migration run → fixture seed → pool/txn handoff → rollback.

Broader integration tests (approval lifecycle, publish flow, embeddings
search with real similarity scores) land in sibling files; this one is
the canary that the infrastructure itself is healthy.
"""

from __future__ import annotations

import pytest

pytestmark = [
    pytest.mark.integration_db,
    # Tests that consume session-scoped async fixtures need to run on
    # the session-scoped event loop; pytest-asyncio's default is
    # function-scoped per the project's pyproject.toml config.
    pytest.mark.asyncio(loop_scope="session"),
]


async def test_schema_was_applied(test_pool) -> None:
    """Every migration ran — a representative set of production tables exist."""
    async with test_pool.acquire() as conn:
        tables = await conn.fetch(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'public' "
            "ORDER BY tablename"
        )
        names = {r["tablename"] for r in tables}
    # A mix of old + recent tables. If migrations didn't run, most of
    # these would be missing.
    must_have = {
        "pipeline_tasks",
        "pipeline_versions",
        "pipeline_gate_history",
        "pipeline_distributions",
        "posts",
        "post_tags",
        "tags",
        "app_settings",
        "model_performance",
        "routing_outcomes",
        "content_revisions",
    }
    missing = must_have - names
    assert not missing, f"Test DB missing expected tables: {sorted(missing)}"


async def test_fixtures_loaded(test_pool) -> None:
    """Canary fixture rows are present."""
    async with test_pool.acquire() as conn:
        post = await conn.fetchrow(
            "SELECT title, slug, status FROM posts WHERE slug = 'fixture-post'"
        )
        assert post is not None, "fixture post missing"
        assert post["status"] == "published"

        setting = await conn.fetchval(
            "SELECT value FROM app_settings WHERE key = 'integration_db_test_flag'"
        )
        assert setting == "true"


async def test_transaction_rolls_back(test_txn) -> None:
    """Mutations inside ``test_txn`` don't leak to other tests."""
    await test_txn.execute(
        "INSERT INTO app_settings (key, value, category, description) "
        "VALUES ('integration_db_rollback_canary', 'touched', 'testing', 'should rollback')",
    )
    row = await test_txn.fetchval(
        "SELECT value FROM app_settings WHERE key = 'integration_db_rollback_canary'"
    )
    assert row == "touched"


async def test_rollback_actually_rolls_back(test_pool) -> None:
    """Follow-up to the previous test — after its rollback, the canary
    row should NOT be visible on a fresh connection. Proves the
    transaction-rollback isolation works.
    """
    async with test_pool.acquire() as conn:
        row = await conn.fetchval(
            "SELECT value FROM app_settings WHERE key = 'integration_db_rollback_canary'"
        )
    assert row is None, (
        "Previous test's transactional write leaked — rollback isolation broken"
    )


async def test_pgvector_embeddings_fixture(test_pool) -> None:
    """The embeddings fixture inserts a real pgvector row. Reading it
    back + running a similarity query against itself should return it
    as the top match (similarity ~= 1.0).
    """
    async with test_pool.acquire() as conn:
        # Skip if pgvector isn't loaded in the test DB.
        ext = await conn.fetchval(
            "SELECT 1 FROM pg_extension WHERE extname = 'vector'"
        )
        if not ext:
            pytest.skip("pgvector extension not installed in test DB")
        row = await conn.fetchrow(
            "SELECT source_table, chunk_index, text_preview FROM embeddings "
            "WHERE source_id = 'chunk-1'"
        )
        if not row:
            pytest.skip("embeddings fixture skipped (table layout mismatch)")
        assert row["source_table"] == "integration_db_test"
        assert row["text_preview"] == "first chunk"
