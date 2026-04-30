"""Integration: end-to-end niche topic-discovery flow.

Spec: docs/superpowers/specs/2026-04-30-rag-pivot-niche-discovery-design.md
Plan: docs/superpowers/plans/2026-04-30-rag-pivot-niche-discovery.md (Task 18)

Two scenarios:

1. ``test_glad_labs_sweep_produces_a_batch`` — verifies the seeded
   ``glad-labs`` niche exists, runs a full sweep against the test DB,
   and asserts an open batch with at least one candidate is created.
2. ``test_resolve_advances_to_content_task`` — manually seeds a batch +
   candidate, ranks + resolves, and asserts a ``content_tasks`` row
   landed with the right ``niche_slug`` / ``writer_rag_mode`` /
   ``topic_batch_id`` provenance.

Self-contained DB fixture
=========================
The unit-tier ``db_pool`` fixture (``tests/unit/conftest.py``) is not on
this directory's conftest chain, and the integration tier's existing
``real_pool`` fixture is gated behind ``INTEGRATION_TESTS=1`` +
``REAL_SERVICES_TESTS=1`` and only loads ``init_test_schema.sql`` (not
the niche migrations 0113-0115). To keep this file standalone + self-
hermetic, we redefine the same disposable-test-DB pattern here. The
fixture skips the whole module if no live Postgres DSN is reachable.
"""

from __future__ import annotations

import secrets
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse, urlunparse

import pytest
import pytest_asyncio

from services.internal_rag_source import InternalCandidate
from services.niche_service import NicheService
from services.topic_batch_service import TopicBatchService

pytestmark = [pytest.mark.asyncio(loop_scope="session"), pytest.mark.integration]


# ---------------------------------------------------------------------------
# Disposable DB fixture (mirrors unit-tier ``db_pool``)
# ---------------------------------------------------------------------------


