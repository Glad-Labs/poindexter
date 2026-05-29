"""Unit tests for services/retention_janitor.py.

Created 2026-05-29 (SiteConfig DI migration #272 leaf batch 3) when the
module was converted from free functions (``run_once`` / ``run_forever``)
+ a module-level ``site_config`` singleton to a ``RetentionJanitor`` class
with constructor DI. The class exposes:

- ``run_once(pool)`` — single pruning pass over every janitor target
- ``run_forever(pool)`` — background loop (interval from SiteConfig)

Tests construct ``RetentionJanitor(site_config=SiteConfig(initial_config=...))``
directly with a real (empty/seeded) SiteConfig — zero shared module state.
A mock asyncpg pool stands in for the DB so no Postgres is required.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from services.retention_janitor import _JANITOR_TARGETS, RetentionJanitor
from services.site_config import SiteConfig


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_pool(execute_return: str = "DELETE 0") -> MagicMock:
    """Build a mock asyncpg pool whose ``acquire()`` yields a conn whose
    ``execute`` returns the given asyncpg status string."""
    conn = MagicMock()
    conn.execute = AsyncMock(return_value=execute_return)
    acm = MagicMock()
    acm.__aenter__ = AsyncMock(return_value=conn)
    acm.__aexit__ = AsyncMock(return_value=False)
    pool = MagicMock()
    pool.acquire = MagicMock(return_value=acm)
    return pool, conn


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


class TestRetentionJanitorConstruction:
    def test_constructs_with_site_config_kwarg(self):
        janitor = RetentionJanitor(site_config=SiteConfig())
        assert janitor is not None

    def test_site_config_is_required(self):
        """No default — passing nothing must raise per fail-loud principle."""
        with pytest.raises(TypeError):
            RetentionJanitor()  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# retention-window resolution
# ---------------------------------------------------------------------------


class TestRetentionDaysFor:
    def test_uses_default_when_key_unset(self):
        janitor = RetentionJanitor(site_config=SiteConfig())
        assert janitor._retention_days_for("gpu_metrics", 90) == 90

    def test_reads_operator_override_from_site_config(self):
        janitor = RetentionJanitor(
            site_config=SiteConfig(initial_config={"retention_days__gpu_metrics": "7"})
        )
        assert janitor._retention_days_for("gpu_metrics", 90) == 7

    def test_non_integer_value_falls_back_to_default(self):
        janitor = RetentionJanitor(
            site_config=SiteConfig(initial_config={"retention_days__gpu_metrics": "not-a-number"})
        )
        assert janitor._retention_days_for("gpu_metrics", 90) == 90


# ---------------------------------------------------------------------------
# _prune_one
# ---------------------------------------------------------------------------


class TestPruneOne:
    @pytest.mark.asyncio
    async def test_zero_days_skips_without_querying(self):
        janitor = RetentionJanitor(site_config=SiteConfig())
        pool, conn = _mock_pool()
        deleted = await janitor._prune_one(pool, "gpu_metrics", "timestamp", 0)
        assert deleted == 0
        conn.execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_parses_deleted_row_count(self):
        janitor = RetentionJanitor(site_config=SiteConfig())
        pool, conn = _mock_pool(execute_return="DELETE 42")
        deleted = await janitor._prune_one(pool, "gpu_metrics", "timestamp", 90)
        assert deleted == 42
        # Table + column + window are interpolated into the SQL.
        sql = conn.execute.call_args.args[0]
        assert "DELETE FROM gpu_metrics" in sql
        assert "timestamp <" in sql
        assert "90 days" in sql

    @pytest.mark.asyncio
    async def test_unparseable_status_returns_zero(self):
        janitor = RetentionJanitor(site_config=SiteConfig())
        pool, _conn = _mock_pool(execute_return="WAT")
        deleted = await janitor._prune_one(pool, "gpu_metrics", "timestamp", 90)
        assert deleted == 0


# ---------------------------------------------------------------------------
# run_once
# ---------------------------------------------------------------------------


class TestRunOnce:
    @pytest.mark.asyncio
    async def test_prunes_every_target_and_returns_counts(self):
        janitor = RetentionJanitor(site_config=SiteConfig())
        pool, _conn = _mock_pool(execute_return="DELETE 3")
        results = await janitor.run_once(pool)
        # Every whitelisted target is represented in the result map.
        assert set(results) == {t[0] for t in _JANITOR_TARGETS}
        # Each non-skipped table reported 3 deletions.
        assert all(v == 3 for v in results.values())

    @pytest.mark.asyncio
    async def test_single_table_failure_does_not_tank_cycle(self):
        janitor = RetentionJanitor(site_config=SiteConfig())
        conn = MagicMock()
        # First execute raises, the rest succeed.
        conn.execute = AsyncMock(side_effect=[RuntimeError("boom")] + ["DELETE 1"] * 50)
        acm = MagicMock()
        acm.__aenter__ = AsyncMock(return_value=conn)
        acm.__aexit__ = AsyncMock(return_value=False)
        pool = MagicMock()
        pool.acquire = MagicMock(return_value=acm)

        results = await janitor.run_once(pool)
        # The failing table is tagged -1; the cycle still covered the rest.
        assert any(v == -1 for v in results.values())
        assert any(v == 1 for v in results.values())
        assert set(results) == {t[0] for t in _JANITOR_TARGETS}


# ---------------------------------------------------------------------------
# Container wiring (#272 leaf batch 3: cached_property on AppContainer)
# ---------------------------------------------------------------------------


class TestAppContainerWiring:
    """``AppContainer.retention_janitor`` returns a memoised RetentionJanitor."""

    def test_app_container_exposes_retention_janitor(self):
        from services.container import AppContainer

        container = AppContainer(site_config=SiteConfig(), pool=MagicMock())
        janitor = container.retention_janitor
        assert isinstance(janitor, RetentionJanitor)

    def test_cached_property_memoises(self):
        from services.container import AppContainer

        container = AppContainer(site_config=SiteConfig(), pool=MagicMock())
        assert container.retention_janitor is container.retention_janitor
