"""Unit tests for services/topic_pool.py."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from plugins.topic_source import DiscoveredTopic
from services.topic_pool import dedup_key, insert_pooled_topics


def test_dedup_key_normalizes_title():
    # case-insensitive + whitespace-collapsed so trivial variants collide
    assert dedup_key("  Local  LLM   Inference ") == dedup_key("local llm inference")
    assert dedup_key("A") != dedup_key("B")


def _conn_returning(ids):
    """Conn whose fetchval pops successive return values (id or None)."""
    conn = MagicMock()
    conn.fetchval = AsyncMock(side_effect=list(ids))
    return conn


@pytest.mark.asyncio
async def test_insert_counts_only_new_rows():
    # First insert returns an id (new), second returns None (ON CONFLICT no-op).
    conn = _conn_returning(["11111111-1111-1111-1111-111111111111", None])
    topics = [
        DiscoveredTopic(title="One", category="tech", source="web_search",
                        source_url="https://x/1", relevance_score=2.0, description="d1"),
        DiscoveredTopic(title="Two", category="tech", source="web_search"),
    ]
    n = await insert_pooled_topics(
        conn, niche_id="22222222-2222-2222-2222-222222222222",
        source="web_search", topics=topics,
    )
    assert n == 1
    assert conn.fetchval.await_count == 2
    # First positional after SQL is niche_id; title is mapped from DiscoveredTopic.
    first = conn.fetchval.await_args_list[0]
    assert "INSERT INTO topic_pool" in first.args[0]
    assert "ON CONFLICT (niche_id, dedup_key) DO NOTHING" in first.args[0]


@pytest.mark.asyncio
async def test_insert_rejects_unknown_table():
    conn = _conn_returning([])
    with pytest.raises(ValueError):
        await insert_pooled_topics(
            conn, niche_id="x", source="web_search", topics=[], table="pipeline_tasks",
        )


# ---------------------------------------------------------------------------
# b2 — read_pooled + mark_batched (real-PG roundtrip via db_pool)
# ---------------------------------------------------------------------------

async def _pool_insert(db_pool, niche_id, source, topics):
    async with db_pool.acquire() as conn:
        return await insert_pooled_topics(
            conn, niche_id=niche_id, source=source, topics=topics,
        )


def _topic(title, *, desc="", url="", cat="", score=0.0):
    return DiscoveredTopic(
        title=title, category=cat, source="x",
        source_url=url, relevance_score=score, description=desc,
    )


@pytest.mark.asyncio(loop_scope="session")
async def test_read_pooled_maps_external_and_internal_shapes(db_pool):
    from services.niche_service import NicheService
    from services.topic_pool import read_pooled

    n = await NicheService(db_pool).create(
        slug="pool-read-shapes", name="Pool Read Shapes",
    )
    await _pool_insert(db_pool, n.id, "web_search", [
        _topic("External headline", desc="ext summary",
               url="https://x/ext", cat="ai", score=2.5),
    ])
    await _pool_insert(db_pool, n.id, "internal_rag", [
        _topic("Distilled internal theme", desc="the angle",
               cat="claude_session"),
    ])

    items = await read_pooled(db_pool, niche_id=n.id, per_source_limit=10)
    by_kind = {i["kind"]: i for i in items}
    assert set(by_kind) == {"external", "internal"}

    ext = by_kind["external"]["data"]
    # Shape parity with the deleted _discover_external mapping.
    assert ext["title"] == "External headline"
    assert ext["summary"] == "ext summary"
    assert ext["source_name"] == "web_search"
    assert ext["source_url"] == "https://x/ext"
    assert ext["source_ref"] == "https://x/ext"
    assert ext["category"] == "ai"
    assert ext["relevance_score"] == 2.5
    # Pool row id rides along so run_sweep can mark_batched the winners.
    assert ext["id"]

    intl = by_kind["internal"]["data"]
    # Shape parity with the internal-candidate dict _embed_and_pre_rank reads.
    assert intl["distilled_topic"] == "Distilled internal theme"
    assert intl["distilled_angle"] == "the angle"
    assert intl["source_kind"] == "claude_session"
    assert intl["primary_ref"]  # pool row id — the mark_batched handle


@pytest.mark.asyncio(loop_scope="session")
async def test_read_pooled_scopes_by_niche_and_status(db_pool):
    from services.niche_service import NicheService
    from services.topic_pool import mark_batched, read_pooled

    nsvc = NicheService(db_pool)
    n1 = await nsvc.create(slug="pool-scope-a", name="A")
    n2 = await nsvc.create(slug="pool-scope-b", name="B")
    await _pool_insert(db_pool, n1.id, "web_search", [_topic("Mine only")])
    await _pool_insert(db_pool, n2.id, "web_search", [_topic("Other niche row")])
    await _pool_insert(db_pool, n1.id, "web_search", [_topic("Already batched row")])

    items = await read_pooled(db_pool, niche_id=n1.id, per_source_limit=10)
    batched_id = next(
        i["data"]["id"] for i in items
        if i["data"]["title"] == "Already batched row"
    )
    async with db_pool.acquire() as conn:
        flipped = await mark_batched(conn, [batched_id])
    assert flipped == 1

    items = await read_pooled(db_pool, niche_id=n1.id, per_source_limit=10)
    titles = {i["data"]["title"] for i in items}
    assert titles == {"Mine only"}


@pytest.mark.asyncio(loop_scope="session")
async def test_read_pooled_caps_per_source_by_score(db_pool):
    from services.niche_service import NicheService
    from services.topic_pool import read_pooled

    n = await NicheService(db_pool).create(
        slug="pool-per-source-cap", name="Cap",
    )
    await _pool_insert(db_pool, n.id, "hackernews", [
        _topic("Low score", score=1.0),
        _topic("Best score", score=3.0),
        _topic("Mid score", score=2.0),
    ])
    await _pool_insert(db_pool, n.id, "internal_rag", [
        _topic("Internal one", cat="brain_knowledge"),
    ])

    items = await read_pooled(db_pool, niche_id=n.id, per_source_limit=2)
    hn_titles = {
        i["data"]["title"] for i in items if i["kind"] == "external"
    }
    # Highest-scored 2 of the 3 hackernews rows + the internal row.
    assert hn_titles == {"Best score", "Mid score"}
    assert sum(1 for i in items if i["kind"] == "internal") == 1


@pytest.mark.asyncio(loop_scope="session")
async def test_mark_batched_flips_only_named_ids(db_pool):
    from services.niche_service import NicheService
    from services.topic_pool import mark_batched, read_pooled

    n = await NicheService(db_pool).create(
        slug="pool-mark-batched", name="Mark",
    )
    await _pool_insert(db_pool, n.id, "web_search", [
        _topic("Chosen"), _topic("Left behind"),
    ])
    items = await read_pooled(db_pool, niche_id=n.id, per_source_limit=10)
    chosen_id = next(
        i["data"]["id"] for i in items if i["data"]["title"] == "Chosen"
    )

    async with db_pool.acquire() as conn:
        flipped = await mark_batched(conn, [chosen_id])
        assert flipped == 1
        row = await conn.fetchrow(
            "SELECT status, batched_at FROM topic_pool WHERE id = $1::uuid",
            chosen_id,
        )
        other = await conn.fetchrow(
            "SELECT status, batched_at FROM topic_pool "
            "WHERE niche_id = $1 AND title = 'Left behind'",
            n.id,
        )
    assert row["status"] == "batched"
    assert row["batched_at"] is not None
    assert other["status"] == "pooled"
    assert other["batched_at"] is None

    # Idempotent — a second call finds nothing pooled to flip.
    async with db_pool.acquire() as conn:
        assert await mark_batched(conn, [chosen_id]) == 0
        assert await mark_batched(conn, []) == 0


# ---------------------------------------------------------------------------
# b3 — claim_best_pooled_topic (the topic="auto" resolution seam)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_claim_best_pooled_topic_picks_sane_best_and_flips(db_pool):
    from services.niche_service import NicheService
    from services.topic_pool import claim_best_pooled_topic

    n = await NicheService(db_pool).create(
        slug="pool-claim-best", name="Claim",
    )
    # The top-scored row is a distiller failure sentinel — it must be
    # skipped in favour of the best SANE candidate.
    await _pool_insert(db_pool, n.id, "hackernews", [
        _topic("No topic found", score=9.0),
        _topic("Quantization Tradeoffs Explained", desc="an angle", score=3.0),
        _topic("Cheaper Alternative Headline", score=1.0),
    ])

    claimed = await claim_best_pooled_topic(db_pool, niche_id=n.id)
    assert claimed is not None
    assert claimed["title"] == "Quantization Tradeoffs Explained"
    assert claimed["summary"] == "an angle"
    assert claimed["source"] == "hackernews"

    async with db_pool.acquire() as conn:
        status = await conn.fetchval(
            "SELECT status FROM topic_pool WHERE id = $1::uuid", claimed["id"],
        )
    assert status == "batched"

    # Second claim returns the next-best sane row, not the same one.
    second = await claim_best_pooled_topic(db_pool, niche_id=n.id)
    assert second is not None
    assert second["title"] == "Cheaper Alternative Headline"


@pytest.mark.asyncio(loop_scope="session")
async def test_claim_best_pooled_topic_returns_none_when_pool_dry(db_pool):
    from services.niche_service import NicheService
    from services.topic_pool import claim_best_pooled_topic

    n = await NicheService(db_pool).create(
        slug="pool-claim-empty", name="Empty",
    )
    assert await claim_best_pooled_topic(db_pool, niche_id=n.id) is None

    # A pool holding ONLY junk also yields None (never a junk title).
    await _pool_insert(db_pool, n.id, "web_search", [_topic("Untitled", score=5.0)])
    assert await claim_best_pooled_topic(db_pool, niche_id=n.id) is None
