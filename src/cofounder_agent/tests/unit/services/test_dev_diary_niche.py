"""Niche prompt rendering test for the dev_diary niche.

Validates the seed shipped in ``0000_baseline.seeds.sql`` against a
real Postgres test DB:

- The ``dev_diary`` row exists with a non-null ``writer_prompt_override``.
- The prompt is first-person and mentions PRs/commits/decisions.
- The prompt carries the bot-attribution footer.
- The prompt forbids claiming external work as Glad Labs'.
- The matching ``qa_allow_first_person_niches`` app_setting is seeded.

Pre-2026-05-08 these tests had their own ``seeded_db_pool`` fixture that
re-imported the legacy migration ``0134_seed_dev_diary_niche.py`` after
each test (because the parent ``db_pool`` fixture truncated ``niches``).
That migration was absorbed into ``0000_baseline``, so the explicit
re-seed went away — the ``db_pool`` fixture now selectively deletes
test-created niches instead of truncating, which keeps the baseline
seed alive for the whole session.
"""

from __future__ import annotations

import pytest


@pytest.mark.unit
@pytest.mark.asyncio(loop_scope="session")
class TestDevDiaryNicheSeed:
    async def test_niche_row_exists(self, db_pool):
        async with db_pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT slug, name, writer_prompt_override, writer_rag_mode, "
                "       discovery_cadence_minute_floor, batch_size, active "
                "FROM niches WHERE slug = 'dev_diary'"
            )
        assert row is not None, "Migration 0134 should seed dev_diary niche"
        assert row["slug"] == "dev_diary"
        assert row["name"] == "Dev Diary"
        assert row["writer_prompt_override"] is not None
        # dev_diary switched from TWO_PASS to a zero-LLM compositor in
        # 2026-05-04 (see project_dev_diary_compositor memory). The seed
        # in 0000_baseline.seeds.sql captures the post-switch state.
        assert row["writer_rag_mode"] == "DETERMINISTIC_COMPOSITOR"
        assert row["discovery_cadence_minute_floor"] == 1440  # daily
        assert row["batch_size"] == 1
        assert row["active"] is True

    async def test_writer_prompt_uses_first_person(self, db_pool):
        prompt = await db_pool.fetchval(
            "SELECT writer_prompt_override FROM niches WHERE slug = 'dev_diary'"
        )
        assert prompt is not None
        # first-person markers explicitly required by the spec
        for token in ("we", "today"):
            assert token in prompt.lower(), f"prompt should use {token!r}"

    async def test_writer_prompt_requires_id_citations(self, db_pool):
        prompt = await db_pool.fetchval(
            "SELECT writer_prompt_override FROM niches WHERE slug = 'dev_diary'"
        )
        # Must instruct the model to cite IDs (PR / commit / decision)
        for token in ("PR", "commit", "decision"):
            assert token in prompt, f"prompt should reference {token!r} IDs"

    async def test_writer_prompt_has_bot_attribution_footer(self, db_pool):
        prompt = await db_pool.fetchval(
            "SELECT writer_prompt_override FROM niches WHERE slug = 'dev_diary'"
        )
        assert "Auto-compiled by Poindexter" in prompt

    async def test_writer_prompt_forbids_external_attribution(self, db_pool):
        prompt = await db_pool.fetchval(
            "SELECT writer_prompt_override FROM niches WHERE slug = 'dev_diary'"
        )
        # Soft guard: prompt must explicitly tell the model not to claim
        # external projects as Glad Labs' own.
        assert "external" in prompt.lower()
        assert "Glad Labs" in prompt

    async def test_niche_goals_sum_to_100(self, db_pool):
        async with db_pool.acquire() as conn:
            niche_id = await conn.fetchval(
                "SELECT id FROM niches WHERE slug = 'dev_diary'"
            )
            total = await conn.fetchval(
                "SELECT COALESCE(SUM(weight_pct), 0) FROM niche_goals WHERE niche_id = $1",
                niche_id,
            )
        assert int(total) == 100

    async def test_authority_is_top_goal(self, db_pool):
        async with db_pool.acquire() as conn:
            niche_id = await conn.fetchval(
                "SELECT id FROM niches WHERE slug = 'dev_diary'"
            )
            row = await conn.fetchrow(
                "SELECT goal_type, weight_pct FROM niche_goals "
                "WHERE niche_id = $1 ORDER BY weight_pct DESC LIMIT 1",
                niche_id,
            )
        assert row["goal_type"] == "AUTHORITY"

    async def test_qa_allow_first_person_niches_seeded(self, db_pool):
        value = await db_pool.fetchval(
            "SELECT value FROM app_settings WHERE key = 'qa_allow_first_person_niches'"
        )
        assert value is not None
        slugs = {s.strip() for s in value.split(",") if s.strip()}
        assert "dev_diary" in slugs
