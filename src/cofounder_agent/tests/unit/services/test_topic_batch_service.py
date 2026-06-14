"""Tests for TopicBatchService — orchestrates discovery → rank → batch → gate.

Roundtrips against the real Postgres test DB via the ``db_pool`` fixture
defined in ``tests/unit/conftest.py``. Skipped automatically when no live
Postgres DSN is reachable.
"""

from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from services.internal_rag_source import InternalCandidate
from services.niche_service import Niche, NicheGoal, NicheService, NicheSource
from services.site_config import SiteConfig
from services.topic_batch_service import CandidateView, TopicBatchService

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
    async def fake_embed_text(text, *, site_config=None):
        return [0.1] * 768

    monkeypatch.setattr("services.topic_ranking.embed_text", fake_embed_text)
    monkeypatch.setattr(
        "services.topic_ranking._embed_text_cached", fake_embed_text,
    )

    async def fake_llm_score(candidates, weights, *, model=None, site_config=None):
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

    svc = TopicBatchService(db_pool, site_config=SiteConfig())
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


async def test_run_sweep_survives_internal_discovery_failure(db_pool, monkeypatch):
    """2026-05-28 content-gen stall regression guard.

    If internal-RAG discovery raises (e.g. a reasoning model returns
    empty JSON and json.loads explodes), the sweep must NOT bail and
    discard the external candidates it already gathered. A batch should
    still form from the external pool.
    """
    nsvc = NicheService(db_pool)
    n = await nsvc.create(
        slug="resilient-sweep", name="Resilient", batch_size=2,
    )
    await nsvc.set_goals(n.id, [NicheGoal("TRAFFIC", 100)])

    # External discovery yields 2 candidates; internal discovery blows up.
    # Titles must be genuinely distinct (no shared content words): run_sweep
    # now runs the dedup pass, and the old "Ext topic 0"/"Ext topic 1" pair
    # was 67% word-overlap → the intra-batch deduper would (correctly)
    # collapse them and this resilience test would see only 1 candidate.
    async def fake_external(self, niche):
        titles = [
            "Local LLM Inference Benchmarks",
            "Postgres Replication Failover",
        ]
        return [
            {"kind": "external", "data": {
                "title": title,
                "summary": f"summary {i}",
                "source_name": "hacker_news",
                "source_ref": f"hn-{i}",
                "source_url": f"https://news.example/{i}",
                "category": "ai",
                "relevance_score": 0.9 - i * 0.1,
            }}
            for i, title in enumerate(titles)
        ]

    async def boom_internal(self, niche):
        raise ValueError("Expecting value: line 1 column 1 (char 0)")

    monkeypatch.setattr(
        "services.topic_batch_service.TopicBatchService._discover_external",
        fake_external,
    )
    monkeypatch.setattr(
        "services.topic_batch_service.TopicBatchService._discover_internal",
        boom_internal,
    )

    async def fake_embed_text(text, *, site_config=None):
        return [0.1] * 768

    monkeypatch.setattr("services.topic_ranking.embed_text", fake_embed_text)
    monkeypatch.setattr(
        "services.topic_ranking._embed_text_cached", fake_embed_text,
    )

    async def fake_llm_score(candidates, weights, *, model=None, site_config=None):
        result = {}
        for idx, c in enumerate(candidates):
            c.llm_score = 80 - idx * 5
            c.score_breakdown = {}
            result[c.id] = c
        return result

    monkeypatch.setattr("services.topic_ranking.llm_final_score", fake_llm_score)

    svc = TopicBatchService(db_pool, site_config=SiteConfig())
    batch = await svc.run_sweep(niche_id=n.id)

    # A batch formed despite the internal failure.
    assert batch is not None
    assert batch.status == "open"
    assert batch.candidate_count == 2

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
            "SELECT * FROM discovery_runs WHERE niche_id = $1 "
            "ORDER BY started_at DESC LIMIT 1",
            n.id,
        )
    # External candidates survived; internal contributed nothing.
    assert external_count == 2
    assert internal_count == 0
    # The run completed successfully (no error recorded) — internal failure
    # was swallowed, not propagated.
    assert run_row is not None
    assert run_row["batch_id"] == batch.id
    assert run_row["error"] is None


