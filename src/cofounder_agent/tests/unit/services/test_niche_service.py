"""Tests for NicheService — CRUD over niches/goals/sources tables.

Spec: docs/superpowers/specs/2026-04-30-rag-pivot-niche-discovery-design.md
Plan: docs/superpowers/plans/2026-04-30-rag-pivot-niche-discovery.md (Task 2)

These tests roundtrip against a real Postgres test DB (the ``db_pool``
fixture is provided by ``tests/unit/conftest.py``). When no live Postgres
is reachable the fixture skips the module so CI runners without a DB
don't blow up at fixture time — same pattern as the integration_db tier.
"""

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
    with pytest.raises(Exception):
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