def _bootstrap_resolve_dsn() -> str | None:
    """Walk the tree up until we find brain/bootstrap.py, then call its
    resolver. Same trick the unit-tier + integration_db conftests use.
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


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def _e2e_db_pool_session():
    """Session-scoped asyncpg pool against a disposable test database.

    Creates a fresh ``poindexter_e2e_<hex>`` database, replays the
    infrastructure init.sql + every migration in
    ``services/migrations/`` (so the seed of ``glad-labs`` from 0115 is
    applied), and yields a pool. Drops the database at session teardown.

    Skips the whole module if no live Postgres DSN is reachable so a
    unit-only CI runner doesn't blow up.
    """
    import asyncpg

    base = _bootstrap_resolve_dsn()
    if not base or base == "postgresql://test:test@localhost/test":
        pytest.skip(
            "No live Postgres DSN configured — niche e2e test requires a reachable DB"
        )

    parsed = urlparse(base)
    admin_dsn = urlunparse(parsed._replace(path="/postgres"))
    test_db_name = f"poindexter_e2e_{secrets.token_hex(6)}"
    test_dsn = urlunparse(parsed._replace(path=f"/{test_db_name}"))

    admin = await asyncpg.connect(admin_dsn)
    try:
        await admin.execute(f"DROP DATABASE IF EXISTS {test_db_name}")
        await admin.execute(f"CREATE DATABASE {test_db_name}")
    finally:
        await admin.close()

    fresh = await asyncpg.connect(test_dsn)
    try:
        await fresh.execute("CREATE EXTENSION IF NOT EXISTS vector")
        await fresh.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
        for p in Path(__file__).resolve().parents:
            init_sql = p / "infrastructure" / "local-db" / "init.sql"
            if init_sql.is_file():
                try:
                    await fresh.execute(init_sql.read_text(encoding="utf-8"))
                except Exception:
                    pass
                break
    finally:
        await fresh.close()

    pool = await asyncpg.create_pool(test_dsn, min_size=1, max_size=4)
    try:
        from services.migrations import run_migrations

        class _StubService:
            def __init__(self, pool):
                self.pool = pool

        ok = await run_migrations(_StubService(pool))
        if not ok:
            pytest.fail("Migrations failed against the niche e2e test DB")

        try:
            yield pool
        finally:
            await pool.close()
    finally:
        admin = await asyncpg.connect(admin_dsn)
        try:
            await admin.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = $1 AND pid <> pg_backend_pid()",
                test_db_name,
            )
            await admin.execute(f"DROP DATABASE IF EXISTS {test_db_name}")
        finally:
            await admin.close()


@pytest_asyncio.fixture(loop_scope="session")
async def db_pool(_e2e_db_pool_session):
    """Per-test wrapper that cleans up content_tasks between tests.

    Niches are NOT truncated — the seeded ``glad-labs`` niche from
    migration 0115 must persist for Test 1 even if it runs second, and
    Test 2 creates its own ad-hoc niche so cross-test slug collision
    isn't a concern (each test uses a unique slug).
    """
    try:
        yield _e2e_db_pool_session
    finally:
        async with _e2e_db_pool_session.acquire() as conn:
            try:
                # Wipe content_tasks the test inserted so re-runs in the
                # same session don't accumulate. niche_slug IS NOT NULL
                # is the marker that distinguishes our test rows from
                # any rows the migrations themselves may have seeded.
                await conn.execute(
                    "DELETE FROM content_tasks WHERE niche_slug IS NOT NULL"
                )
            except Exception:
                pass


@pytest.fixture(autouse=True)
def _clear_goal_vec_cache():
    """``services.topic_ranking._GOAL_VEC_CACHE`` is module-level and
    persists for the process lifetime. Clear between tests so the second
    test doesn't inherit the first's monkeypatched fake vectors.
    """
    from services.topic_ranking import _GOAL_VEC_CACHE

    _GOAL_VEC_CACHE.clear()
    yield
    _GOAL_VEC_CACHE.clear()


# ---------------------------------------------------------------------------
# Test 1 — full sweep against the seeded glad-labs niche
# ---------------------------------------------------------------------------


async def test_glad_labs_sweep_produces_a_batch(db_pool, monkeypatch):
    """Run a sweep against the seeded glad-labs niche and assert it
    produces an open batch with at least one candidate.

    The sweep would normally call real Ollama for embeddings + LLM
    scoring — both are monkeypatched to deterministic fakes so the test
    is hermetic. ``InternalRagSource.generate`` is also faked so the
    test doesn't depend on real embedding rows existing in the test DB.
    """
    nsvc = NicheService(db_pool)
    n = await nsvc.get_by_slug("glad-labs")
    assert n is not None, "glad-labs niche should be seeded by migration 0115"

    # Mock the internal source so we don't need real embeddings rows.
    async def fake_internal_generate(self, **kwargs):
        return [
            InternalCandidate(
                source_kind="claude_session",
                primary_ref=f"glad-{i}",
                distilled_topic=f"Glad Labs Topic {i}",
                distilled_angle=f"Angle {i}",
            )
            for i in range(5)
        ]

    monkeypatch.setattr(
        "services.internal_rag_source.InternalRagSource.generate",
        fake_internal_generate,
    )

    # Hermetic embedding + LLM scoring — patch the source module so the
    # lazy imports inside TopicBatchService pick up the fakes.
    async def fake_embed_text(text):
        return [0.1] * 768

    monkeypatch.setattr("services.topic_ranking.embed_text", fake_embed_text)
    monkeypatch.setattr(
        "services.topic_ranking._embed_text_cached", fake_embed_text,
    )

    async def fake_llm_score(candidates, weights, *, model=None):
        result = {}
        for idx, c in enumerate(candidates):
            c.llm_score = 80 - idx * 5
            c.score_breakdown = {}
            result[c.id] = c
        return result

    monkeypatch.setattr("services.topic_ranking.llm_final_score", fake_llm_score)

    svc = TopicBatchService(db_pool)
    batch = await svc.run_sweep(niche_id=n.id)

    if batch is None:
        # Floor not elapsed since a prior run; force it via the test by
        # wiping discovery_runs and re-sweeping. This mirrors the plan's
        # "force the floor" instruction.
        async with db_pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM discovery_runs WHERE niche_id = $1", n.id,
            )
        batch = await svc.run_sweep(niche_id=n.id)

    assert batch is not None, "sweep must produce a batch"
    assert batch.status == "open"
    assert batch.candidate_count >= 1


# ---------------------------------------------------------------------------
# Test 2 — resolve advances winner into content_tasks
# ---------------------------------------------------------------------------


async def test_resolve_advances_to_content_task(db_pool, monkeypatch):
    """End-to-end pick → rank → resolve → content_tasks insert.

    Seeds an isolated niche + open batch + 2 candidates directly via
    SQL (avoids re-running the discovery flow), ranks them, then calls
    ``resolve_batch`` and asserts a ``content_tasks`` row landed with
    the right niche_slug / writer_rag_mode / topic_batch_id provenance.
    """
    nsvc = NicheService(db_pool)
    niche = await nsvc.create(
        slug="e2e-resolve-niche",
        name="E2E Resolve",
        writer_rag_mode="TWO_PASS",
        batch_size=3,
    )

    expires = datetime.now(timezone.utc) + timedelta(days=7)
    cand_ids: list[str] = []
    async with db_pool.acquire() as conn:
        batch_row = await conn.fetchrow(
            "INSERT INTO topic_batches (niche_id, status, expires_at) "
            "VALUES ($1, 'open', $2) RETURNING id",
            niche.id, expires,
        )
        batch_id = batch_row["id"]

        # Two external candidates so resolve picks the rank-1 of the two.
        for i in range(2):
            row = await conn.fetchrow(
                """
                INSERT INTO topic_candidates
                  (batch_id, niche_id, source_name, source_ref, title, summary,
                   score, score_breakdown, rank_in_batch, decay_factor)
                VALUES ($1, $2, 'external', $3, $4, $5, $6, '{}'::jsonb, $7, 1.0)
                RETURNING id
                """,
                batch_id, niche.id, f"e2e-ref-{i}",
                f"E2E Topic {i}", f"E2E summary {i}",
                90 - i, i + 1,
            )
            cand_ids.append(str(row["id"]))

    svc = TopicBatchService(db_pool)
    # Operator picks candidate 0 as the winner.
    await svc.rank_batch(batch_id=batch_id, ordered_candidate_ids=cand_ids)

    # Resolve calls _handoff_to_pipeline (real implementation, not mocked)
    # which inserts the content_tasks row — the assertion target.
    await svc.resolve_batch(batch_id=batch_id)

    async with db_pool.acquire() as conn:
        task_row = await conn.fetchrow(
            "SELECT * FROM content_tasks WHERE topic_batch_id = $1",
            batch_id,
        )
        batch_after = await conn.fetchrow(
            "SELECT * FROM topic_batches WHERE id = $1", batch_id,
        )

    assert task_row is not None, "resolve_batch must insert a content_tasks row"
    assert task_row["niche_slug"] == "e2e-resolve-niche"
    assert task_row["writer_rag_mode"] == "TWO_PASS"
    assert task_row["topic_batch_id"] == batch_id
    assert task_row["topic"] == "E2E Topic 0"
    assert task_row["status"] == "pending"

    # Batch was marked resolved + picked candidate recorded.
    assert batch_after["status"] == "resolved"
    assert str(batch_after["picked_candidate_id"]) == cand_ids[0]
    assert batch_after["picked_candidate_kind"] == "external"