async def test_run_sweep_dedupes_duplicate_candidates(db_pool, monkeypatch):
    """Regression guard: the niche-batch sweep must drop duplicate
    candidates before writing them to a batch.

    ``TopicBatchService`` replaced ``topic_proposal_service`` but never
    carried over the dedup pass the legacy ``TopicDiscovery`` path runs.
    Internal RAG routinely distills the SAME topic from two different
    source rows — identical ``distilled_topic``, distinct ``primary_ref``
    — so the pair survives the dict-keyed pre-rank as two separate ids and
    both land in the batch. In prod, "operator surface unreachability"
    showed up in a single batch x3 this way. ``run_sweep`` now runs
    ``get_deduplicator().mark_duplicates()`` so the copies collapse to one.
    """
    nsvc = NicheService(db_pool)
    n = await nsvc.create(slug="dedup-sweep", name="Dedup", batch_size=5)
    await nsvc.set_goals(n.id, [NicheGoal("TRAFFIC", 100)])
    await nsvc.set_sources(n.id, [
        NicheSource("internal_rag", enabled=True, weight_pct=100),
    ])

    # Two candidates share distilled_topic but carry DISTINCT primary_refs
    # (the prod shape) plus two genuinely distinct topics. Titles are
    # chosen so word-overlap dedup flags ONLY the exact pair, never the
    # distinct ones (no shared content words across the three topics).
    async def fake_internal_generate(self, **kwargs):
        return [
            InternalCandidate(
                source_kind="claude_session",
                primary_ref="sess-A",
                distilled_topic="Operator Surface Unreachability",
                distilled_angle="why the gauge flatlines",
            ),
            InternalCandidate(
                source_kind="claude_session",
                primary_ref="sess-B",  # different ref, SAME topic
                distilled_topic="Operator Surface Unreachability",
                distilled_angle="duplicate distilled from a second session",
            ),
            InternalCandidate(
                source_kind="brain_knowledge",
                primary_ref="kb-1",
                distilled_topic="Postgres Vacuum Tuning Guide",
                distilled_angle="autovacuum thresholds",
            ),
            InternalCandidate(
                source_kind="decision_log",
                primary_ref="dec-1",
                distilled_topic="Zero Trust Network Segmentation",
                distilled_angle="east-west traffic controls",
            ),
        ]

    monkeypatch.setattr(
        "services.internal_rag_source.InternalRagSource.generate",
        fake_internal_generate,
    )

    async def fake_embed_text(text, *, site_config=None):
        return [0.1] * 768

    monkeypatch.setattr("services.topic_ranking.embed_text", fake_embed_text)
    monkeypatch.setattr(
        "services.topic_ranking._embed_text_cached", fake_embed_text,
    )

    async def fake_llm_score(candidates, weights, *, model=None, site_config=None):
        result = {}
        for idx, c in enumerate(candidates):
            c.llm_score = 80 - idx * 5
            c.score_breakdown = {}
            result[c.id] = c
        return result

    monkeypatch.setattr("services.topic_ranking.llm_final_score", fake_llm_score)

    svc = TopicBatchService(db_pool, site_config=SiteConfig())
    batch = await svc.run_sweep(niche_id=n.id)

    assert batch is not None
    view = await svc.show_batch(batch_id=batch.id)
    titles = [c.title for c in view.candidates]
    # The duplicate pair collapsed to a single candidate.
    assert titles.count("Operator Surface Unreachability") == 1
    # Three distinct topics survive (4 generated − 1 duplicate).
    assert len(titles) == 3
    # No duplicate titles anywhere in the persisted batch.
    assert len(set(titles)) == len(titles)


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

    async def fake_embed_text(text, *, site_config=None):
        return [0.1] * 768

    monkeypatch.setattr("services.topic_ranking.embed_text", fake_embed_text)
    monkeypatch.setattr(
        "services.topic_ranking._embed_text_cached", fake_embed_text,
    )

    async def fake_llm_score(candidates, weights, *, model=None, site_config=None):
        result = {}
        for idx, c in enumerate(candidates):
            c.llm_score = 50 - idx
            c.score_breakdown = {}
            result[c.id] = c
        return result

    monkeypatch.setattr("services.topic_ranking.llm_final_score", fake_llm_score)

    svc = TopicBatchService(db_pool, site_config=SiteConfig())
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


