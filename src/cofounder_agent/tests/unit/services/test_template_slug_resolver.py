"""Tests for ``services.template_slug_resolver``.

Resolution chain (in order):
  1. explicit caller-supplied template_slug
  2. niches.default_template_slug for the row's niche
  3. app_settings.default_template_slug (process-wide fallback)
  4. raise TemplateSlugUnresolvable (fail loud)

The resolver is the shared seam used by every ``pipeline_tasks``
inserter that doesn't go through ``tasks_db.add_task`` —
``topic_batch_service._handoff_to_pipeline``,
``topic_proposal_service.propose_topic``, and
``topic_discovery.queue_topics``. Each tier of the chain has its
own unit test below; the integration ordering test pins the order
(explicit beats niche beats setting).
"""

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest

from services.template_slug_resolver import (
    TemplateSlugUnresolvable,
    resolve_template_slug,
)

pytestmark = pytest.mark.asyncio(loop_scope="session")


def _make_pool(*, niche_row_value=None, app_setting_row_value=None,
               niche_raises=False, app_setting_raises=False):
    """Construct an asyncpg-pool mock that returns specific values for
    the resolver's two reads.

    ``niche_row_value`` and ``app_setting_row_value`` are the raw
    column values the resolver would see (``None``, ``""``, or a
    non-empty string). ``*_raises=True`` makes that particular read
    raise — tests can pin best-effort behaviour against transient DB
    errors.
    """
    conn = MagicMock()

    async def _fetchval(sql, *args, **kwargs):
        if "FROM niches" in sql:
            if niche_raises:
                raise RuntimeError("niche table boom")
            return niche_row_value
        raise AssertionError(f"unexpected fetchval sql: {sql!r}")

    async def _fetchrow(sql, *args, **kwargs):
        if "FROM app_settings" in sql:
            if app_setting_raises:
                raise RuntimeError("app_settings boom")
            if app_setting_row_value is None:
                return None
            return {"value": app_setting_row_value}
        raise AssertionError(f"unexpected fetchrow sql: {sql!r}")

    conn.fetchval = AsyncMock(side_effect=_fetchval)
    conn.fetchrow = AsyncMock(side_effect=_fetchrow)

    pool = MagicMock()

    @asynccontextmanager
    async def _acquire():
        yield conn

    pool.acquire = _acquire
    return pool, conn


@pytest.mark.unit
class TestExplicitWins:
    """Tier 1: caller-supplied value short-circuits everything else."""

    async def test_explicit_value_returned_without_db_hits(self):
        pool, conn = _make_pool(
            niche_row_value="should-not-see-this",
            app_setting_row_value="nor-this",
        )
        out = await resolve_template_slug(
            pool, explicit="dev_diary", niche_slug="dev_diary",
        )
        assert out == "dev_diary"
        # No DB read happened — the explicit value short-circuited.
        conn.fetchval.assert_not_awaited()
        conn.fetchrow.assert_not_awaited()

    async def test_explicit_value_trimmed(self):
        pool, _ = _make_pool()
        out = await resolve_template_slug(
            pool, explicit="  canonical_blog  ", niche_slug=None,
        )
        assert out == "canonical_blog"

    async def test_explicit_blank_string_falls_through(self):
        """Empty / whitespace-only explicit is treated as missing."""
        pool, _ = _make_pool(niche_row_value="canonical_blog")
        out = await resolve_template_slug(
            pool, explicit="   ", niche_slug="glad-labs",
        )
        assert out == "canonical_blog"


@pytest.mark.unit
class TestNicheTierWins:
    """Tier 2: niches.default_template_slug for the row's niche."""

    async def test_niche_default_returned(self):
        pool, conn = _make_pool(
            niche_row_value="canonical_blog",
            app_setting_row_value="should-not-see-this",
        )
        out = await resolve_template_slug(pool, niche_slug="glad-labs")
        assert out == "canonical_blog"
        # app_settings read never happened — niche tier short-circuited.
        conn.fetchrow.assert_not_awaited()

    async def test_niche_blank_falls_through_to_app_settings(self):
        pool, _ = _make_pool(
            niche_row_value="",
            app_setting_row_value="canonical_blog",
        )
        out = await resolve_template_slug(pool, niche_slug="glad-labs")
        assert out == "canonical_blog"

    async def test_niche_null_falls_through_to_app_settings(self):
        pool, _ = _make_pool(
            niche_row_value=None,
            app_setting_row_value="canonical_blog",
        )
        out = await resolve_template_slug(pool, niche_slug="glad-labs")
        assert out == "canonical_blog"

    async def test_no_niche_slug_skips_niche_read(self):
        pool, conn = _make_pool(app_setting_row_value="canonical_blog")
        out = await resolve_template_slug(pool, niche_slug=None)
        assert out == "canonical_blog"
        # Niche read skipped entirely because we had no slug to key off.
        conn.fetchval.assert_not_awaited()

    async def test_niche_read_error_falls_through(self):
        """A DB hiccup on niche lookup must NOT block task creation —
        we walk to the next tier rather than raising."""
        pool, _ = _make_pool(
            niche_raises=True,
            app_setting_row_value="canonical_blog",
        )
        out = await resolve_template_slug(pool, niche_slug="glad-labs")
        assert out == "canonical_blog"


@pytest.mark.unit
class TestAppSettingsTierWins:
    """Tier 3: app_settings.default_template_slug fallback."""

    async def test_app_setting_returned_when_niche_blank(self):
        pool, _ = _make_pool(
            niche_row_value=None,
            app_setting_row_value="canonical_blog",
        )
        out = await resolve_template_slug(pool, niche_slug="some-niche")
        assert out == "canonical_blog"

    async def test_app_setting_trimmed(self):
        pool, _ = _make_pool(
            niche_row_value=None,
            app_setting_row_value="  canonical_blog  ",
        )
        out = await resolve_template_slug(pool, niche_slug=None)
        assert out == "canonical_blog"


@pytest.mark.unit
class TestFailLoud:
    """Tier 4: hard fail when every source is blank."""

    async def test_nothing_resolvable_raises(self):
        pool, _ = _make_pool(
            niche_row_value=None,
            app_setting_row_value=None,
        )
        with pytest.raises(TemplateSlugUnresolvable) as ei:
            await resolve_template_slug(pool, niche_slug="glad-labs")
        # Error message names the niche so the operator can fix the
        # right row instead of guessing.
        assert "glad-labs" in str(ei.value)

    async def test_app_setting_blank_string_raises(self):
        pool, _ = _make_pool(
            niche_row_value="",
            app_setting_row_value="",
        )
        with pytest.raises(TemplateSlugUnresolvable):
            await resolve_template_slug(pool, niche_slug="glad-labs")

    async def test_db_errors_on_all_tiers_still_raise(self):
        """When both tiers fail with transient errors, the resolver
        raises — better to fail loud than to write a NULL row that
        will fail downstream anyway, per feedback_no_silent_defaults."""
        pool, _ = _make_pool(niche_raises=True, app_setting_raises=True)
        with pytest.raises(TemplateSlugUnresolvable):
            await resolve_template_slug(pool, niche_slug="glad-labs")
