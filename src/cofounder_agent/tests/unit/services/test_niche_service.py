"""Tests for NicheService — CRUD over niches/goals/sources tables.

Spec: docs/superpowers/specs/2026-04-30-rag-pivot-niche-discovery-design.md
Plan: docs/superpowers/plans/2026-04-30-rag-pivot-niche-discovery.md (Task 2)

These tests roundtrip against a real Postgres test DB (the ``db_pool``
fixture is provided by ``tests/unit/conftest.py``). When no live Postgres
is reachable the fixture skips the module so CI runners without a DB
don't blow up at fixture time — same pattern as the integration_db tier.
"""

from uuid import uuid4

import asyncpg
import pytest
from services.niche_service import NicheService, Niche, NicheGoal, NicheSource

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def test_create_niche_inserts_row(db_pool):
    svc = NicheService(db_pool)
    niche = await svc.create(slug="glad-labs", name="Glad Labs",
                             writer_rag_mode="TWO_PASS", batch_size=5)
    assert isinstance(niche, Niche)
    assert niche.slug == "glad-labs"
    assert niche.writer_rag_mode == "TWO_PASS"
    fetched = await svc.get_by_slug("glad-labs")
    assert fetched.id == niche.id


async def test_create_niche_rejects_duplicate_slug(db_pool):
    svc = NicheService(db_pool)
    await svc.create(slug="dupe", name="A")
    with pytest.raises(asyncpg.exceptions.UniqueViolationError):
        await svc.create(slug="dupe", name="B")


async def test_set_goals_validates_weights_sum_to_100(db_pool):
    svc = NicheService(db_pool)
    n = await svc.create(slug="x", name="X")
    with pytest.raises(ValueError, match="weights must sum to ~100"):
        await svc.set_goals(n.id, [
            NicheGoal(goal_type="TRAFFIC", weight_pct=30),
            NicheGoal(goal_type="EDUCATION", weight_pct=20),
            # sums to 50, should reject
        ])
    # 100 ± 1 tolerance is fine
    await svc.set_goals(n.id, [
        NicheGoal(goal_type="TRAFFIC", weight_pct=60),
        NicheGoal(goal_type="EDUCATION", weight_pct=40),
    ])
    fetched = await svc.get_goals(n.id)
    assert {g.goal_type: g.weight_pct for g in fetched} == {"TRAFFIC": 60, "EDUCATION": 40}


async def test_set_sources_replaces_prior_config(db_pool):
    svc = NicheService(db_pool)
    n = await svc.create(slug="y", name="Y")
    await svc.set_sources(n.id, [
        NicheSource(source_name="hackernews", enabled=True, weight_pct=50),
        NicheSource(source_name="devto", enabled=True, weight_pct=50),
    ])
    await svc.set_sources(n.id, [
        NicheSource(source_name="internal_rag", enabled=True, weight_pct=100),
    ])
    fetched = await svc.get_sources(n.id)
    assert len(fetched) == 1
    assert fetched[0].source_name == "internal_rag"


# ---------------------------------------------------------------------------
# Edge cases / error paths added 2026-05-08 — auto/test-expand
# ---------------------------------------------------------------------------


async def test_create_rejects_invalid_writer_rag_mode(db_pool):
    # Pre-insert guard: ValueError fires before the DB roundtrip, so no row.
    svc = NicheService(db_pool)
    with pytest.raises(ValueError, match="invalid writer_rag_mode"):
        await svc.create(slug="bad-mode", name="Bad Mode", writer_rag_mode="HYBRID")
    assert await svc.get_by_slug("bad-mode") is None


async def test_create_applies_defaults_for_optional_kwargs(db_pool):
    svc = NicheService(db_pool)
    n = await svc.create(slug="defaults", name="Defaults")
    assert n.active is True
    assert n.target_audience_tags == []
    assert n.writer_prompt_override is None
    assert n.writer_rag_mode == "TOPIC_ONLY"
    assert n.batch_size == 5
    assert n.discovery_cadence_minute_floor == 60


async def test_get_by_slug_returns_none_when_missing(db_pool):
    svc = NicheService(db_pool)
    assert await svc.get_by_slug("does-not-exist") is None