async def test_run_sweep_suppresses_empty_batch_when_nothing_ranks(
    db_pool, monkeypatch,
):
    """Empty-batch wedge guard (2026-06-11 incident class).

    If discovery runs but ranking yields nothing usable (every source
    dry, all deduped, or the LLM final-scorer returns an empty dict),
    ``run_sweep`` must NOT persist an empty ``open`` batch. A
    candidate-less open batch can never be resolved, yet
    ``_open_batch_exists`` would then short-circuit every future sweep
    for the niche — a silent, multi-day content stall. Expect: returns
    None, leaves zero ``topic_batches`` rows, and records the suppressed
    run on ``discovery_runs`` for observability.
    """
    nsvc = NicheService(db_pool)
    n = await nsvc.create(
        slug="empty-batch-guard", name="EmptyGuard", batch_size=3,
    )
    await nsvc.set_goals(n.id, [NicheGoal("TRAFFIC", 100)])
    await nsvc.set_sources(n.id, [
        NicheSource("internal_rag", enabled=True, weight_pct=100),
    ])

    async def fake_internal_generate(self, **kwargs):
        # Discovery DOES find candidates this sweep …
        return [
            InternalCandidate(
                source_kind="claude_session",
                primary_ref=f"sess-{i}",
                distilled_topic=f"Topic {i}",
                distilled_angle=f"Angle {i}",
            )
            for i in range(4)
        ]

    monkeypatch.setattr(
        "services.internal_rag_source.InternalRagSource.generate",
        fake_internal_generate,
    )

    async def fake_embed_text(text, *, site_config=None):
        return [0.1] * 768

    monkeypatch.setattr("services.topic_ranking.embed_text", fake_embed_text)
    monkeypatch.setattr(
        "services.topic_ranking._embed_text_cached", fake_embed_text,
    )

    # … but the LLM final-scorer returns nothing usable → ranked == [].
    async def empty_llm_score(candidates, weights, *, model=None, site_config=None):
        return {}

    monkeypatch.setattr("services.topic_ranking.llm_final_score", empty_llm_score)

    svc = TopicBatchService(db_pool, site_config=SiteConfig())
    result = await svc.run_sweep(niche_id=n.id)

    # Guard fired — no batch object handed back to the caller.
    assert result is None

    async with db_pool.acquire() as conn:
        open_batches = await conn.fetchval(
            "SELECT count(*) FROM topic_batches WHERE niche_id = $1", n.id,
        )
        run_row = await conn.fetchrow(
            "SELECT * FROM discovery_runs WHERE niche_id = $1 "
            "ORDER BY started_at DESC LIMIT 1",
            n.id,
        )

    # No empty batch persisted → the next sweep isn't wedged.
    assert open_batches == 0
    # The suppressed run is still recorded (no batch, with a reason).
    assert run_row is not None
    assert run_row["batch_id"] is None
    assert run_row["finished_at"] is not None
    assert run_row["error"] is not None
    assert "empty batch suppressed" in run_row["error"]


# ---------------------------------------------------------------------------
# Operator-interaction tests (Task 7)
# ---------------------------------------------------------------------------
#
# These tests don't exercise run_sweep — they seed a batch + candidates
# directly via SQL so each test isolates the operator method under
# test (show / rank / edit / resolve / reject). The seed helper returns
# (niche, batch_id, ext_ids, int_ids).


async def _seed_batch_with_mixed_candidates(
    db_pool, *, slug: str, n_external: int = 2, n_internal: int = 3,
):
    """Insert a niche + an open batch + N external + M internal candidates.

    Returns (niche, batch_id, [external candidate ids], [internal candidate ids]).

    Scores are assigned descending starting at 90 so an unranked
    show_batch sort-by-effective-score is deterministic.
    """
    nsvc = NicheService(db_pool)
    niche = await nsvc.create(slug=slug, name=slug.title(), batch_size=5)
    await nsvc.set_goals(niche.id, [NicheGoal("TRAFFIC", 100)])
    await nsvc.set_sources(
        niche.id, [NicheSource("internal_rag", enabled=True, weight_pct=100)],
    )

    expires = datetime.now(timezone.utc) + timedelta(days=7)
    ext_ids: list[str] = []
    int_ids: list[str] = []
    async with db_pool.acquire() as conn:
        batch_row = await conn.fetchrow(
            "INSERT INTO topic_batches (niche_id, status, expires_at) "
            "VALUES ($1, 'open', $2) RETURNING id",
            niche.id, expires,
        )
        batch_id = batch_row["id"]

        rank = 0
        # External candidates first.
        for i in range(n_external):
            rank += 1
            row = await conn.fetchrow(
                """
                INSERT INTO topic_candidates
                  (batch_id, niche_id, source_name, source_ref, title, summary,
                   score, score_breakdown, rank_in_batch, decay_factor)
                VALUES ($1, $2, 'external', $3, $4, $5, $6, '{}'::jsonb, $7, 1.0)
                RETURNING id
                """,
                batch_id, niche.id, f"ext-ref-{i}",
                f"External Topic {i}", f"External summary {i}",
                90 - rank, rank,
            )
            ext_ids.append(str(row["id"]))

        # Internal candidates next.
        for i in range(n_internal):
            rank += 1
            row = await conn.fetchrow(
                """
                INSERT INTO internal_topic_candidates
                  (batch_id, niche_id, source_kind, primary_ref,
                   supporting_refs, distilled_topic, distilled_angle,
                   score, score_breakdown, rank_in_batch, decay_factor)
                VALUES ($1, $2, 'claude_session', $3, '[]'::jsonb, $4, $5,
                        $6, '{}'::jsonb, $7, 1.0)
                RETURNING id
                """,
                batch_id, niche.id, f"int-ref-{i}",
                f"Internal Topic {i}", f"Internal angle {i}",
                90 - rank, rank,
            )
            int_ids.append(str(row["id"]))

    return niche, batch_id, ext_ids, int_ids


