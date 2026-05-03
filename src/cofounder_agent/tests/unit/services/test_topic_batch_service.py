"""Tests for TopicBatchService — orchestrates discovery → rank → batch → gate.

Spec: docs/superpowers/specs/2026-04-30-rag-pivot-niche-discovery-design.md
Plan: docs/superpowers/plans/2026-04-30-rag-pivot-niche-discovery.md (Task 6)

Roundtrips against the real Postgres test DB via the ``db_pool`` fixture
defined in ``tests/unit/conftest.py``. Skipped automatically when no live
Postgres DSN is reachable.
"""

from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from services.niche_service import Niche, NicheService, NicheGoal, NicheSource
from services.internal_rag_source import InternalCandidate
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

    svc = TopicBatchService(db_pool)
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

    svc = TopicBatchService(db_pool)
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
    svc = TopicBatchService(db_pool)
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
    svc = TopicBatchService(db_pool)
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


async def test_reject_batch_marks_expired_and_can_re_discover(db_pool):
    """reject_batch flips the batch to expired + frees up the
    one-open-batch-per-niche slot for a future sweep."""
    niche, batch_id, ext_ids, int_ids = await _seed_batch_with_mixed_candidates(
        db_pool, slug="reject-batch-niche", n_external=2, n_internal=3,
    )
    svc = TopicBatchService(db_pool)
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


# ===========================================================================
# _handoff_to_pipeline — #188/#341 regression guard
# ===========================================================================


def _make_mock_pool(execute_side_effect=None):
    """Lightweight pool that supports ``async with pool.acquire()`` +
    ``async with conn.transaction()`` + ``await conn.execute(...)``.

    Mirrors the helpers in ``test_tasks_db.py`` and
    ``test_topic_discovery.py`` so all #188 INSERT-target guard tests
    share a uniform shape.
    """
    conn = MagicMock()
    if execute_side_effect:
        conn.execute = AsyncMock(side_effect=execute_side_effect)
    else:
        conn.execute = AsyncMock()

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
        writer_rag_mode="TOPIC_ONLY",
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
        svc = TopicBatchService(pool)

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
        svc = TopicBatchService(pool)

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
        svc = TopicBatchService(pool)

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