async def test_get_by_id_roundtrips_and_returns_none_for_missing(db_pool):
    svc = NicheService(db_pool)
    n = await svc.create(slug="by-id", name="By ID")
    fetched = await svc.get_by_id(n.id)
    assert fetched is not None and fetched.slug == "by-id"
    assert await svc.get_by_id(uuid4()) is None


async def test_list_active_orders_by_slug_and_excludes_inactive(db_pool):
    svc = NicheService(db_pool)
    await svc.create(slug="alpha", name="Alpha")
    await svc.create(slug="charlie", name="Charlie")
    inactive = await svc.create(slug="bravo-inactive", name="Bravo Inactive")
    # NicheService has no deactivate API — flip the flag directly.
    async with db_pool.acquire() as conn:
        await conn.execute("UPDATE niches SET active = false WHERE id = $1", inactive.id)
    actives = await svc.list_active()
    slugs = [n.slug for n in actives]
    assert "bravo-inactive" not in slugs
    assert slugs == sorted(slugs)
    assert {"alpha", "charlie"}.issubset(set(slugs))


async def test_set_goals_rejects_unknown_goal_type(db_pool):
    svc = NicheService(db_pool)
    n = await svc.create(slug="bad-goal", name="Bad Goal")
    with pytest.raises(ValueError, match="invalid goal_type"):
        await svc.set_goals(n.id, [
            NicheGoal(goal_type="MOON_LANDING", weight_pct=50),
            NicheGoal(goal_type="TRAFFIC", weight_pct=50),
        ])


async def test_set_goals_validation_preserves_existing_goals(db_pool):
    # The validation guards run BEFORE the DELETE inside the txn, so a
    # rejected call must not wipe the prior config — regression guard.
    svc = NicheService(db_pool)
    n = await svc.create(slug="atomic", name="Atomic")
    await svc.set_goals(n.id, [NicheGoal(goal_type="TRAFFIC", weight_pct=100)])
    with pytest.raises(ValueError):
        await svc.set_goals(n.id, [NicheGoal(goal_type="TRAFFIC", weight_pct=42)])
    surviving = await svc.get_goals(n.id)
    assert len(surviving) == 1
    assert surviving[0].weight_pct == 100


async def test_set_goals_accepts_99_and_101_weight_boundary(db_pool):
    svc = NicheService(db_pool)
    n = await svc.create(slug="boundary", name="Boundary")
    # 99 — lower edge of the ~100 tolerance window.
    await svc.set_goals(n.id, [
        NicheGoal(goal_type="TRAFFIC", weight_pct=49),
        NicheGoal(goal_type="EDUCATION", weight_pct=50),
    ])
    # 101 — upper edge.
    await svc.set_goals(n.id, [
        NicheGoal(goal_type="TRAFFIC", weight_pct=51),
        NicheGoal(goal_type="EDUCATION", weight_pct=50),
    ])
    # 98 — just outside, should reject.
    with pytest.raises(ValueError, match="weights must sum to ~100"):
        await svc.set_goals(n.id, [
            NicheGoal(goal_type="TRAFFIC", weight_pct=49),
            NicheGoal(goal_type="EDUCATION", weight_pct=49),
        ])


async def test_get_goals_orders_by_weight_pct_desc(db_pool):
    svc = NicheService(db_pool)
    n = await svc.create(slug="ordering", name="Ordering")
    await svc.set_goals(n.id, [
        NicheGoal(goal_type="TRAFFIC", weight_pct=20),
        NicheGoal(goal_type="EDUCATION", weight_pct=50),
        NicheGoal(goal_type="BRAND", weight_pct=30),
    ])
    goals = await svc.get_goals(n.id)
    weights = [g.weight_pct for g in goals]
    assert weights == sorted(weights, reverse=True)
    assert goals[0].goal_type == "EDUCATION"


async def test_get_sources_empty_for_niche_with_none_configured(db_pool):
    svc = NicheService(db_pool)
    n = await svc.create(slug="no-sources", name="No Sources")
    assert await svc.get_sources(n.id) == []