async def test_show_batch_returns_unified_ranked_view(db_pool):
    """show_batch merges external + internal candidates into a single
    list ordered by effective_score (= score * decay_factor) desc."""
    niche, batch_id, ext_ids, int_ids = await _seed_batch_with_mixed_candidates(
        db_pool, slug="show-batch-niche", n_external=2, n_internal=3,
    )

    svc = TopicBatchService(db_pool, site_config=SiteConfig())
    view = await svc.show_batch(batch_id=batch_id)

    assert view.id == batch_id
    assert view.status == "open"
    assert view.picked_candidate_id is None
    assert len(view.candidates) == 5
    # Mixed kinds present.
    kinds = {c.kind for c in view.candidates}
    assert kinds == {"external", "internal"}
    # Sorted by effective_score desc.
    scores = [c.effective_score for c in view.candidates]
    assert scores == sorted(scores, reverse=True)
    # Every candidate has an effective_score == score * decay_factor.
    for c in view.candidates:
        assert c.effective_score == pytest.approx(c.score * c.decay_factor)


async def test_rank_batch_records_operator_order(db_pool):
    """rank_batch should set operator_rank by 1-based position in the
    provided list, transparently spanning both candidate tables."""
    niche, batch_id, ext_ids, int_ids = await _seed_batch_with_mixed_candidates(
        db_pool, slug="rank-batch-niche", n_external=2, n_internal=3,
    )
    # Interleave external + internal ids so the test exercises the
    # external-first-then-internal fallback.
    ordered = [int_ids[2], ext_ids[0], int_ids[0], ext_ids[1], int_ids[1]]

    svc = TopicBatchService(db_pool, site_config=SiteConfig())
    await svc.rank_batch(batch_id=batch_id, ordered_candidate_ids=ordered)

    view = await svc.show_batch(batch_id=batch_id)
    ranked = sorted(
        [c for c in view.candidates if c.operator_rank is not None],
        key=lambda c: c.operator_rank,
    )
    assert [c.id for c in ranked] == ordered


async def test_edit_winner_sets_operator_edit_fields(db_pool):
    """edit_winner updates the operator_edited_topic / angle on the
    rank-1 candidate, regardless of which table it lives in."""
    niche, batch_id, ext_ids, int_ids = await _seed_batch_with_mixed_candidates(
        db_pool, slug="edit-winner-niche", n_external=2, n_internal=3,
    )
    # Make an INTERNAL candidate the winner so we exercise the fallback.
    ordered = [int_ids[0], ext_ids[0], int_ids[1], ext_ids[1], int_ids[2]]
    svc = TopicBatchService(db_pool, site_config=SiteConfig())
    await svc.rank_batch(batch_id=batch_id, ordered_candidate_ids=ordered)

    await svc.edit_winner(
        batch_id=batch_id, topic="Operator-Edited Title", angle="Operator angle",
    )

    view = await svc.show_batch(batch_id=batch_id)
    winner = next(c for c in view.candidates if c.operator_rank == 1)
    assert winner.id == int_ids[0]
    assert winner.operator_edited_topic == "Operator-Edited Title"
    assert winner.operator_edited_angle == "Operator angle"


async def test_resolve_batch_advances_winner_and_marks_resolved(db_pool, monkeypatch):
    """resolve_batch hands the winner off to the pipeline + flips
    status to resolved + records picked_candidate_id."""
    niche, batch_id, ext_ids, int_ids = await _seed_batch_with_mixed_candidates(
        db_pool, slug="resolve-batch-niche", n_external=2, n_internal=3,
    )
    ordered = [ext_ids[0], int_ids[0], ext_ids[1], int_ids[1], int_ids[2]]
    svc = TopicBatchService(db_pool, site_config=SiteConfig())
    await svc.rank_batch(batch_id=batch_id, ordered_candidate_ids=ordered)

    handoff_calls: list[tuple[str, str, str]] = []

    async def fake_handoff(self, candidate, niche, handoff_batch_id):
        # CRITICAL: signature carries batch_id explicitly so the
        # content_tasks row's topic_batch_id provenance points at the
        # batch, not the candidate. Plan body had the wrong variable
        # threaded through; this fake captures it so the test asserts
        # we wired the right value.
        handoff_calls.append(
            (candidate.id, niche.slug, str(handoff_batch_id)),
        )

    monkeypatch.setattr(
        "services.topic_batch_service.TopicBatchService._handoff_to_pipeline",
        fake_handoff,
    )

    await svc.resolve_batch(batch_id=batch_id)

    view = await svc.show_batch(batch_id=batch_id)
    assert view.status == "resolved"
    assert view.picked_candidate_id is not None
    assert str(view.picked_candidate_id) == ext_ids[0]
    assert len(handoff_calls) == 1
    assert handoff_calls[0] == (ext_ids[0], niche.slug, str(batch_id))


