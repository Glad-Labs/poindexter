"""Tests for TopicBatchService — orchestrates discovery → rank → batch → gate.

Spec: docs/superpowers/specs/2026-04-30-rag-pivot-niche-discovery-design.md
Plan: docs/superpowers/plans/2026-04-30-rag-pivot-niche-discovery.md (Task 6)

Roundtrips against the real Postgres test DB via the ``db_pool`` fixture
defined in ``tests/unit/conftest.py``. Skipped automatically when no live
Postgres DSN is reachable.
"""

import pytest

from services.niche_service import NicheService, NicheGoal, NicheSource
from services.internal_rag_source import InternalCandidate
from services.topic_batch_service import TopicBatchService

pytestmark = pytest.mark.asyncio(loop_scope="session")


@pytest.fixture(autouse=True)
def _clear_goal_vec_cache():
    """``services.topic_ranking._GOAL_VEC_CACHE`` is module-level and lives
    for the process lifetime. If we don't clear it between tests, the
    second test inherits the first test's monkeypatched-fake vectors —
    or worse, a real production vector that bled in from another module.
    """
    from services.topic_ranking import _GOAL_VEC_CACHE
    _GOAL_VEC_CACHE.clear()
    yield
    _GOAL_VEC_CACHE.clear()


async def test_run_sweep_creates_open_batch_with_candidates(db_pool, monkeypatch):
    """End-to-end happy-path:

    Seed a niche with batch_size=3 and a single ``internal_rag`` source,
    monkeypatch the source to yield 5 fake candidates, monkeypatch the
    embedding + LLM scorer, expect an ``open`` batch with 3 ranked
    candidates persisted across the candidate tables.
    """
    nsvc = NicheService(db_pool)
    n = await nsvc.create(slug="test-niche-batch-svc", name="Test", batch_size=3)
    await nsvc.set_goals(n.id, [
        NicheGoal("TRAFFIC", 50),
        NicheGoal("EDUCATION", 50),
    ])
    await nsvc.set_sources(n.id, [
        NicheSource("internal_rag", enabled=True, weight_pct=100),
    ])

    # Mock the internal source generator → 5 fake candidates.
    async def fake_internal_generate(self, **kwargs):
        return [
            InternalCandidate(
                source_kind="claude_session",
                primary_ref=f"sess-{i}",
                distilled_topic=f"Topic {i}",
                distilled_angle=f"Angle {i}",
            )
            for i in range(5)
        ]

    monkeypatch.setattr(
        "services.internal_rag_source.InternalRagSource.generate",
        fake_internal_generate,
    )

    # Mock the embedding step + LLM final scorer. Patch BOTH the public
    # ``embed_text`` (used for candidate texts via lazy import in
    # TopicBatchService) AND the private ``_embed_text_cached`` (used by
    # ``goal_vector_for`` to embed goal description anchors).
    async def fake_embed_text(text):
        return [0.1] * 768

    monkeypatch.setattr("services.topic_ranking.embed_text", fake_embed_text)
    monkeypatch.setattr(
        "services.topic_ranking._embed_text_cached", fake_embed_text,
    )

    async def fake_llm_score(candidates, weights, *, model=None):
        # Return the same candidates with a descending llm_score so order
        # is deterministic. Use enumerate to mimic the spec: first → 80,
        # then 75, 70, …
        result = {}
        for idx, c in enumerate(candidates):
            c.llm_score = 80 - idx * 5
            c.score_breakdown = {}
            result[c.id] = c
        return result

    monkeypatch.setattr("services.topic_ranking.llm_final_score", fake_llm_score)

    svc = TopicBatchService(db_pool)
    batch = await svc.run_sweep(niche_id=n.id)

    assert batch is not None
    assert batch.status == "open"
    # batch_size=3, 5 generated → top 3 in the batch.
    assert batch.candidate_count == 3

    # Verify rows actually landed in the DB.
    async with db_pool.acquire() as conn:
        external_count = await conn.fetchval(
            "SELECT count(*) FROM topic_candidates WHERE batch_id = $1",
            batch.id,
        )
        internal_count = await conn.fetchval(
            "SELECT count(*) FROM internal_topic_candidates WHERE batch_id = $1",
            batch.id,
        )
        run_row = await conn.fetchrow(
            "SELECT * FROM discovery_runs WHERE niche_id = $1 ORDER BY started_at DESC LIMIT 1",
            n.id,
        )
    assert external_count + internal_count == 3
    # All five candidates came from the internal_rag source → all rows
    # are internal_topic_candidates.
    assert internal_count == 3
    assert external_count == 0
    # discovery_runs row recorded.
    assert run_row is not None
    assert run_row["batch_id"] == batch.id
    assert run_row["finished_at"] is not None
    assert run_row["candidates_generated"] == 5


async def test_only_one_open_batch_per_niche(db_pool, monkeypatch):
    """Second sweep while an open batch exists should be a no-op (return None).

    The ``uq_one_open_batch_per_niche`` partial unique index also enforces
    this at the DB level, but the service short-circuits before insert so
    the operator gets a friendly skip rather than a constraint violation.
    """
    nsvc = NicheService(db_pool)
    n = await nsvc.create(
        slug="solo-batch", name="Solo",
        batch_size=2,
        # Force the floor check to always pass on the second call.
        discovery_cadence_minute_floor=1,
    )
    await nsvc.set_goals(n.id, [
        NicheGoal("TRAFFIC", 100),
    ])
    await nsvc.set_sources(n.id, [
        NicheSource("internal_rag", enabled=True, weight_pct=100),
    ])

    async def fake_internal_generate(self, **kwargs):
        return [
            InternalCandidate(
                source_kind="claude_session",
                primary_ref=f"solo-{i}",
                distilled_topic=f"T{i}",
                distilled_angle=f"A{i}",
            )
            for i in range(3)
        ]

    monkeypatch.setattr(
        "services.internal_rag_source.InternalRagSource.generate",
        fake_internal_generate,
    )

    async def fake_embed_text(text):
        return [0.1] * 768

    monkeypatch.setattr("services.topic_ranking.embed_text", fake_embed_text)
    monkeypatch.setattr(
        "services.topic_ranking._embed_text_cached", fake_embed_text,
    )

    async def fake_llm_score(candidates, weights, *, model=None):
        result = {}
        for idx, c in enumerate(candidates):
            c.llm_score = 50 - idx
            c.score_breakdown = {}
            result[c.id] = c
        return result

    monkeypatch.setattr("services.topic_ranking.llm_final_score", fake_llm_score)

    svc = TopicBatchService(db_pool)
    first = await svc.run_sweep(niche_id=n.id)
    assert first is not None
    assert first.status == "open"

    # Second sweep — open batch already exists → service must return None
    # rather than insert a second open batch.
    second = await svc.run_sweep(niche_id=n.id)
    assert second is None

    async with db_pool.acquire() as conn:
        open_count = await conn.fetchval(
            "SELECT count(*) FROM topic_batches WHERE niche_id = $1 AND status = 'open'",
            n.id,
        )
    assert open_count == 1
