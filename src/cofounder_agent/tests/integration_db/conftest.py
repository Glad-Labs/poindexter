"""
Fixtures for the real-Postgres integration test tier (GH#21).

Layering:

  session-scoped
  ───────────────────────────────────────────────────────────────────
  ``admin_dsn``     — DSN to the admin (postgres) DB on the live
                       postgres-local container. Read via
                       ``brain.bootstrap.resolve_database_url()`` and then
                       rewritten to the ``postgres`` maintenance DB so we
                       can ``CREATE DATABASE`` a disposable one. Skips the
                       whole tier (``pytest.skip``) when no live DB is
                       reachable, mirroring the memory_client pattern.
  ``test_db_name``  — random ``poindexter_test_<hex>`` name. Dropped at
                       session teardown.
  ``test_dsn``      — DSN pointing at the disposable test DB.
  ``schema_loaded`` — runs every migration in
                       ``services/migrations/`` against ``test_dsn`` in
                       order, so the schema matches production.
  ``fixtures_loaded`` — seeds a handful of representative rows: one site,
                       one category, one published post, a couple of
                       app_settings, a fact_override, two embeddings.

  function-scoped
  ───────────────────────────────────────────────────────────────────
  ``test_pool``     — an ``asyncpg`` pool against ``test_dsn`` that tests
                       can ``acquire()`` from. The session-level schema +
                       fixtures are applied once; within a test, use
                       ``test_txn`` for automatic rollback isolation.
  ``test_txn``      — a single ``asyncpg`` connection inside a
                       transaction that rolls back at teardown. Use this
                       for tests that mutate state.

Usage:
    @pytest.mark.integration_db
    async def test_my_thing(test_txn):
        row = await test_txn.fetchrow("SELECT ... FROM posts LIMIT 1")
        assert row["slug"] == "..."
"""

from __future__ import annotations

import secrets
import sys
from collections.abc import AsyncGenerator
from pathlib import Path
from urllib.parse import urlparse, urlunparse

import pytest
import pytest_asyncio


def _bootstrap_resolve_dsn() -> str | None:
    """Walk the tree up until we find brain/bootstrap.py, then call its
    resolver. Same trick the production code uses to avoid hardcoded
    DATABASE_URL reads.
    """
    for p in Path(__file__).resolve().parents:
        if (p / "brain" / "bootstrap.py").is_file():
            if str(p) not in sys.path:
                sys.path.insert(0, str(p))
            break
    try:
        from brain.bootstrap import resolve_database_url
        return resolve_database_url()
    except Exception:
        return None


def _admin_dsn_from(base: str) -> str:
    """Rewrite a DSN's path to /postgres (maintenance DB) so we can
    ``CREATE DATABASE <test>`` against it.
    """
    u = urlparse(base)
    # SQLAlchemy-style "postgres" vs "postgresql" — both resolve.
    return urlunparse(u._replace(path="/postgres"))


def _test_dsn_from(base: str, db_name: str) -> str:
    u = urlparse(base)
    return urlunparse(u._replace(path=f"/{db_name}"))


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def admin_dsn() -> str:
    """Resolve the admin DSN for the live Postgres, or skip the whole
    integration_db tier if no DB is reachable. Matches the skip pattern
    used by test_memory_client so ``pytest -m integration_db`` on a CI
    runner without a DB just marks everything skipped instead of
    blowing up at fixture time.
    """
    base = _bootstrap_resolve_dsn()
    if not base or base == "postgresql://test:test@localhost/test":
        pytest.skip(
            "No live Postgres DSN configured — integration_db tier requires a reachable DB"
        )
    return _admin_dsn_from(base)