async def test_resolve_batch_raises_when_niche_missing(db_pool, monkeypatch):
    """Defensive guard: if the batch's niche row has vanished between
    show_batch and handoff, resolve_batch must fail loud with a clear
    ValueError rather than crash with a NoneType AttributeError on
    ``niche.slug`` deep inside _handoff_to_pipeline."""
    niche, batch_id, ext_ids, _int_ids = await _seed_batch_with_mixed_candidates(
        db_pool, slug="resolve-missing-niche", n_external=1, n_internal=0,
    )
    svc = TopicBatchService(db_pool, site_config=SiteConfig())
    await svc.rank_batch(batch_id=batch_id, ordered_candidate_ids=[ext_ids[0]])

    # Simulate the niche having disappeared (orphaned batch).
    async def _no_niche(_niche_id):
        return None

    monkeypatch.setattr(svc._niche_svc, "get_by_id", _no_niche)

    with pytest.raises(ValueError, match="unknown niche"):
        await svc.resolve_batch(batch_id=batch_id)

    # The batch must NOT have been flipped to resolved on the failed path.
    async with db_pool.acquire() as conn:
        status = await conn.fetchval(
            "SELECT status FROM topic_batches WHERE id = $1", batch_id,
        )
    assert status == "open"


async def test_reject_batch_marks_expired_and_can_re_discover(db_pool):
    """reject_batch flips the batch to expired + frees up the
    one-open-batch-per-niche slot for a future sweep."""
    niche, batch_id, ext_ids, int_ids = await _seed_batch_with_mixed_candidates(
        db_pool, slug="reject-batch-niche", n_external=2, n_internal=3,
    )
    svc = TopicBatchService(db_pool, site_config=SiteConfig())
    await svc.reject_batch(batch_id=batch_id, reason="none of these")

    view = await svc.show_batch(batch_id=batch_id)
    assert view.status == "expired"

    # One-open-batch-per-niche slot freed: a new open batch row may now
    # be inserted without violating uq_one_open_batch_per_niche.
    async with db_pool.acquire() as conn:
        new_batch_row = await conn.fetchrow(
            "INSERT INTO topic_batches (niche_id, status, expires_at) "
            "VALUES ($1, 'open', NOW() + INTERVAL '7 days') RETURNING id",
            niche.id,
        )
    assert new_batch_row is not None


async def test_list_open_batches_returns_only_open_with_candidates_and_niche(db_pool):
    """list_open_batches surfaces every *open* batch (across niches) with its
    merged candidate view + niche slug/name, and excludes resolved/expired
    batches. Powers the console's GET /api/topics/proposals triage surface."""
    niche_a, batch_a, ext_a, int_a = await _seed_batch_with_mixed_candidates(
        db_pool, slug="list-open-a", n_external=2, n_internal=1,
    )
    _niche_b, batch_b, _ext_b, _int_b = await _seed_batch_with_mixed_candidates(
        db_pool, slug="list-open-b", n_external=1, n_internal=2,
    )

    svc = TopicBatchService(db_pool, site_config=SiteConfig())
    # Reject batch_b → status 'expired' so it must drop out of the open list.
    await svc.reject_batch(batch_id=batch_b, reason="none of these")

    out = await svc.list_open_batches()

    ids = {str(ob.view.id) for ob in out}
    assert str(batch_a) in ids
    assert str(batch_b) not in ids

    ob_a = next(ob for ob in out if str(ob.view.id) == str(batch_a))
    # Niche metadata is resolved + attached for operator display.
    assert ob_a.niche_slug == "list-open-a"
    assert ob_a.niche_name == niche_a.name
    # The merged candidate view rides along (2 external + 1 internal).
    assert ob_a.view.status == "open"
    assert len(ob_a.view.candidates) == 3
    assert {c.kind for c in ob_a.view.candidates} == {"external", "internal"}


# ===========================================================================
# _discover_external — TopicSource plugin dispatch (Task 6 follow-up)
# ===========================================================================


class _StubTopicSource:
    """Stub implementing the ``plugins.topic_source.TopicSource`` Protocol.

    Captures the ``(pool, config)`` it was called with so tests can assert
    the niche context propagated correctly. Returns a deterministic list
    of ``DiscoveredTopic`` (or raises if ``error`` is set).
    """

    def __init__(self, name, topics=None, error=None):
        self.name = name
        self._topics = topics or []
        self._error = error
        self.calls = []

    async def extract(self, pool, config):
        self.calls.append({"pool": pool, "config": config})
        if self._error is not None:
            raise self._error
        return list(self._topics)


