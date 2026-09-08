"""poindexter#1042 — pool rows must never stay 'batched' with no candidate.

Two leak paths, both DB-backed against the throwaway test Postgres (the
``db_pool`` fixture; skipped when no DSN is reachable), plus the uuid guard.

1. A batch **refresh** replaces the candidate set wholesale; a previous
   winner that does not re-rank must go back to ``pooled``.
2. A **rejected / reaped** batch produced no task; every candidate's pool row
   goes back to ``pooled``.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from services.niche_service import NicheGoal, NicheService
from services.site_config import SiteConfig
from services.topic_batch_service import TopicBatchService, _looks_like_uuid
from services.topic_ranking import ScoredCandidate

# Same session-scoped loop as test_topic_batch_service: the db_pool fixture's
# connections live on it, and a test on its own loop would make asyncpg open a
# fresh socket that the unit-test egress guard rightly refuses.
pytestmark = pytest.mark.asyncio(loop_scope="session")


async def test_uuid_guard():
    assert _looks_like_uuid("1f1ef492-4640-41da-be09-4a95fa57a6a0")
    assert not _looks_like_uuid("ext-ref-0")
    assert not _looks_like_uuid("https://example.com/post")
    assert not _looks_like_uuid("")
    assert not _looks_like_uuid(None)


async def _seed_pool(db_pool, niche_id, titles, *, source="hackernews"):
    from plugins.topic_source import DiscoveredTopic
    from services.topic_pool import insert_pooled_topics

    async with db_pool.acquire() as conn:
        await insert_pooled_topics(
            conn, niche_id=niche_id, source=source,
            topics=[
                DiscoveredTopic(title=t, category="technology", source=source, description=f"about {t}")
                for t in titles
            ],
        )
        rows = await conn.fetch(
            "SELECT id, title FROM topic_pool WHERE niche_id = $1 ORDER BY title", niche_id,
        )
    return {r["title"]: str(r["id"]) for r in rows}


async def _pool_status(db_pool, pool_id: str) -> str:
    async with db_pool.acquire() as conn:
        return await conn.fetchval("SELECT status FROM topic_pool WHERE id = $1::uuid", pool_id)


def _cand(pool_id: str, title: str, score: float) -> ScoredCandidate:
    return ScoredCandidate(id=pool_id, title=title, summary=None, embedding_score=score, llm_score=score)


async def test_refresh_returns_dropped_winners_to_pooled(db_pool):
    nsvc = NicheService(db_pool)
    niche = await nsvc.create(slug="leak-refresh-niche", name="Leak", batch_size=2)
    await nsvc.set_goals(niche.id, [NicheGoal("TRAFFIC", 100)])
    ids = await _seed_pool(db_pool, niche.id, ["Alpha topic one", "Beta topic two", "Gamma topic three"])
    a, b, c = ids["Alpha topic one"], ids["Beta topic two"], ids["Gamma topic three"]

    svc = TopicBatchService(db_pool, site_config=SiteConfig())
    # First ranking: A and B win; the sweep marks them batched.
    snap = await svc._write_batch(niche, [_cand(a, "Alpha topic one", 90), _cand(b, "Beta topic two", 80)], [], [])
    from services.topic_pool import mark_batched

    async with db_pool.acquire() as conn:
        assert await mark_batched(conn, [a, b]) == 2
    assert await _pool_status(db_pool, a) == "batched"

    # Refresh: B and C win; A lost the re-rank.
    await svc._write_batch(
        niche, [_cand(b, "Beta topic two", 85), _cand(c, "Gamma topic three", 70)], [], [],
        replace_batch_id=snap.id,
    )
    assert await _pool_status(db_pool, a) == "pooled", "loser of a refresh must be re-pooled"
    assert await _pool_status(db_pool, b) == "batched", "a re-winner stays batched"
    # C is a fresh winner — the sweep (not _write_batch) flips it; still pooled here.
    assert await _pool_status(db_pool, c) == "pooled"
    async with db_pool.acquire() as conn:
        titles = {r["title"] for r in await conn.fetch("SELECT title FROM topic_candidates WHERE batch_id = $1", snap.id)}
    assert titles == {"Beta topic two", "Gamma topic three"}


async def test_reject_batch_returns_every_candidate_row_to_pooled(db_pool):
    nsvc = NicheService(db_pool)
    niche = await nsvc.create(slug="leak-reject-niche", name="Leak", batch_size=5)
    await nsvc.set_goals(niche.id, [NicheGoal("TRAFFIC", 100)])
    ext = await _seed_pool(db_pool, niche.id, ["Delta external topic"], source="search_autocomplete")
    internal = await _seed_pool(db_pool, niche.id, ["Epsilon internal topic"], source="internal_rag")
    d, e = ext["Delta external topic"], internal["Epsilon internal topic"]
    expires = datetime.now(timezone.utc) + timedelta(days=7)
    async with db_pool.acquire() as conn:
        batch_id = await conn.fetchval(
            "INSERT INTO topic_batches (niche_id, status, expires_at) VALUES ($1, 'open', $2) RETURNING id",
            niche.id, expires,
        )
        await conn.execute(
            "INSERT INTO topic_candidates (batch_id, niche_id, source_name, source_ref, title, summary, score, score_breakdown, rank_in_batch, decay_factor) "
            "VALUES ($1, $2, 'external', $3, $4, '', 80, '{}'::jsonb, 1, 1.0)",
            batch_id, niche.id, d, "Delta external topic",
        )
        await conn.execute(
            "INSERT INTO internal_topic_candidates (batch_id, niche_id, source_kind, primary_ref, supporting_refs, distilled_topic, distilled_angle, score, score_breakdown, rank_in_batch, decay_factor) "
            "VALUES ($1, $2, 'claude_session', $3, '[]'::jsonb, $4, '', 70, '{}'::jsonb, 2, 1.0)",
            batch_id, niche.id, e, "Epsilon internal topic",
        )
        # A legacy candidate whose ref is not a pool id must not break the repool.
        await conn.execute(
            "INSERT INTO topic_candidates (batch_id, niche_id, source_name, source_ref, title, summary, score, score_breakdown, rank_in_batch, decay_factor) "
            "VALUES ($1, $2, 'external', 'https://example.com/legacy', 'Legacy ref topic', '', 60, '{}'::jsonb, 3, 1.0)",
            batch_id, niche.id,
        )
        await conn.execute("UPDATE topic_pool SET status = 'batched', batched_at = NOW() WHERE id = ANY($1::uuid[])", [d, e])

    svc = TopicBatchService(db_pool, site_config=SiteConfig())
    await svc.reject_batch(batch_id=batch_id, reason="stale")

    assert await _pool_status(db_pool, d) == "pooled"
    assert await _pool_status(db_pool, e) == "pooled"
    async with db_pool.acquire() as conn:
        assert await conn.fetchval("SELECT status FROM topic_batches WHERE id = $1", batch_id) == "expired"
