"""Unit tests for the retention framework (Phase B / GH-110)."""

from __future__ import annotations

from typing import Any

import pytest

from services.integrations import registry as registry_module
from services.integrations import retention_runner
from services.integrations.handlers import retention_downsample, retention_ttl_prune


class _FakeConn:
    def __init__(self, pool):
        self._pool = pool

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        pass

    async def execute(self, query, *args):
        self._pool.executes.append((query, args))
        # Simulate DELETE N / INSERT N result strings for the handler tests.
        if query.strip().upper().startswith("DELETE"):
            return f"DELETE {self._pool.next_delete_count}"
        if query.strip().upper().startswith("INSERT"):
            return f"INSERT 0 {self._pool.next_insert_count}"
        return "UPDATE 1"

    async def fetchval(self, query, *args):
        return self._pool.next_fetchval

    async def fetch(self, query, *args):
        return self._pool.next_fetch


class _FakePool:
    def __init__(self):
        self.executes: list[tuple[str, tuple]] = []
        self.next_fetchval: Any = None
        self.next_fetch: list[dict[str, Any]] = []
        self.next_delete_count = 0
        self.next_insert_count = 0

    def acquire(self):
        return _FakeConn(self)


def _policy(**overrides):
    base = {
        "id": "00000000-0000-0000-0000-000000000020",
        "name": "test_policy",
        "handler_name": "ttl_prune",
        "table_name": "some_table",
        "filter_sql": None,
        "age_column": "created_at",
        "ttl_days": 30,
        "downsample_rule": None,
        "summarize_handler": None,
        "enabled": True,
        "config": {},
        "metadata": {},
    }
    base.update(overrides)
    return base


@pytest.fixture(autouse=True)
def _isolation():
    saved = dict(registry_module._REGISTRY)
    registry_module._REGISTRY.clear()
    # Re-register the real retention handlers for tests that exercise them.
    registry_module._REGISTRY["retention.ttl_prune"] = retention_ttl_prune.ttl_prune
    registry_module._REGISTRY["retention.downsample"] = retention_downsample.downsample
    yield
    registry_module._REGISTRY.clear()
    registry_module._REGISTRY.update(saved)


# ---------------------------------------------------------------------------
# ttl_prune handler
# ---------------------------------------------------------------------------


class TestTtlPrune:
    @pytest.mark.asyncio
    async def test_dry_run_counts_without_deleting(self):
        pool = _FakePool()
        pool.next_fetchval = 123
        result = await retention_ttl_prune.ttl_prune(
            None,
            site_config=None,
            row=_policy(config={"dry_run": True}),
            pool=pool,
        )
        assert result == {"dry_run": True, "would_delete": 123, "deleted": 0}
        # Only the SELECT COUNT(*) query was executed, no DELETE
        assert not any("DELETE" in q.upper() for q, _ in pool.executes)

    @pytest.mark.asyncio
    async def test_happy_path_loops_until_batch_exhausted(self):
        pool = _FakePool()
        # First two deletes hit the batch_size cap (10000), third returns
        # fewer than batch_size -> loop exits. The simple _FakePool
        # returns a constant count, so we simulate termination by lowering
        # the returned count below batch_size after the first iteration.
        # Simplest: return < batch_size so the loop exits immediately.
        pool.next_delete_count = 42
        result = await retention_ttl_prune.ttl_prune(
            None, site_config=None, row=_policy(), pool=pool,
        )
        assert result["deleted"] == 42
        assert result["table"] == "some_table"
        assert result["ttl_days"] == 30

    @pytest.mark.asyncio
    async def test_filter_sql_is_injected_into_where_clause(self):
        pool = _FakePool()
        pool.next_delete_count = 5
        await retention_ttl_prune.ttl_prune(
            None,
            site_config=None,
            row=_policy(
                table_name="embeddings",
                filter_sql="source_table = 'claude_sessions'",
            ),
            pool=pool,
        )
        # Inspect the DELETE query that ran
        delete_queries = [q for q, _ in pool.executes if "DELETE" in q.upper()]
        assert delete_queries
        assert "source_table = 'claude_sessions'" in delete_queries[0]

    @pytest.mark.asyncio
    async def test_ttl_days_required(self):
        pool = _FakePool()
        with pytest.raises(ValueError, match="ttl_days is required"):
            await retention_ttl_prune.ttl_prune(
                None, site_config=None, row=_policy(ttl_days=None), pool=pool,
            )

    @pytest.mark.asyncio
    async def test_ttl_days_negative_rejected(self):
        pool = _FakePool()
        with pytest.raises(ValueError, match=">= 0"):
            await retention_ttl_prune.ttl_prune(
                None, site_config=None, row=_policy(ttl_days=-1), pool=pool,
            )

    @pytest.mark.asyncio
    async def test_invalid_table_name_rejected(self):
        pool = _FakePool()
        with pytest.raises(ValueError, match="table_name"):
            await retention_ttl_prune.ttl_prune(
                None, site_config=None,
                row=_policy(table_name="has; drop table x;"),
                pool=pool,
            )

    @pytest.mark.asyncio
    async def test_pool_none_raises(self):
        with pytest.raises(RuntimeError, match="pool unavailable"):
            await retention_ttl_prune.ttl_prune(
                None, site_config=None, row=_policy(), pool=None,
            )


# ---------------------------------------------------------------------------
# downsample handler
# ---------------------------------------------------------------------------