async def test_discover_external_dispatches_registered_plugins(db_pool, monkeypatch):
    """Each enabled non-internal_rag source matches a registered plugin
    by name; results aggregate into the {kind, data} shape consumed by
    _embed_and_pre_rank."""
    from plugins.topic_source import DiscoveredTopic

    nsvc = NicheService(db_pool)
    n = await nsvc.create(slug="ext-niche-dispatch", name="ExtDispatch")
    await nsvc.set_sources(n.id, [
        NicheSource("hackernews", enabled=True, weight_pct=60),
        NicheSource("devto", enabled=True, weight_pct=40),
        # internal_rag must NOT be invoked by _discover_external.
        NicheSource("internal_rag", enabled=True, weight_pct=0),
    ])

    hn = _StubTopicSource("hackernews", topics=[
        DiscoveredTopic(
            title="Rust 1.80 ships with stable async iterators",
            category="technology",
            source="hackernews",
            source_url="https://news.ycombinator.com/item?id=1",
            relevance_score=4.2,
            description="HN top story",
        ),
    ])
    devto = _StubTopicSource("devto", topics=[
        DiscoveredTopic(
            title="Why I switched from Webpack to Vite",
            category="technology",
            source="devto",
            source_url="https://dev.to/x/y",
            relevance_score=2.5,
            description="Dev.to trending",
        ),
        DiscoveredTopic(
            title="Postgres 17 query plans demystified",
            category="technology",
            source="devto",
            source_url="https://dev.to/a/b",
            relevance_score=3.0,
            description="Dev.to trending",
        ),
    ])

    monkeypatch.setattr(
        "plugins.registry.get_topic_sources",
        lambda: [hn, devto],
    )

    svc = TopicBatchService(db_pool, site_config=SiteConfig())
    out = await svc._discover_external(n)

    assert len(out) == 3
    # All have the expected shape.
    for item in out:
        assert item["kind"] == "external"
        data = item["data"]
        assert {"title", "summary", "source_name", "source_ref"} <= data.keys()

    # Aggregate from BOTH plugins.
    assert {item["data"]["source_name"] for item in out} == {
        "hackernews", "devto",
    }
    titles = {item["data"]["title"] for item in out}
    assert "Rust 1.80 ships with stable async iterators" in titles
    assert "Why I switched from Webpack to Vite" in titles

    # Each plugin's extract() saw the niche context.
    for stub in (hn, devto):
        assert len(stub.calls) == 1
        cfg = stub.calls[0]["config"]
        assert cfg["niche_slug"] == n.slug
        assert cfg["niche_id"] == str(n.id)
        assert "_site_config" in cfg

    # internal_rag must not have been routed through here at all.
    assert "internal_rag" not in {s.name for s in (hn, devto)}


async def test_discover_external_skips_unknown_source_with_warning(
    db_pool, monkeypatch, caplog,
):
    """A niche-source name not present in the registry must log a
    warning and be skipped — never raise."""
    from plugins.topic_source import DiscoveredTopic

    nsvc = NicheService(db_pool)
    n = await nsvc.create(slug="ext-niche-unknown", name="ExtUnknown")
    await nsvc.set_sources(n.id, [
        NicheSource("hackernews", enabled=True, weight_pct=50),
        # Not registered — must be skipped, not crashed on.
        NicheSource("legacy_rss", enabled=True, weight_pct=50),
    ])

    hn = _StubTopicSource("hackernews", topics=[
        DiscoveredTopic(
            title="The case for monorepos in 2026",
            category="technology",
            source="hackernews",
            source_url="https://example/1",
        ),
    ])
    monkeypatch.setattr("plugins.registry.get_topic_sources", lambda: [hn])

    svc = TopicBatchService(db_pool, site_config=SiteConfig())
    import logging
    with caplog.at_level(logging.WARNING):
        out = await svc._discover_external(n)

    # Got hn's one topic; legacy_rss silently skipped (warned, not raised).
    assert len(out) == 1
    assert out[0]["data"]["source_name"] == "hackernews"

    warned = [
        r for r in caplog.records
        if r.levelno >= logging.WARNING and "legacy_rss" in r.getMessage()
    ]
    assert warned, "expected a warning for the unregistered source"


async def test_discover_external_isolates_per_source_failures(
    db_pool, monkeypatch,
):
    """A plugin raising must not kill the sweep — other plugins still
    contribute their topics."""
    from plugins.topic_source import DiscoveredTopic

    nsvc = NicheService(db_pool)
    n = await nsvc.create(slug="ext-niche-isolate", name="ExtIsolate")
    await nsvc.set_sources(n.id, [
        NicheSource("hackernews", enabled=True, weight_pct=50),
        NicheSource("devto", enabled=True, weight_pct=50),
    ])

    bad = _StubTopicSource("hackernews", error=RuntimeError("boom"))
    good = _StubTopicSource("devto", topics=[
        DiscoveredTopic(
            title="Goroutines vs async/await",
            category="technology",
            source="devto",
            source_url="https://dev.to/g",
        ),
    ])
    monkeypatch.setattr(
        "plugins.registry.get_topic_sources", lambda: [bad, good],
    )

    svc = TopicBatchService(db_pool, site_config=SiteConfig())
    out = await svc._discover_external(n)

    assert len(out) == 1
    assert out[0]["data"]["source_name"] == "devto"


