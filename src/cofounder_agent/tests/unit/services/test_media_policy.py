"""Unit tests for ``resolve_media_to_generate`` (Glad-Labs/poindexter#24).

Exposes the niche-policy lookup (``niches.default_media_to_generate``,
migration 20260519_134736) as a standalone callable so the gate sequence
can be resolved at draft-approval time — not just at publish. Uses the
shared real-Postgres ``db_pool`` fixture (tests/unit/conftest.py).
"""

import pytest

from services.media_policy import resolve_media_to_generate

# asyncpg connections are loop-bound; the shared db_pool fixture is
# session-scoped, so tests must run on the session loop or asyncpg raises
# "got Future attached to a different loop" (repo convention — mirrors
# tests/unit/services/gates/test_post_approval_gates.py).
pytestmark = pytest.mark.asyncio(loop_scope="session")


async def test_resolves_from_niche(db_pool):
    # ``niches.name`` is NOT NULL with no default — must be supplied.
    await db_pool.execute(
        "INSERT INTO niches (slug, name, default_media_to_generate) VALUES ($1, $2, $3)",
        "ai-ml", "AI / ML", ["podcast", "video"],
    )
    assert await resolve_media_to_generate(db_pool, "ai-ml") == ["podcast", "video"]


async def test_missing_niche_returns_empty(db_pool):
    assert await resolve_media_to_generate(db_pool, "does-not-exist") == []


async def test_empty_policy_returns_empty(db_pool):
    # A niche that opts into no media (the NOT NULL ARRAY[] default) must
    # resolve to [] — the driver treats that as the text-only fast path.
    await db_pool.execute(
        "INSERT INTO niches (slug, name, default_media_to_generate) VALUES ($1, $2, $3)",
        "text-only", "Text Only", [],
    )
    assert await resolve_media_to_generate(db_pool, "text-only") == []


async def test_none_slug_returns_empty(db_pool):
    # A post carrying no niche slug must short-circuit without a DB round-trip.
    assert await resolve_media_to_generate(db_pool, None) == []
