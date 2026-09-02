"""Tests for TopicBatchService — orchestrates discovery → rank → batch → gate.

Roundtrips against the real Postgres test DB via the ``db_pool`` fixture
defined in ``tests/unit/conftest.py``. Skipped automatically when no live
Postgres DSN is reachable.
"""

from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from services.niche_service import Niche, NicheGoal, NicheService, NicheSource
from services.site_config import SiteConfig
from services.topic_batch_service import BatchSnapshot, CandidateView, TopicBatchService

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


@pytest.fixture(autouse=True)
def _no_recent_coverage(monkeypatch):
    """Neutralize the recent-coverage handoff gate for this file's tests.

    The real ``check_recent_coverage`` builds a ``MemoryClient`` (eager DB
    connect + embed calls) — a side channel no unit test should open, and a
    nondeterminism source for the db-backed ones. Gate-specific tests
    re-patch with their own return value / side effect.
    """
    monkeypatch.setattr(
        "services.topic_recent_coverage.check_recent_coverage",
        AsyncMock(return_value=None),
    )


async def test_run_sweep_creates_open_batch_with_candidates(db_pool, monkeypatch):
    """End-to-end happy-path (b2 pool-reader):

    Seed a niche with batch_size=3, deposit 5 internal_rag candidates in
    ``topic_pool`` (as the tap handler does), monkeypatch the embedding +
    LLM scorer, expect an ``open`` batch with 3 ranked candidates
    persisted across the candidate tables — and the 3 winners' pool rows
    flipped to ``batched``.
    """
    nsvc = NicheService(db_pool)
    n = await nsvc.create(slug="test-niche-batch-svc", name="Test", batch_size=3)
    await nsvc.set_goals(n.id, [
        NicheGoal("TRAFFIC", 50),
        NicheGoal("EDUCATION", 50),
    ])

    # Deposit 5 pool candidates the way tap.builtin_topic_source does.
    # Titles are multi-word (so the topic-sanity intake filter keeps them)
    # AND mutually word-disjoint (so the intra-batch dedup pass keeps them —
    # fuzzy matching only skips single-content-word titles).
    from plugins.topic_source import DiscoveredTopic
    from services.topic_pool import insert_pooled_topics

    titles = [
        "Async worker pools explained",
        "Grafana dashboard provisioning",
        "Postgres vacuum tuning",
        "LangGraph checkpoint recovery",
        "Docker compose profiles",
    ]
    async with db_pool.acquire() as conn:
        await insert_pooled_topics(
            conn, niche_id=n.id, source="internal_rag",
            topics=[
                DiscoveredTopic(
                    title=t, category="claude_session",
                    source="internal_rag", description=f"Angle {i}",
                )
                for i, t in enumerate(titles)
            ],
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

    # The 3 batch winners' pool rows flipped to 'batched'; the 2 unpicked
    # rows stay 'pooled' for future sweeps.
    async with db_pool.acquire() as conn:
        statuses = await conn.fetch(
            "SELECT status, count(*) AS c FROM topic_pool "
            "WHERE niche_id = $1 GROUP BY status",
            n.id,
        )
    by_status = {r["status"]: r["c"] for r in statuses}
    assert by_status == {"batched": 3, "pooled": 2}


async def test_run_sweep_mixes_external_and_internal_pool_rows(db_pool, monkeypatch):
    """b2 pool-reader: a sweep over a pool holding both external-source and
    internal_rag rows routes each winner to the right candidate table
    (topic_candidates vs internal_topic_candidates).

    (Replaces the pre-b2 "survives internal discovery failure" regression
    guard — ingestion failures now happen in the tap runner, outside the
    sweep, so that failure mode is structurally impossible here.)
    """
    from plugins.topic_source import DiscoveredTopic
    from services.topic_pool import insert_pooled_topics

    nsvc = NicheService(db_pool)
    n = await nsvc.create(
        slug="resilient-sweep", name="Resilient", batch_size=2,
    )
    await nsvc.set_goals(n.id, [NicheGoal("TRAFFIC", 100)])

    # Titles must be genuinely distinct (no shared content words): run_sweep
    # runs the dedup pass, and a high-word-overlap pair would (correctly)
    # collapse before ranking.
    async with db_pool.acquire() as conn:
        await insert_pooled_topics(
            conn, niche_id=n.id, source="hackernews",
            topics=[
                DiscoveredTopic(
                    title="Local LLM Inference Benchmarks", category="ai",
                    source="hackernews", source_url="https://news.example/1",
                    relevance_score=0.9, description="summary 1",
                ),
            ],
        )
        await insert_pooled_topics(
            conn, niche_id=n.id, source="internal_rag",
            topics=[
                DiscoveredTopic(
                    title="Postgres Replication Failover",
                    category="claude_session", source="internal_rag",
                    description="an internal angle",
                ),
            ],
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
        pool_batched = await conn.fetchval(
            "SELECT count(*) FROM topic_pool "
            "WHERE niche_id = $1 AND status = 'batched'",
            n.id,
        )
    # One winner in each table, and both pool rows flipped.
    assert external_count == 1
    assert internal_count == 1
    assert pool_batched == 2


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

    # Two pool rows share the SAME title but carry DISTINCT dedup_keys —
    # raw SQL because insert_pooled_topics would (correctly) collapse them
    # at ingest. The pair still reaches the sweep whenever near-dupes get
    # distinct keys (title variants, rows ingested before a dedup fix), so
    # the sweep-side mark_duplicates pass (#1561) stays load-bearing. Plus
    # two genuinely distinct topics (no shared content words).
    rows = [
        ("Operator Surface Unreachability", "why the gauge flatlines", "manual-a"),
        ("Operator Surface Unreachability", "duplicate from a second session", "manual-b"),
        ("Postgres Vacuum Tuning Guide", "autovacuum thresholds", "manual-c"),
        ("Zero Trust Network Segmentation", "east-west traffic controls", "manual-d"),
    ]
    async with db_pool.acquire() as conn:
        for title, angle, key in rows:
            await conn.execute(
                "INSERT INTO topic_pool (niche_id, source, title, summary, "
                "category, dedup_key, status) "
                "VALUES ($1, 'internal_rag', $2, $3, 'claude_session', $4, 'pooled')",
                n.id, title, angle, key,
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

    # Multi-word, word-disjoint titles: survive the topic-sanity intake
    # filter and the intra-batch dedup pass.
    from plugins.topic_source import DiscoveredTopic
    from services.topic_pool import insert_pooled_topics

    async with db_pool.acquire() as conn:
        await insert_pooled_topics(
            conn, niche_id=n.id, source="internal_rag",
            topics=[
                DiscoveredTopic(
                    title=t, category="claude_session",
                    source="internal_rag", description=f"A{i}",
                )
                for i, t in enumerate([
                    "Vector embedding drift",
                    "Telegram alert routing",
                    "Cuda memory fragmentation",
                ])
            ],
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

    # The pool DOES hold candidates this sweep … (multi-word, word-disjoint
    # titles so they survive the sanity filter + dedup and genuinely reach
    # the LLM scorer — the guard under test is about SCORER emptiness, not
    # upstream filtering).
    from plugins.topic_source import DiscoveredTopic
    from services.topic_pool import insert_pooled_topics

    async with db_pool.acquire() as conn:
        await insert_pooled_topics(
            conn, niche_id=n.id, source="internal_rag",
            topics=[
                DiscoveredTopic(
                    title=t, category="claude_session",
                    source="internal_rag", description=f"Angle {i}",
                )
                for i, t in enumerate([
                    "Async worker pools explained",
                    "Grafana dashboard provisioning",
                    "Postgres vacuum tuning",
                    "LangGraph checkpoint recovery",
                ])
            ],
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

    async def test_handoff_carries_the_summary_as_research_context(self):
        """The measured fact block must reach the writer, not just the angle.

        ``writer_core._extract_caller_research`` reads exactly
        ``metadata.research_context`` — it does NOT fall back to ``summary`` or
        ``angle``. This assertion is the one that would have caught the
        2026-09-02 incident: task e043649f carried a 673-char measured fact
        block into ``topic_candidates.summary``, lost it here, and the writer
        invented every throughput figure in a post that reached
        ``awaiting_approval`` at quality_score 98.
        """
        import json

        captured: list[tuple] = []

        async def _capture(sql, *args, **kwargs):
            captured.append((sql, args))
            return "INSERT 0 1"

        pool, _conn = _make_mock_pool(execute_side_effect=_capture)
        svc = TopicBatchService(pool, site_config=SiteConfig())
        winner = _make_candidate()
        winner.summary = "MEASURED DATA — decode 124.6 tokens/second."

        await svc._handoff_to_pipeline(
            winner=winner, niche=_make_niche(), batch_id=uuid4(),
        )

        versions = [a for sql, a in captured if "pipeline_versions" in sql]
        assert versions, "no pipeline_versions INSERT captured"
        blob = json.dumps(versions[0], default=str)
        assert "research_context" in blob, (
            "the handoff must write metadata.research_context — the writer "
            "reads that key and nothing else"
        )
        assert "124.6" in blob, "the fact block itself must survive the handoff"

    async def test_a_summaryless_winner_still_hands_off(self):
        """Most topics carry no summary; research_context is then empty, which
        is honest — the writer simply has no caller-attached corpus."""
        pool, conn = _make_mock_pool()
        svc = TopicBatchService(pool, site_config=SiteConfig())
        winner = _make_candidate()
        winner.summary = None

        await svc._handoff_to_pipeline(
            winner=winner, niche=_make_niche(), batch_id=uuid4(),
        )
        assert conn.execute.await_count == 2

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


# ===========================================================================
# Topic-sanity gate — 2026-06-30 dots-topic incident regression guards
# ===========================================================================

# The real topic from pipeline_tasks 9921678f-9b5b-4d24-9f07-c9d0398cf793,
# verbatim: a dots-only dev.to headline that the LLM final-scorer ranked
# TOP of its batch (65) and auto-resolve promoted into a full GPU run.
DOTS_TOPIC = ". .. . ... . .... . .... . ... ."


@pytest.mark.unit
class TestHandoffTopicSanityGate:
    """``_handoff_to_pipeline`` is the last seam before a batch winner
    becomes a ``pipeline_tasks`` row — a contentless topic must be blocked
    HERE, before any DB write, with a loud ``topic_sanity_rejected``
    finding (per ``feedback_no_silent_defaults``)."""

    async def test_dots_topic_blocked_before_any_insert(self):
        from services.topic_sanity import TopicSanityError

        pool, conn = _make_mock_pool()
        svc = TopicBatchService(pool, site_config=SiteConfig())

        with patch("services.topic_batch_service.emit_finding") as emit:
            with pytest.raises(TopicSanityError):
                await svc._handoff_to_pipeline(
                    winner=_make_candidate(title=DOTS_TOPIC),
                    niche=_make_niche(),
                    batch_id=uuid4(),
                )

        # Gate fired before the task/version INSERTs (and before the
        # template-slug resolver ever touched the pool).
        assert conn.execute.await_count == 0
        emit.assert_called_once()
        kwargs = emit.call_args.kwargs
        assert kwargs["kind"] == "topic_sanity_rejected"
        assert kwargs["severity"] == "warn"

    async def test_operator_edit_is_what_gets_gated(self):
        """The gate judges the topic that actually ships — a garbage
        candidate title rescued by a sane operator edit passes."""
        winner = _make_candidate(title=DOTS_TOPIC)
        winner.operator_edited_topic = "Why local LLM routers beat cloud defaults"

        pool, conn = _make_mock_pool()
        svc = TopicBatchService(pool, site_config=SiteConfig())

        await svc._handoff_to_pipeline(
            winner=winner, niche=_make_niche(), batch_id=uuid4(),
        )

        assert conn.execute.await_count == 2  # pipeline_tasks + pipeline_versions

    async def test_min_alpha_words_read_from_site_config(self):
        """Operator-tuned threshold flows through: min=1 lets a
        single-word topic hand off."""
        pool, conn = _make_mock_pool()
        svc = TopicBatchService(
            pool,
            site_config=SiteConfig(
                initial_config={"topic_sanity_min_alpha_words": "1"}
            ),
        )

        await svc._handoff_to_pipeline(
            winner=_make_candidate(title="Cybersecurity"),
            niche=_make_niche(),
            batch_id=uuid4(),
        )

        assert conn.execute.await_count == 2


@pytest.mark.unit
class TestHandoffRecentCoverageGate:
    """Incident 2026-07-23: internal_rag re-proposed the already-published
    Grafana-telemetry theme ("Ditching Grafana for Native Telemetry" vs
    "The Shift to Native Telemetry", published 8 days earlier) and it
    auto-resolved into a full generation the operator had to reject.
    ``_handoff_to_pipeline`` is the last dedup seam before a winner becomes
    a ``pipeline_tasks`` row — a near-duplicate must be blocked HERE,
    before any DB write, with a ``topic_duplicate_rejected`` finding."""

    def _match(self):
        from services.topic_recent_coverage import RecentCoverageMatch

        return RecentCoverageMatch(
            kind="published_post",
            ref_id="36a2c300",
            title="The Shift to Native Telemetry",
            similarity=0.86,
            published_at=datetime(2026, 7, 15, tzinfo=timezone.utc),
        )

    async def test_near_duplicate_blocked_before_any_insert(self, monkeypatch):
        from services.topic_recent_coverage import RecentCoverageError

        monkeypatch.setattr(
            "services.topic_recent_coverage.check_recent_coverage",
            AsyncMock(return_value=self._match()),
        )
        pool, conn = _make_mock_pool()
        svc = TopicBatchService(pool, site_config=SiteConfig())

        with patch("services.topic_batch_service.emit_finding") as emit:
            with pytest.raises(RecentCoverageError) as exc_info:
                await svc._handoff_to_pipeline(
                    winner=_make_candidate(
                        title="Ditching Grafana for Native Telemetry",
                    ),
                    niche=_make_niche("glad-labs"),
                    batch_id=uuid4(),
                )

        # Gate fired before the task/version INSERTs.
        assert conn.execute.await_count == 0
        emit.assert_called_once()
        kwargs = emit.call_args.kwargs
        assert kwargs["kind"] == "topic_duplicate_rejected"
        assert kwargs["severity"] == "warn"
        assert kwargs["extra"]["match"]["title"] == "The Shift to Native Telemetry"
        # Operator-facing message names the colliding post.
        assert "The Shift to Native Telemetry" in str(exc_info.value)

    async def test_gate_judges_operator_edited_composite(self, monkeypatch):
        """The gate must judge what actually ships — the operator-edited
        topic + angle composite, not the raw candidate title."""
        seen: list[str] = []

        async def _check(text, **kwargs):
            seen.append(text)
            return None

        monkeypatch.setattr(
            "services.topic_recent_coverage.check_recent_coverage", _check,
        )
        pool, conn = _make_mock_pool()
        svc = TopicBatchService(pool, site_config=SiteConfig())

        winner = _make_candidate(title="Original Title")
        winner.operator_edited_topic = "Edited Topic"
        winner.operator_edited_angle = "Edited angle"

        await svc._handoff_to_pipeline(
            winner=winner, niche=_make_niche(), batch_id=uuid4(),
        )

        assert seen == ["Edited Topic — Edited angle"]
        assert conn.execute.await_count == 2  # gate passed → both INSERTs

    async def test_no_match_hands_off_normally(self):
        # The autouse _no_recent_coverage fixture already stubs the check
        # to None — the plain handoff path must be unaffected by the gate.
        pool, conn = _make_mock_pool()
        svc = TopicBatchService(pool, site_config=SiteConfig())

        await svc._handoff_to_pipeline(
            winner=_make_candidate(), niche=_make_niche(), batch_id=uuid4(),
        )

        assert conn.execute.await_count == 2


@pytest.mark.unit
class TestDedupeCandidatesRecentCoverage:
    """``_dedupe_candidates`` threads the angle/summary + niche into the
    dedup engine (the composite is what separates re-treads from
    neighbours) and surfaces NAMED drops as one aggregated
    ``topic_duplicate_suppressed`` finding per sweep."""

    class _FakeDeduper:
        def __init__(self):
            self.wrappers = None

        async def mark_duplicates(self, wrappers):
            self.wrappers = wrappers
            for w in wrappers:
                if "Ditching Grafana" in w.title:
                    w.is_duplicate = True
                    w.duplicate_of = {
                        "kind": "published_post",
                        "ref_id": "36a2c300",
                        "title": "The Shift to Native Telemetry",
                        "similarity": 0.86,
                        "published_at": "2026-07-15T00:00:00+00:00",
                    }
            return wrappers

    async def test_summaries_and_niche_threaded_named_drop_surfaced(
        self, monkeypatch,
    ):
        deduper = self._FakeDeduper()
        captured_kwargs: dict = {}

        def _get_deduper(pool, *, site_config, niche_slug=None):
            captured_kwargs["niche_slug"] = niche_slug
            return deduper

        monkeypatch.setattr(
            "services.topic_dedup_semantic.get_deduplicator", _get_deduper,
        )
        pool, _conn = _make_mock_pool()
        svc = TopicBatchService(pool, site_config=SiteConfig())

        external = [
            {"kind": "external", "data": {
                "title": "Some external headline", "summary": "external summary",
            }},
        ]
        internal = [
            {"kind": "internal", "data": {
                "distilled_topic": "Ditching Grafana for Native Telemetry",
                "distilled_angle": "The shift from generic monitoring tools",
            }},
        ]

        with patch("services.topic_batch_service.emit_finding") as emit:
            kept_ext, kept_int = await svc._dedupe_candidates(
                external, internal, niche=_make_niche("glad-labs"),
            )

        # Niche threaded into the engine factory.
        assert captured_kwargs["niche_slug"] == "glad-labs"
        # Wrappers carried the composite inputs.
        by_title = {w.title: w for w in deduper.wrappers}
        assert by_title["Some external headline"].summary == "external summary"
        assert (
            by_title["Ditching Grafana for Native Telemetry"].summary
            == "The shift from generic monitoring tools"
        )
        # The named duplicate was dropped; the external survivor kept.
        assert kept_ext == external
        assert kept_int == []
        # One aggregated finding naming candidate + matched post.
        emit.assert_called_once()
        kwargs = emit.call_args.kwargs
        assert kwargs["kind"] == "topic_duplicate_suppressed"
        assert kwargs["severity"] == "info"
        assert "The Shift to Native Telemetry" in kwargs["body"]
        assert kwargs["extra"]["suppressed"][0]["match"]["similarity"] == 0.86

    async def test_unnamed_drops_emit_no_finding(self, monkeypatch):
        """Lexical/semantic engines mark duplicates without a named match —
        the finding only fires when there's something to tell the operator."""

        class _Anon:
            async def mark_duplicates(self, wrappers):
                wrappers[0].is_duplicate = True  # no duplicate_of annotation
                return wrappers

        monkeypatch.setattr(
            "services.topic_dedup_semantic.get_deduplicator",
            lambda pool, *, site_config, niche_slug=None: _Anon(),
        )
        pool, _conn = _make_mock_pool()
        svc = TopicBatchService(pool, site_config=SiteConfig())

        with patch("services.topic_batch_service.emit_finding") as emit:
            kept_ext, _ = await svc._dedupe_candidates(
                [{"kind": "external", "data": {"title": "T", "summary": ""}}],
                [],
                niche=_make_niche(),
            )

        assert kept_ext == []
        emit.assert_not_called()


@pytest.mark.unit
class TestDropContentlessCandidates:
    """Sweep-intake filter — garbage candidate titles must never occupy a
    batch slot (they'd otherwise reach the auto-resolver and, pre-gate,
    a GPU run). One aggregated finding per sweep, not silence."""

    def _svc(self) -> TopicBatchService:
        pool, _conn = _make_mock_pool()
        return TopicBatchService(pool, site_config=SiteConfig())

    async def test_dots_candidate_dropped_and_finding_emitted(self):
        svc = self._svc()
        external = [
            {"kind": "external", "data": {"title": DOTS_TOPIC, "summary": ""}},
            {"kind": "external", "data": {"title": "Why RTX 5090 thermals matter", "summary": "s"}},
        ]
        internal = [
            {"kind": "internal", "data": {"distilled_topic": "Pipeline design lessons", "distilled_angle": "a"}},
        ]

        with patch("services.topic_batch_service.emit_finding") as emit:
            kept_ext, kept_int = svc._drop_contentless_candidates(
                _make_niche(), external, internal,
            )

        assert len(kept_ext) == 1
        assert kept_ext[0]["data"]["title"].startswith("Why RTX")
        assert len(kept_int) == 1
        emit.assert_called_once()
        kwargs = emit.call_args.kwargs
        assert kwargs["kind"] == "topic_sanity_rejected"
        assert kwargs["severity"] == "warn"

    async def test_carry_forward_row_shape_filtered(self):
        svc = self._svc()
        external = [
            {"row": {"title": DOTS_TOPIC}, "decay_factor": 0.7},
            {"row": {"title": "A perfectly good headline"}, "decay_factor": 0.7},
        ]

        with patch("services.topic_batch_service.emit_finding"):
            kept_ext, kept_int = svc._drop_contentless_candidates(
                _make_niche(), external, [],
            )

        assert len(kept_ext) == 1
        assert kept_ext[0]["row"]["title"] == "A perfectly good headline"
        assert kept_int == []

    async def test_all_sane_pools_pass_through_without_finding(self):
        svc = self._svc()
        external = [
            {"kind": "external", "data": {"title": "Local model routing in practice"}},
        ]
        internal = [
            {"kind": "internal", "data": {"distilled_topic": "QA rails as hard gates"}},
        ]

        with patch("services.topic_batch_service.emit_finding") as emit:
            kept_ext, kept_int = svc._drop_contentless_candidates(
                _make_niche(), external, internal,
            )

        assert kept_ext == external
        assert kept_int == internal
        emit.assert_not_called()


async def test_run_sweep_drops_contentless_candidates_at_intake(db_pool, monkeypatch):
    """End-to-end intake guard: a source surfacing the incident's dots
    topic (and an empty distillation) must produce a batch containing
    only the sane candidates."""
    nsvc = NicheService(db_pool)
    n = await nsvc.create(
        slug=f"test-niche-sanity-{uuid4().hex[:8]}", name="Test", batch_size=5,
    )
    await nsvc.set_goals(n.id, [
        NicheGoal("TRAFFIC", 50),
        NicheGoal("EDUCATION", 50),
    ])
    # Raw SQL seeding (unique manual dedup_keys) so the contentless rows
    # genuinely reach the sweep's intake filter — the pool-ingest path
    # (insert_pooled_topics) would collide empty titles on dedup_key.
    topics = [
        DOTS_TOPIC,  # the incident topic, verbatim
        "",          # empty distillation
        "How local QA rails catch fabricated citations",
        "Why single-GPU VRAM budgets shape model routing",
        "Postgres as the spinal cord of an AI business",
    ]
    async with db_pool.acquire() as conn:
        for i, t in enumerate(topics):
            await conn.execute(
                "INSERT INTO topic_pool (niche_id, source, title, summary, "
                "category, dedup_key, status) "
                "VALUES ($1, 'internal_rag', $2, 'angle', 'claude_session', $3, 'pooled')",
                n.id, t, f"sanity-{i}",
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
    # 5 generated − 2 contentless = 3 slots used.
    assert batch.candidate_count == 3

    async with db_pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT distilled_topic FROM internal_topic_candidates "
            "WHERE batch_id = $1",
            batch.id,
        )
    titles = [r["distilled_topic"] for r in rows]
    assert DOTS_TOPIC not in titles
    assert "" not in titles
    assert len(titles) == 3


# ===========================================================================
# _handoff_to_pipeline — target_length variety (length-uniformity bug)
# ===========================================================================


@pytest.mark.unit
class TestHandoffTargetLength:
    """The niche auto-queue handoff must vary post length via the shared
    weighted picker (``services.topic_length.pick_target_length``) rather
    than omitting ``target_length`` and falling to the
    ``pipeline_tasks.target_length`` column default (1500).

    Pins the length-uniformity bug: every ``glad-labs`` post requested
    exactly 1500 words because this INSERT never set ``target_length`` —
    so the variance picker (and the DB-configurable
    ``topic_discovery_length_distribution``) had no effect on auto-queued
    content.
    """

    async def test_pipeline_insert_includes_picked_target_length(self, monkeypatch):
        """The INSERT must name the ``target_length`` column and carry the
        value the weighted picker returned (not the hardcoded 1500 default).
        """
        captured: list[tuple[str, tuple]] = []

        async def _capture(sql, *args, **kwargs):
            captured.append((sql, args))
            return "INSERT 0 1"

        # Pin the picker to a sentinel so the assertion is deterministic
        # (the real picker draws a random length from the weighted buckets).
        monkeypatch.setattr(
            "services.topic_batch_service.pick_target_length",
            lambda site_config: 2345,
            raising=False,
        )

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
        assert "target_length" in pipeline_sql
        assert 2345 in pipeline_args

    async def test_handoff_calls_picker_with_di_site_config(self, monkeypatch):
        """The picker must receive the service's DI ``site_config`` so the
        DB-configurable distribution applies — not a ``None`` fallback to
        the hardcoded default buckets.
        """
        seen: dict = {}

        def _fake_pick(site_config):
            seen["site_config"] = site_config
            return 1234

        monkeypatch.setattr(
            "services.topic_batch_service.pick_target_length",
            _fake_pick,
            raising=False,
        )

        pool, _ = _make_mock_pool()
        sc = SiteConfig()
        svc = TopicBatchService(pool, site_config=sc)

        await svc._handoff_to_pipeline(
            winner=_make_candidate(), niche=_make_niche(), batch_id=uuid4(),
        )

        assert seen["site_config"] is sc


# ---------------------------------------------------------------------------
# External-candidate internal grounding (poindexter#822)
# ---------------------------------------------------------------------------


def _grounding_cfg(**over):
    base = {
        "niche_external_grounding_enabled": "true",
        "niche_external_grounding_penalty_factor": "0.5",
        "niche_top_n_per_pool": "5",
    }
    base.update(over)
    return SiteConfig(initial_config=base)


async def _grounding_niche(db_pool, slug):
    nsvc = NicheService(db_pool)
    n = await nsvc.create(slug=slug, name="G", batch_size=3)
    await nsvc.set_goals(n.id, [NicheGoal("TRAFFIC", 100)])
    return n


async def test_ungrounded_external_gets_penalty(db_pool, monkeypatch):
    """An ungrounded external candidate's pre-rank score is multiplied by the
    penalty factor, the similarity is recorded, and a finding is emitted."""
    from services import topic_batch_service as tbs
    from services.topic_grounding import GroundingResult

    async def fake_embed(text, *, site_config=None):
        return [0.1] * 768

    monkeypatch.setattr("services.topic_ranking.embed_text", fake_embed)
    monkeypatch.setattr("services.topic_ranking._embed_text_cached", fake_embed)

    grounded_flag = {"grounded": True}

    async def fake_grounding(pool, vec, *, site_config):
        return GroundingResult(
            similarity=0.1, grounded=grounded_flag["grounded"], match=None,
        )

    monkeypatch.setattr(tbs, "internal_grounding", fake_grounding)
    findings: list[dict] = []
    monkeypatch.setattr(tbs, "emit_finding", lambda **kw: findings.append(kw))

    n = await _grounding_niche(db_pool, "grounding-penalty")
    svc = TopicBatchService(db_pool, site_config=_grounding_cfg())
    item = {"data": {"id": "e1", "title": "Popular Thing", "summary": "s"}}

    # Control run: grounded -> no penalty -> baseline score.
    grounded_flag["grounded"] = True
    ctrl, _ = await svc._embed_and_pre_rank(n, [dict(item)], [])
    base = ctrl[0].embedding_score

    # Penalized run: ungrounded -> score * 0.5.
    grounded_flag["grounded"] = False
    pen, _ = await svc._embed_and_pre_rank(n, [dict(item)], [])

    assert pen[0].embedding_score == pytest.approx(base * 0.5)
    assert pen[0].score_breakdown["_grounding"] == pytest.approx(0.1)
    assert pen[0].grounding_match is None
    assert any(f["kind"] == "external_topic_ungrounded" for f in findings)


async def test_grounded_external_no_penalty_and_match_stashed(db_pool, monkeypatch):
    from services import topic_batch_service as tbs
    from services.topic_grounding import GroundingMatch, GroundingResult

    async def fake_embed(text, *, site_config=None):
        return [0.1] * 768

    monkeypatch.setattr("services.topic_ranking.embed_text", fake_embed)
    monkeypatch.setattr("services.topic_ranking._embed_text_cached", fake_embed)

    match = GroundingMatch("posts", "p1", "we shipped X", 0.9)

    async def fake_grounding(pool, vec, *, site_config):
        return GroundingResult(similarity=0.9, grounded=True, match=match)

    monkeypatch.setattr(tbs, "internal_grounding", fake_grounding)

    n = await _grounding_niche(db_pool, "grounding-stash")
    svc = TopicBatchService(db_pool, site_config=_grounding_cfg())
    ext, _ = await svc._embed_and_pre_rank(
        n, [{"data": {"id": "e1", "title": "Grounded Thing", "summary": "s"}}], [],
    )
    assert ext[0].grounding_match is match
    assert ext[0].score_breakdown["_grounding"] == pytest.approx(0.9)


async def test_grounding_disabled_is_noop(db_pool, monkeypatch):
    from services import topic_batch_service as tbs

    async def fake_embed(text, *, site_config=None):
        return [0.1] * 768

    monkeypatch.setattr("services.topic_ranking.embed_text", fake_embed)
    monkeypatch.setattr("services.topic_ranking._embed_text_cached", fake_embed)

    async def fake_grounding(pool, vec, *, site_config):
        raise AssertionError("must not be called when disabled")

    monkeypatch.setattr(tbs, "internal_grounding", fake_grounding)

    n = await _grounding_niche(db_pool, "grounding-disabled")
    svc = TopicBatchService(
        db_pool, site_config=_grounding_cfg(niche_external_grounding_enabled="false"),
    )
    ext, _ = await svc._embed_and_pre_rank(
        n, [{"data": {"id": "e1", "title": "Thing Here", "summary": "s"}}], [],
    )
    assert "_grounding" not in ext[0].score_breakdown


async def test_internal_candidates_never_grounding_penalized(db_pool, monkeypatch):
    from services import topic_batch_service as tbs

    async def fake_embed(text, *, site_config=None):
        return [0.1] * 768

    monkeypatch.setattr("services.topic_ranking.embed_text", fake_embed)
    monkeypatch.setattr("services.topic_ranking._embed_text_cached", fake_embed)

    async def fake_grounding(pool, vec, *, site_config):
        raise AssertionError("grounding must not run for internal candidates")

    monkeypatch.setattr(tbs, "internal_grounding", fake_grounding)

    n = await _grounding_niche(db_pool, "grounding-internal")
    svc = TopicBatchService(db_pool, site_config=_grounding_cfg())
    _ext, intr = await svc._embed_and_pre_rank(
        n, [], [{"data": {"distilled_topic": "Our Retro",
                          "distilled_angle": "why we did it",
                          "primary_ref": "r1"}}],
    )
    assert intr and "_grounding" not in intr[0].score_breakdown


def _captured_stage_data(seen):
    """Parse the stage_data JSON from the captured pipeline_versions INSERT."""
    import json as _json_mod
    args = next(a for sql, a in seen if "pipeline_versions" in sql)
    return _json_mod.loads(args[2])  # $3 positional = json.dumps(stage_data)


async def test_handoff_threads_internal_grounding_metadata():
    """A grounded external winner threads its match into
    stage_data.metadata.internal_grounding (data-only, #822)."""
    seen: list[tuple] = []

    async def _capture(sql, *args, **kwargs):
        seen.append((sql, args))
        return "INSERT 0 1"

    pool, _conn = _make_mock_pool(execute_side_effect=_capture)
    svc = TopicBatchService(pool, site_config=SiteConfig())
    winner = _make_candidate()
    winner.grounding_ref = {
        "source_table": "posts", "source_id": "p1",
        "preview": "we shipped X", "similarity": 0.9,
    }

    await svc._handoff_to_pipeline(
        winner=winner, niche=_make_niche(), batch_id=uuid4(),
    )

    stage_data = _captured_stage_data(seen)
    assert stage_data["metadata"]["internal_grounding"] == {
        "source_table": "posts", "source_id": "p1",
        "preview": "we shipped X", "similarity": 0.9,
    }


async def test_handoff_internal_grounding_none_when_absent():
    """An internal / ungrounded winner threads internal_grounding=None."""
    seen: list[tuple] = []

    async def _capture(sql, *args, **kwargs):
        seen.append((sql, args))
        return "INSERT 0 1"

    pool, _conn = _make_mock_pool(execute_side_effect=_capture)
    svc = TopicBatchService(pool, site_config=SiteConfig())

    await svc._handoff_to_pipeline(
        winner=_make_candidate(), niche=_make_niche(), batch_id=uuid4(),
    )

    stage_data = _captured_stage_data(seen)
    assert stage_data["metadata"]["internal_grounding"] is None


@pytest.mark.unit
class TestOpenTopicDecisionGate:
    """poindexter#862 — the gate-open stub must actually notify the
    operator, not just log. ``topic_batches.status='open'`` (persisted by
    ``_write_batch`` before this is called) is already the durable gate
    state; ``services.approval_service.pause_at_gate`` doesn't apply here
    (it's ``pipeline_tasks``-specific), so the real gap was purely the
    missing notification — routine, not critical, so Discord per
    ``feedback_telegram_vs_discord``.
    """

    def _make_batch(self, niche_id) -> BatchSnapshot:
        return BatchSnapshot(
            id=uuid4(),
            niche_id=niche_id,
            status="open",
            candidate_count=5,
            expires_at=datetime.now(timezone.utc) + timedelta(days=7),
        )

    async def test_notifies_operator_as_non_critical(self, monkeypatch):
        notify_mock = AsyncMock()
        monkeypatch.setattr(
            "services.integrations.operator_notify.notify_operator",
            notify_mock,
        )
        niche = _make_niche(slug="test-niche")
        batch = self._make_batch(niche.id)
        svc = TopicBatchService(MagicMock(), site_config=SiteConfig())

        await svc._open_topic_decision_gate(batch, niche)

        notify_mock.assert_awaited_once()
        _args, kwargs = notify_mock.call_args
        assert kwargs["critical"] is False

    async def test_notification_message_identifies_niche_and_batch(
        self, monkeypatch,
    ):
        notify_mock = AsyncMock()
        monkeypatch.setattr(
            "services.integrations.operator_notify.notify_operator",
            notify_mock,
        )
        niche = _make_niche(slug="widget-reviews")
        batch = self._make_batch(niche.id)
        svc = TopicBatchService(MagicMock(), site_config=SiteConfig())

        await svc._open_topic_decision_gate(batch, niche)

        message = notify_mock.call_args[0][0]
        assert "widget-reviews" in message
        assert str(batch.candidate_count) in message