async def test_discover_external_disabled_sources_skipped(
    db_pool, monkeypatch,
):
    """Disabled niche-source rows must not invoke their plugin."""
    from plugins.topic_source import DiscoveredTopic

    nsvc = NicheService(db_pool)
    n = await nsvc.create(slug="ext-niche-disabled", name="ExtDisabled")
    await nsvc.set_sources(n.id, [
        NicheSource("hackernews", enabled=False, weight_pct=50),
        NicheSource("devto", enabled=True, weight_pct=50),
    ])

    hn = _StubTopicSource("hackernews", topics=[
        DiscoveredTopic(
            title="should not appear",
            category="technology",
            source="hackernews",
        ),
    ])
    devto = _StubTopicSource("devto", topics=[
        DiscoveredTopic(
            title="appears",
            category="technology",
            source="devto",
        ),
    ])
    monkeypatch.setattr(
        "plugins.registry.get_topic_sources", lambda: [hn, devto],
    )

    svc = TopicBatchService(db_pool, site_config=SiteConfig())
    out = await svc._discover_external(n)

    assert len(out) == 1
    assert out[0]["data"]["title"] == "appears"
    assert hn.calls == [], "disabled source should never be invoked"


# ===========================================================================
# _handoff_to_pipeline — #188/#341 regression guard
# ===========================================================================


def _make_mock_pool(execute_side_effect=None, *,
                    niche_template_slug=None,
                    app_setting_template_slug="canonical_blog"):
    """Lightweight pool that supports ``async with pool.acquire()`` +
    ``async with conn.transaction()`` + ``await conn.execute(...)``.

    Mirrors the helpers in ``test_tasks_db.py`` and
    ``test_topic_discovery.py`` so all #188 INSERT-target guard tests
    share a uniform shape.

    Also wires ``conn.fetchval`` / ``conn.fetchrow`` for the
    ``template_slug_resolver`` lookups that ``_handoff_to_pipeline``
    now makes. Defaults to the app_settings tier returning
    ``'canonical_blog'`` so the resolver succeeds without explicit
    test setup — tests that want a different value override the
    kwargs.
    """
    conn = MagicMock()
    if execute_side_effect:
        conn.execute = AsyncMock(side_effect=execute_side_effect)
    else:
        conn.execute = AsyncMock()

    async def _fetchval(sql, *args, **kwargs):
        if "FROM niches" in sql:
            return niche_template_slug
        return None

    async def _fetchrow(sql, *args, **kwargs):
        if "FROM app_settings" in sql:
            if app_setting_template_slug is None:
                return None
            return {"value": app_setting_template_slug}
        return None

    conn.fetchval = AsyncMock(side_effect=_fetchval)
    conn.fetchrow = AsyncMock(side_effect=_fetchrow)

    @asynccontextmanager
    async def _tx_inner():
        yield

    conn.transaction = MagicMock(side_effect=lambda *a, **kw: _tx_inner())

    pool = MagicMock()

    @asynccontextmanager
    async def _acquire():
        yield conn

    pool.acquire = _acquire
    return pool, conn


def _make_niche(slug: str = "test-niche") -> Niche:
    return Niche(
        id=uuid4(),
        slug=slug,
        name="Test",
        active=True,
        target_audience_tags=[],
        writer_prompt_override=None,
        batch_size=5,
        discovery_cadence_minute_floor=60,
    )


def _make_candidate(title: str = "Why X beats Y") -> CandidateView:
    return CandidateView(
        id="cand-1",
        kind="external",
        title=title,
        summary="Short summary",
        score=0.8,
        decay_factor=1.0,
        effective_score=0.8,
        rank_in_batch=1,
        operator_rank=1,
        operator_edited_topic=None,
        operator_edited_angle=None,
        score_breakdown={},
    )


@pytest.mark.unit
class TestHandoffToPipelineSQL:
    """#341 regression guard — ``_handoff_to_pipeline`` must INSERT into
    ``pipeline_tasks`` + ``pipeline_versions`` (the underlying tables),
    never into the ``content_tasks`` view (which raises
    ``ObjectNotInPrerequisiteStateError`` in production).
    """

    async def test_writes_to_pipeline_tables_not_view(self):
        seen: list[str] = []

        async def _capture(sql, *args, **kwargs):
            seen.append(sql)
            return "INSERT 0 1"

        pool, _conn = _make_mock_pool(execute_side_effect=_capture)
        svc = TopicBatchService(pool, site_config=SiteConfig())

        await svc._handoff_to_pipeline(
            winner=_make_candidate(),
            niche=_make_niche(),
            batch_id=uuid4(),
        )

        joined = "\n".join(seen)
        assert "pipeline_tasks" in joined
        assert "pipeline_versions" in joined
        assert "INSERT INTO content_tasks" not in joined

    async def test_emits_two_inserts_per_handoff(self):
        # One INSERT into pipeline_tasks + one into pipeline_versions.
        pool, conn = _make_mock_pool()
        svc = TopicBatchService(pool, site_config=SiteConfig())

        await svc._handoff_to_pipeline(
            winner=_make_candidate(),
            niche=_make_niche(),
            batch_id=uuid4(),
        )

        assert conn.execute.await_count == 2

    async def test_uses_operator_edits_when_present(self):
        captured_args: list[tuple] = []

        async def _capture(sql, *args, **kwargs):
            captured_args.append((sql, args))
            return "INSERT 0 1"

        pool, _conn = _make_mock_pool(execute_side_effect=_capture)
        svc = TopicBatchService(pool, site_config=SiteConfig())

        winner = CandidateView(
            id="cand-1",
            kind="external",
            title="Original Title",
            summary="Original summary",
            score=0.8,
            decay_factor=1.0,
            effective_score=0.8,
            rank_in_batch=1,
            operator_rank=1,
            operator_edited_topic="Operator-Edited Topic",
            operator_edited_angle="Operator angle",
            score_breakdown={},
        )

        await svc._handoff_to_pipeline(
            winner=winner, niche=_make_niche(), batch_id=uuid4(),
        )

        # Topic on pipeline_tasks insert must be the operator edit, not
        # the original candidate title.
        pipeline_call = next(
            (sql, args) for sql, args in captured_args
            if "pipeline_tasks" in sql
        )
        _, args = pipeline_call
        assert "Operator-Edited Topic" in args


