"""Niche prompt rendering test for the dev_diary niche.

Validates the migration 0134 seed against a real Postgres test DB:

- The ``dev_diary`` row exists with a non-null ``writer_prompt_override``.
- The prompt is first-person and mentions PRs/commits/decisions.
- The prompt carries the bot-attribution footer.
- The prompt forbids claiming external work as Glad Labs'.
- The matching ``qa_allow_first_person_niches`` app_setting is seeded.
"""

from __future__ import annotations

import pytest
import pytest_asyncio


@pytest_asyncio.fixture(loop_scope="session")
async def seeded_db_pool(db_pool):
    """The conftest's ``db_pool`` fixture truncates ``niches CASCADE``
    after each test, which wipes our migration-0134 seed. Re-run the
    migration's ``up`` here so each dev_diary test starts with the row
    in place.
    """
    # Lazy import the migration module so we don't trigger a real-DB
    # connection at collection time.
    import importlib.util
    from pathlib import Path

    mig_path = Path(__file__).resolve().parents[3] / "services" / "migrations" \
               / "0134_seed_dev_diary_niche.py"
    spec = importlib.util.spec_from_file_location("_mig_0134", mig_path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    await mod.up(db_pool)
    yield db_pool


@pytest.mark.unit
@pytest.mark.asyncio(loop_scope="session")
class TestDevDiaryNicheSeed:
    async def test_niche_row_exists(self, seeded_db_pool):
        async with seeded_db_pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT slug, name, writer_prompt_override, writer_rag_mode, "
                "       discovery_cadence_minute_floor, batch_size, active "
                "FROM niches WHERE slug = 'dev_diary'"
            )
        assert row is not None, "Migration 0134 should seed dev_diary niche"
        assert row["slug"] == "dev_diary"
        assert row["name"] == "Dev Diary"
        assert row["writer_prompt_override"] is not None
        assert row["writer_rag_mode"] == "TWO_PASS"
        assert row["discovery_cadence_minute_floor"] == 1440  # daily
        assert row["batch_size"] == 1
        assert row["active"] is True

    async def test_writer_prompt_uses_first_person(self, seeded_db_pool):
        prompt = await seeded_db_pool.fetchval(
            "SELECT writer_prompt_override FROM niches WHERE slug = 'dev_diary'"
        )
        assert prompt is not None
        # first-person markers explicitly required by the spec
        for token in ("we", "today"):
            assert token in prompt.lower(), f"prompt should use {token!r}"

    async def test_writer_prompt_requires_id_citations(self, seeded_db_pool):
        prompt = await seeded_db_pool.fetchval(
            "SELECT writer_prompt_override FROM niches WHERE slug = 'dev_diary'"
        )
        # Must instruct the model to cite IDs (PR / commit / decision)
        for token in ("PR", "commit", "decision"):
            assert token in prompt, f"prompt should reference {token!r} IDs"

    async def test_writer_prompt_has_bot_attribution_footer(self, seeded_db_pool):
        prompt = await seeded_db_pool.fetchval(
            "SELECT writer_prompt_override FROM niches WHERE slug = 'dev_diary'"
        )
        assert "Auto-compiled by Poindexter" in prompt

    async def test_writer_prompt_forbids_external_attribution(self, seeded_db_pool):
        prompt = await seeded_db_pool.fetchval(
            "SELECT writer_prompt_override FROM niches WHERE slug = 'dev_diary'"
        )
        # Soft guard: prompt must explicitly tell the model not to claim
        # external projects as Glad Labs' own.
        assert "external" in prompt.lower()
        assert "Glad Labs" in prompt

    async def test_niche_goals_sum_to_100(self, seeded_db_pool):
        async with seeded_db_pool.acquire() as conn:
            niche_id = await conn.fetchval(
                "SELECT id FROM niches WHERE slug = 'dev_diary'"
            )
            total = await conn.fetchval(
                "SELECT COALESCE(SUM(weight_pct), 0) FROM niche_goals WHERE niche_id = $1",
                niche_id,
            )
        assert int(total) == 100

    async def test_authority_is_top_goal(self, seeded_db_pool):
        async with seeded_db_pool.acquire() as conn:
            niche_id = await conn.fetchval(
                "SELECT id FROM niches WHERE slug = 'dev_diary'"
            )
            row = await conn.fetchrow(
                "SELECT goal_type, weight_pct FROM niche_goals "
                "WHERE niche_id = $1 ORDER BY weight_pct DESC LIMIT 1",
                niche_id,
            )
        assert row["goal_type"] == "AUTHORITY"

    async def test_qa_allow_first_person_niches_seeded(self, seeded_db_pool):
        value = await seeded_db_pool.fetchval(
            "SELECT value FROM app_settings WHERE key = 'qa_allow_first_person_niches'"
        )
        assert value is not None
        slugs = {s.strip() for s in value.split(",") if s.strip()}
        assert "dev_diary" in slugs