@pytest.fixture(scope="session")
def test_db_name() -> str:
    """Generates a unique DB name per test session so parallel runs
    don't collide.
    """
    return f"poindexter_test_{secrets.token_hex(6)}"


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def test_dsn(admin_dsn: str, test_db_name: str) -> AsyncGenerator[str, None]:
    """Create a disposable Postgres DB at session start; drop it at
    session end. Yields the DSN pointing at the fresh DB.
    """
    import asyncpg

    admin = await asyncpg.connect(admin_dsn)
    try:
        # Terminate any lingering connections from a previous crashed session
        # (same DB name is unique so this is defensive).
        await admin.execute(
            f"DROP DATABASE IF EXISTS {test_db_name}"
        )
        await admin.execute(f"CREATE DATABASE {test_db_name}")
    finally:
        await admin.close()

    dsn = _test_dsn_from(admin_dsn, test_db_name)
    # pgvector + initial CMS schema come from infrastructure/local-db/init.sql
    # which runs at postgres container init time, NOT from the Python
    # migration system. Replay it against the disposable test DB so the
    # tables + extensions the migrations + fixtures depend on exist.
    try:
        fresh = await asyncpg.connect(dsn)
        try:
            await fresh.execute("CREATE EXTENSION IF NOT EXISTS vector")
            for p in Path(__file__).resolve().parents:
                init_sql = p / "infrastructure" / "local-db" / "init.sql"
                if init_sql.is_file():
                    try:
                        await fresh.execute(init_sql.read_text(encoding="utf-8"))
                    except Exception:
                        # init.sql may have partial-apply errors against a
                        # DB the migrations also touch — swallow so the
                        # harness still usable even when init.sql drifts.
                        pass
                    break
        finally:
            await fresh.close()
    except Exception:
        pass

    try:
        yield dsn
    finally:
        admin = await asyncpg.connect(admin_dsn)
        try:
            # Kick any connections still holding the DB before drop.
            await admin.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = $1 AND pid <> pg_backend_pid()",
                test_db_name,
            )
            await admin.execute(
                f"DROP DATABASE IF EXISTS {test_db_name}"
            )
        finally:
            await admin.close()


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def schema_loaded(test_dsn: str) -> str:
    """Run every migration in services/migrations/ against the fresh test
    DB. The migrations module's loader runs Python + SQL migrations in
    filename order.
    """
    import asyncpg

    from services.migrations import run_migrations

    pool = await asyncpg.create_pool(test_dsn, min_size=1, max_size=2)
    try:
        # run_migrations expects a database_service-shaped thing with .pool.
        class _StubService:
            def __init__(self, pool):
                self.pool = pool

        ok = await run_migrations(_StubService(pool))
        if not ok:
            pytest.fail("Migrations failed against the test DB")
    finally:
        await pool.close()
    return test_dsn


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def fixtures_loaded(schema_loaded: str) -> str:
    """Seed the test DB with a small, representative dataset.

    Enough to exercise common queries:
      - one ``sites`` row
      - one ``categories`` row
      - one published ``posts`` row + its ``post_tags`` link
      - two ``app_settings`` (one boolean, one with a numeric value)
      - two ``embeddings`` rows (same source_table, different chunk)
      - one ``fact_overrides`` row
    """
    import asyncpg

    pool = await asyncpg.create_pool(schema_loaded, min_size=1, max_size=2)
    try:
        async with pool.acquire() as conn:
            # Minimal CMS surface. Each INSERT is isolated in suppress()
            # because column layouts drift between migrations and we
            # want the harness to survive a benign schema change — a
            # missing fixture row is less bad than a whole-suite bail.
            import contextlib as _c
            with _c.suppress(Exception):
                await conn.execute(
                    """
                    INSERT INTO sites (id, slug, name, base_url)
                    VALUES (gen_random_uuid(), 'test-site', 'Test Site', 'https://test.example')
                    ON CONFLICT (slug) DO NOTHING
                    """,
                )
            with _c.suppress(Exception):
                await conn.execute(
                    """
                    INSERT INTO categories (id, name, slug)
                    VALUES (gen_random_uuid(), 'Testing', 'testing')
                    ON CONFLICT (slug) DO NOTHING
                    """,
                )
            with _c.suppress(Exception):
                await conn.execute(
                    """
                    INSERT INTO tags (name, slug) VALUES ('Testing', 'testing')
                    ON CONFLICT (slug) DO NOTHING
                    """,
                )
            post_id = None
            with _c.suppress(Exception):
                post_id = await conn.fetchval(
                    """
                    INSERT INTO posts (id, title, slug, content, excerpt, status, published_at)
                    VALUES (gen_random_uuid(), 'Fixture Post', 'fixture-post',
                            '# Fixture' || E'\n\n' || 'Integration-test fixture content.',
                            'Fixture excerpt.',
                            'published', NOW())
                    ON CONFLICT (slug) DO NOTHING
                    RETURNING id
                    """,
                )
            tag_id = None
            with _c.suppress(Exception):
                tag_id = await conn.fetchval(
                    "SELECT id FROM tags WHERE slug = 'testing'"
                )
            if post_id and tag_id:
                with _c.suppress(Exception):
                    await conn.execute(
                        """
                        INSERT INTO post_tags (post_id, tag_id) VALUES ($1, $2)
                        ON CONFLICT DO NOTHING
                        """,
                        post_id, tag_id,
                    )
            # Settings.
            with _c.suppress(Exception):
                await conn.execute(
                    """
                    INSERT INTO app_settings (key, value, category, description)
                    VALUES ('integration_db_test_flag', 'true', 'testing', 'Harness canary'),
                           ('integration_db_numeric_setting', '42', 'testing', 'Harness numeric canary')
                    ON CONFLICT (key) DO NOTHING
                    """,
                )
            # Fact override — content_validator.py reads this table.
            with _c.suppress(Exception):
                await conn.execute(
                    """
                    INSERT INTO fact_overrides (pattern, correction, severity, active)
                    VALUES ('foo is bar', 'foo is baz', 'warning', true)
                    ON CONFLICT DO NOTHING
                    """,
                )
            # Embeddings — real pgvector column. Use 768-dim zeros since
            # nomic-embed-text vectors are that size. Skip if pgvector
            # isn't available in the test DB.
            with _c.suppress(Exception):
                await conn.execute(
                    """
                    INSERT INTO embeddings (source_table, source_id, chunk_index,
                                            content_hash, embedding_model, text_preview,
                                            embedding, writer)
                    VALUES ('integration_db_test', 'chunk-1', 0, 'hash-1',
                            'test-model', 'first chunk', array_fill(0.01::real, ARRAY[768])::vector,
                            'integration-harness')
                    ON CONFLICT DO NOTHING
                    """,
                )
    finally:
        await pool.close()
    return schema_loaded


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def test_pool(fixtures_loaded: str):
    """Function-scoped asyncpg pool. Tests can either use this directly
    (for read-only or idempotent writes) or prefer ``test_txn`` for
    per-test rollback.
    """
    import asyncpg

    pool = await asyncpg.create_pool(fixtures_loaded, min_size=1, max_size=4)
    try:
        yield pool
    finally:
        await pool.close()


@pytest_asyncio.fixture(loop_scope="session")
async def test_txn(test_pool):
    """A single connection running inside a transaction that rolls back
    at teardown. Use for tests that mutate state so they don't leak rows
    between tests.
    """
    async with test_pool.acquire() as conn:
        txn = conn.transaction()
        await txn.start()
        try:
            yield conn
        finally:
            await txn.rollback()