# ===========================================================================
# _handoff_to_pipeline — template_slug resolution (jank-audit finding #3)
# ===========================================================================


@pytest.mark.unit
class TestHandoffTemplateSlugResolution:
    """The niche topic-batch path was inserting ``pipeline_tasks`` rows
    without ``template_slug``, leaving the column NULL and causing
    ``content_router_service`` to fail every task per
    ``feedback_no_silent_defaults``. The fix: resolve the slug at
    insert time via ``services.template_slug_resolver``.

    Resolution priority (verified individually below):
      1. niches.default_template_slug for this niche
      2. app_settings.default_template_slug (process-wide fallback)
      3. raise — no silent default
    """

    async def test_pipeline_insert_includes_template_slug_column(self):
        """The INSERT statement must mention the column name +
        carry the resolved slug in args. Prior to the fix the column
        was entirely absent from the INSERT (the bug).
        """
        captured: list[tuple[str, tuple]] = []

        async def _capture(sql, *args, **kwargs):
            captured.append((sql, args))
            return "INSERT 0 1"

        pool, _ = _make_mock_pool(execute_side_effect=_capture)
        svc = TopicBatchService(pool, site_config=SiteConfig())

        await svc._handoff_to_pipeline(
            winner=_make_candidate(),
            niche=_make_niche("glad-labs"),
            batch_id=uuid4(),
        )

        pipeline_sql, pipeline_args = next(
            (sql, args) for sql, args in captured if "pipeline_tasks" in sql
        )
        assert "template_slug" in pipeline_sql
        # default app_setting slug from the mock pool is 'canonical_blog'.
        assert "canonical_blog" in pipeline_args

    async def test_niche_default_wins_over_app_setting(self):
        """When the niche row carries its own
        default_template_slug, it must beat the app_setting fallback
        — that's the structured DB seam per
        ``feedback_filter_on_seams_not_slugs``.
        """
        captured: list[tuple[str, tuple]] = []

        async def _capture(sql, *args, **kwargs):
            captured.append((sql, args))
            return "INSERT 0 1"

        pool, _ = _make_mock_pool(
            execute_side_effect=_capture,
            niche_template_slug="dev_diary",
            app_setting_template_slug="canonical_blog",
        )
        svc = TopicBatchService(pool, site_config=SiteConfig())

        await svc._handoff_to_pipeline(
            winner=_make_candidate(),
            niche=_make_niche("special-niche"),
            batch_id=uuid4(),
        )

        _, pipeline_args = next(
            (sql, args) for sql, args in captured if "pipeline_tasks" in sql
        )
        # Niche default beat the app_setting default.
        assert "dev_diary" in pipeline_args
        assert "canonical_blog" not in pipeline_args

    async def test_no_resolvable_slug_raises_not_silent_null(self):
        """When neither tier has a value, the handoff raises rather
        than writing a NULL row. Per ``feedback_no_silent_defaults``:
        let the operator see the misconfig instead of a queue of
        pre-failed tasks (which was finding #3 of the jank audit).
        """
        from services.template_slug_resolver import TemplateSlugUnresolvable

        captured: list[str] = []

        async def _capture(sql, *args, **kwargs):
            captured.append(sql)
            return "INSERT 0 1"

        pool, _ = _make_mock_pool(
            execute_side_effect=_capture,
            niche_template_slug=None,
            app_setting_template_slug=None,
        )
        svc = TopicBatchService(pool, site_config=SiteConfig())

        with pytest.raises(TemplateSlugUnresolvable):
            await svc._handoff_to_pipeline(
                winner=_make_candidate(),
                niche=_make_niche("glad-labs"),
                batch_id=uuid4(),
            )

        # No INSERT into pipeline_tasks happened — we failed before
        # the write.
        assert not any("INSERT INTO pipeline_tasks" in s for s in captured)