class TestDownsample:
    _RULE = {
        "keep_raw_days": 30,
        "rollup_table": "gpu_metrics_hourly",
        "rollup_interval": "1 hour",
        "aggregations": [
            {"col": "utilization_pct", "fn": "avg", "as": "avg_utilization"},
            {"col": "power_watts", "fn": "max", "as": "peak_power"},
        ],
    }

    @pytest.mark.asyncio
    async def test_empty_source_short_circuits(self):
        pool = _FakePool()
        pool.next_fetchval = 0
        result = await retention_downsample.downsample(
            None,
            site_config=None,
            row=_policy(
                handler_name="downsample",
                table_name="gpu_metrics",
                age_column="sampled_at",
                ttl_days=None,
                downsample_rule=self._RULE,
            ),
            pool=pool,
        )
        assert result == {
            "rolled_up": 0, "deleted": 0, "bucket_interval": "1 hour",
        }

    @pytest.mark.asyncio
    async def test_dry_run(self):
        pool = _FakePool()
        pool.next_fetchval = 500
        result = await retention_downsample.downsample(
            None, site_config=None,
            row=_policy(
                handler_name="downsample",
                table_name="gpu_metrics",
                age_column="sampled_at",
                ttl_days=None,
                downsample_rule=self._RULE,
                config={"dry_run": True},
            ),
            pool=pool,
        )
        assert result["dry_run"] is True
        assert result["would_affect"] == 500

    @pytest.mark.asyncio
    async def test_happy_path(self):
        pool = _FakePool()
        pool.next_fetchval = 720
        pool.next_insert_count = 24
        pool.next_delete_count = 720
        result = await retention_downsample.downsample(
            None, site_config=None,
            row=_policy(
                handler_name="downsample",
                table_name="gpu_metrics",
                age_column="sampled_at",
                ttl_days=None,
                downsample_rule=self._RULE,
            ),
            pool=pool,
        )
        assert result["rolled_up"] == 24
        assert result["deleted"] == 720

    @pytest.mark.asyncio
    async def test_invalid_fn_rejected(self):
        pool = _FakePool()
        pool.next_fetchval = 100
        bad = {**self._RULE, "aggregations": [{"col": "x", "fn": "median"}]}
        with pytest.raises(ValueError, match="median"):
            await retention_downsample.downsample(
                None, site_config=None,
                row=_policy(
                    handler_name="downsample",
                    table_name="gpu_metrics",
                    ttl_days=None,
                    downsample_rule=bad,
                ),
                pool=pool,
            )

    @pytest.mark.asyncio
    async def test_invalid_interval_rejected(self):
        pool = _FakePool()
        bad = {**self._RULE, "rollup_interval": "1 parsec"}
        with pytest.raises(ValueError, match="rollup_interval"):
            await retention_downsample.downsample(
                None, site_config=None,
                row=_policy(
                    handler_name="downsample",
                    table_name="gpu_metrics",
                    ttl_days=None,
                    downsample_rule=bad,
                ),
                pool=pool,
            )


# ---------------------------------------------------------------------------
# retention_runner
# ---------------------------------------------------------------------------


class _StubHandler:
    def __init__(self, returning: dict[str, Any] | None = None, raises: Exception | None = None):
        self.returning = returning or {}
        self.raises = raises
        self.calls = 0

    async def __call__(self, payload, *, site_config, row, pool):
        self.calls += 1
        if self.raises:
            raise self.raises
        return dict(self.returning)


class TestRunAll:
    @pytest.mark.asyncio
    async def test_empty_when_no_enabled_rows(self):
        pool = _FakePool()
        pool.next_fetch = []
        summary = await retention_runner.run_all(pool)
        assert summary.policies == []
        assert summary.total_deleted == 0
        assert summary.total_failed == 0

    @pytest.mark.asyncio
    async def test_aggregates_per_policy_counts(self):
        stub = _StubHandler(returning={"deleted": 15, "summarized": 0})
        registry_module._REGISTRY["retention.ttl_prune"] = stub

        pool = _FakePool()
        pool.next_fetch = [_policy(name="policy_a"), _policy(name="policy_b")]

        summary = await retention_runner.run_all(pool)
        assert len(summary.policies) == 2
        assert stub.calls == 2
        assert summary.total_deleted == 30
        assert all(p.ok for p in summary.policies)

    @pytest.mark.asyncio
    async def test_isolates_per_policy_failures(self):
        good = _StubHandler(returning={"deleted": 7})
        bad = _StubHandler(raises=RuntimeError("disk full"))
        # Different handler_names for clarity
        registry_module._REGISTRY["retention.ttl_prune"] = good
        registry_module._REGISTRY["retention.downsample"] = bad

        pool = _FakePool()
        pool.next_fetch = [
            _policy(name="policy_ok", handler_name="ttl_prune"),
            _policy(name="policy_bad", handler_name="downsample"),
        ]

        summary = await retention_runner.run_all(pool)
        assert summary.total_deleted == 7
        assert summary.total_failed == 1
        names_ok = [p.name for p in summary.policies if p.ok]
        names_bad = [p.name for p in summary.policies if not p.ok]
        assert names_ok == ["policy_ok"]
        assert names_bad == ["policy_bad"]

    @pytest.mark.asyncio
    async def test_only_names_filters(self):
        pool = _FakePool()
        pool.next_fetch = [_policy(name="x")]
        summary = await retention_runner.run_all(pool, only_names=["x"])
        assert len(summary.policies) == 1
