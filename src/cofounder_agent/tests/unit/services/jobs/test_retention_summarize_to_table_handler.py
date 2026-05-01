"""Unit tests for ``services/integrations/handlers/retention_summarize_to_table.py``.

Phase B+C of the memory-compression pipeline. These tests exercise the
generic per-day compression handler that aggregates older audit_log /
brain_decisions rows into per-day summary rows + LLM paragraph and
deletes the originals.

Coverage:

1. Required-config validation — ``ttl_days``, ``table_name``,
   ``bucket``, ``summary_table``, ``text_columns``, ``count_columns``.
2. Bad-bucket rejection — only ``"day"`` is supported in v1.
3. Identifier whitelist — non-``[A-Za-z_][A-Za-z0-9_]*`` values raise
   instead of getting interpolated into SQL.
4. Day-bucketing groups rows correctly and produces one summary per day.
5. LLM is called once per bucket (with the right ``source_table`` and
   model name).
6. LLM returning ``None`` falls back to a joined-preview summary; the
   bucket is still written with ``summary_method = 'joined_preview'``.
7. Transactional safety — if the INSERT fails, the DELETE doesn't fire.
8. ``dry_run`` mode counts buckets but doesn't insert/delete.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timezone
from typing import Any
from unittest.mock import patch

import pytest

from services.integrations.handlers import retention_summarize_to_table as mod

# ---------------------------------------------------------------------------
# Fake pool — same shape as ``test_collapse_old_embeddings_job.FakePool``,
# adapted for the summarize_to_table handler's call pattern.
# ---------------------------------------------------------------------------


class FakePool:
    """Minimal asyncpg.Pool stand-in.

    - ``app_settings`` settings come from ``settings`` mapping (read by
      ``mod._get_setting`` via top-level ``fetchrow``).
    - The bucket-discovery and per-bucket SELECTs return rows from
      ``raw_rows`` (a plain list of dicts with an ``id`` and the
      handler-needed columns).
    - INSERT into the summary table is recorded in ``inserted``.
    - DELETE rows are recorded in ``deleted_ids``.
    - ``fail_insert`` makes the INSERT raise so the rollback path can
      be verified (no DELETEs fire when the INSERT fails).
    """

    def __init__(
        self,
        *,
        settings: dict[str, str] | None = None,
        raw_rows: list[dict[str, Any]] | None = None,
        fail_insert: bool = False,
    ):
        self.settings = dict(settings or {})
        self.raw_rows = list(raw_rows or [])
        self.fail_insert = fail_insert
        self.inserted: list[dict[str, Any]] = []
        self.deleted_ids: list[list[Any]] = []
        self.insert_calls = 0
        self.executes: list[tuple[str, tuple[Any, ...]]] = []

    async def fetchrow(self, query: str, *args: Any) -> Any:
        """Settings reads only — handler's _get_setting path."""
        if "app_settings" in query and args:
            key = args[0]
            if key in self.settings:
                return {"value": self.settings[key]}
        return None

    def acquire(self):
        return _AcquireCtx(self)


class _AcquireCtx:
    def __init__(self, pool: FakePool):
        self.pool = pool

    async def __aenter__(self):
        return _RecordingConn(self.pool)

    async def __aexit__(self, *_: Any):
        return False


class _TxCtx:
    def __init__(self, conn: _RecordingConn):
        self.conn = conn

    async def __aenter__(self):
        self.conn.in_tx = True
        self.conn.tx_inserts = []
        self.conn.tx_deletes = []
        return None

    async def __aexit__(self, exc_type, exc, tb):
        if exc_type is not None:
            # Rollback — discard everything the tx touched.
            self.conn.tx_inserts = []
            self.conn.tx_deletes = []
        else:
            # Commit — promote tx-state to pool-level recorders.
            self.conn.pool.inserted.extend(self.conn.tx_inserts)
            self.conn.pool.deleted_ids.extend(self.conn.tx_deletes)
        self.conn.in_tx = False
        return False


class _RecordingConn:
    def __init__(self, pool: FakePool):
        self.pool = pool
        self.in_tx = False
        self.tx_inserts: list[dict[str, Any]] = []
        self.tx_deletes: list[list[Any]] = []

    def transaction(self):
        return _TxCtx(self)

    async def fetch(self, query: str, *args: Any) -> list[dict[str, Any]]:
        if "date_trunc" in query and "GROUP BY" in query:
            # Bucket-discovery query: return one row per distinct day.
            ttl_days = args[0]
            cutoff = datetime.now(timezone.utc).timestamp() - (ttl_days * 86400)
            buckets: dict[datetime, int] = {}
            for r in self.pool.raw_rows:
                age_col = _detect_age_col(r)
                ts = r[age_col]
                if ts.timestamp() >= cutoff:
                    continue
                day = datetime(
                    ts.year, ts.month, ts.day, tzinfo=timezone.utc,
                )
                buckets[day] = buckets.get(day, 0) + 1
            out = [
                {"bucket_start": d, "row_count": c}
                for d, c in sorted(buckets.items())
            ]
            return out
        if "WHERE" in query and "AND" in query:
            # Per-bucket SELECT: WHERE age >= $1 AND age < $2
            bucket_start, bucket_end = args[0], args[1]
            out = []
            for r in self.pool.raw_rows:
                age_col = _detect_age_col(r)
                ts = r[age_col]
                if bucket_start <= ts < bucket_end:
                    out.append(r)
            return out
        return []

    async def fetchrow(self, query: str, *args: Any) -> Any:
        if "INSERT INTO" in query and "RETURNING id" in query:
            self.pool.insert_calls += 1
            if self.pool.fail_insert:
                raise RuntimeError("simulated insert failure")
            row = {"args": args, "id": 9000 + self.pool.insert_calls}
            self.tx_inserts.append(row)
            return {"id": row["id"]}
        return None

    async def execute(self, query: str, *args: Any) -> str:
        self.pool.executes.append((query, args))
        if "DELETE FROM" in query:
            ids = args[0] if args else []
            self.tx_deletes.append(list(ids))
            return f"DELETE {len(ids)}"
        return "OK"


def _detect_age_col(row: dict[str, Any]) -> str:
    """Pick the timestamp column out of a fake row."""
    for c in ("timestamp", "created_at"):
        if c in row:
            return c
    raise KeyError(f"no age column in row keys={list(row)}")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _audit_row(
    row_id: int, ts: datetime, *, event_type: str, severity: str = "info",
    source: str = "brain.alert_sync", details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "id": row_id,
        "timestamp": ts,
        "event_type": event_type,
        "source": source,
        "severity": severity,
        "details": details or {"detail": f"row {row_id}"},
    }


def _audit_policy_row(**overrides: Any) -> dict[str, Any]:
    base = {
        "id": "00000000-0000-0000-0000-000000000128",
        "name": "audit_log",
        "handler_name": "summarize_to_table",
        "table_name": "audit_log",
        "filter_sql": None,
        "age_column": "timestamp",
        "ttl_days": 90,
        "downsample_rule": None,
        "summarize_handler": "summarize_to_table",
        "enabled": True,
        "config": {
            "bucket": "day",
            "summary_table": "audit_log_summaries",
            "text_columns": ["event_type", "source", "severity", "details"],
            "count_columns": ["event_type", "severity"],
            "top_source_column": "source",
            "excerpts_column": "error_excerpts",
            "excerpts_filter": "severity = 'error'",
            "excerpts_text_columns": ["event_type", "source", "details"],
        },
        "metadata": {},
    }
    base.update(overrides)
    return base


def _two_days_of_audit_rows() -> list[dict[str, Any]]:
    """Two distinct day-buckets, one in 2025-01-01 and one in 2025-01-02."""
    day_a = datetime(2025, 1, 1, 12, 0, tzinfo=timezone.utc)
    day_b = datetime(2025, 1, 2, 9, 30, tzinfo=timezone.utc)
    return [
        _audit_row(1, day_a, event_type="probe.ok"),
        _audit_row(2, day_a, event_type="probe.ok", severity="warning"),
        _audit_row(
            3, day_a, event_type="probe.failed", severity="error",
            source="brain.alert_sync", details={"err": "timeout"},
        ),
        _audit_row(4, day_b, event_type="probe.ok"),
        _audit_row(
            5, day_b, event_type="probe.failed", severity="error",
            details={"err": "boom"},
        ),
    ]


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestValidation:
    @pytest.mark.asyncio
    async def test_pool_none_raises(self):
        with pytest.raises(RuntimeError, match="pool unavailable"):
            await mod.summarize_to_table(
                None, site_config=None, row=_audit_policy_row(), pool=None,
            )

    @pytest.mark.asyncio
    async def test_ttl_days_required(self):
        pool = FakePool()
        with pytest.raises(ValueError, match="ttl_days is required"):
            await mod.summarize_to_table(
                None, site_config=None,
                row=_audit_policy_row(ttl_days=None), pool=pool,
            )

    @pytest.mark.asyncio
    async def test_ttl_days_negative_rejected(self):
        pool = FakePool()
        with pytest.raises(ValueError, match=">= 0"):
            await mod.summarize_to_table(
                None, site_config=None,
                row=_audit_policy_row(ttl_days=-3), pool=pool,
            )

    @pytest.mark.asyncio
    async def test_invalid_table_name_rejected(self):
        pool = FakePool()
        with pytest.raises(ValueError, match="table_name"):
            await mod.summarize_to_table(
                None, site_config=None,
                row=_audit_policy_row(table_name="boom; DROP TABLE x;"),
                pool=pool,
            )

    @pytest.mark.asyncio
    async def test_invalid_summary_table_rejected(self):
        pool = FakePool()
        cfg = dict(_audit_policy_row()["config"])
        cfg["summary_table"] = "x; DELETE FROM y;"
        with pytest.raises(ValueError, match="summary_table"):
            await mod.summarize_to_table(
                None, site_config=None,
                row=_audit_policy_row(config=cfg), pool=pool,
            )

    @pytest.mark.asyncio
    async def test_unknown_bucket_rejected(self):
        pool = FakePool()
        cfg = dict(_audit_policy_row()["config"])
        cfg["bucket"] = "hour"
        with pytest.raises(ValueError, match="bucket"):
            await mod.summarize_to_table(
                None, site_config=None,
                row=_audit_policy_row(config=cfg), pool=pool,
            )

    @pytest.mark.asyncio
    async def test_text_columns_required(self):
        pool = FakePool()
        cfg = dict(_audit_policy_row()["config"])
        cfg["text_columns"] = []
        with pytest.raises(ValueError, match="text_columns"):
            await mod.summarize_to_table(
                None, site_config=None,
                row=_audit_policy_row(config=cfg), pool=pool,
            )

    @pytest.mark.asyncio
    async def test_count_columns_required(self):
        pool = FakePool()
        cfg = dict(_audit_policy_row()["config"])
        cfg["count_columns"] = []
        with pytest.raises(ValueError, match="count_columns"):
            await mod.summarize_to_table(
                None, site_config=None,
                row=_audit_policy_row(config=cfg), pool=pool,
            )

    @pytest.mark.asyncio
    async def test_invalid_text_column_identifier_rejected(self):
        pool = FakePool()
        cfg = dict(_audit_policy_row()["config"])
        cfg["text_columns"] = ["event_type", "x; drop table z;"]
        with pytest.raises(ValueError, match="text_columns"):
            await mod.summarize_to_table(
                None, site_config=None,
                row=_audit_policy_row(config=cfg), pool=pool,
            )


# ---------------------------------------------------------------------------
# Bucket math + simple-equality filter parser
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestPureFunctions:
    def test_day_bucket_naive_utcified(self):
        ts = datetime(2025, 6, 4, 14, 30, 0)
        start, end = mod._day_bucket(ts)
        assert start == datetime(2025, 6, 4, tzinfo=timezone.utc)
        assert end == datetime(2025, 6, 5, tzinfo=timezone.utc)

    def test_day_bucket_already_aware(self):
        ts = datetime(2025, 6, 4, 23, 59, tzinfo=timezone.utc)
        start, end = mod._day_bucket(ts)
        assert start.day == 4
        assert end.day == 5

    def test_simple_eq_filter_parses(self):
        assert mod._parse_simple_eq_filter("severity = 'error'") == (
            "severity", "error",
        )

    def test_simple_eq_filter_rejects_complex(self):
        # Anything beyond `col = '<lit>'` returns None
        assert mod._parse_simple_eq_filter(
            "severity = 'error' OR foo = 'bar'"
        ) is None
        assert mod._parse_simple_eq_filter("") is None
        assert mod._parse_simple_eq_filter("severity > 1") is None

    def test_validate_identifier_list_strict(self):
        with pytest.raises(ValueError):
            mod._validate_identifier_list(["good", "bad name"], "field")
        assert mod._validate_identifier_list(["a", "b_c"], "field") == ["a", "b_c"]


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestAggregate:
    def test_counts_and_top_sources(self):
        rows = _two_days_of_audit_rows()[:3]  # day_a only
        agg = mod._aggregate(
            rows,
            count_columns=["event_type", "severity"],
            top_source_column="source",
            confidence_column=None,
            excerpts_filter="",
            excerpts_text_columns=["event_type"],
            excerpts_per_bucket=10,
        )
        assert agg["counts"]["event_type"] == {"probe.ok": 2, "probe.failed": 1}
        assert agg["counts"]["severity"] == {
            "info": 1, "warning": 1, "error": 1,
        }
        assert any(
            s["value"] == "brain.alert_sync" and s["count"] == 3
            for s in agg["top_sources"]
        )

    def test_excerpts_filter_picks_only_errors(self):
        rows = _two_days_of_audit_rows()[:3]
        agg = mod._aggregate(
            rows,
            count_columns=["event_type"],
            top_source_column=None,
            confidence_column=None,
            excerpts_filter="severity = 'error'",
            excerpts_text_columns=["event_type", "details"],
            excerpts_per_bucket=10,
        )
        # Only one row matched the filter
        assert len(agg["excerpts"]) == 1
        assert agg["excerpts"][0]["event_type"] == "probe.failed"

    def test_avg_confidence(self):
        rows = [
            {"id": 1, "decision": "x", "confidence": 0.8},
            {"id": 2, "decision": "y", "confidence": 0.6},
            {"id": 3, "decision": "z", "confidence": None},
        ]
        agg = mod._aggregate(
            rows,
            count_columns=["decision"],
            top_source_column=None,
            confidence_column="confidence",
            excerpts_filter="",
            excerpts_text_columns=["decision"],
            excerpts_per_bucket=10,
        )
        assert agg["avg_confidence"] == pytest.approx(0.7)


# ---------------------------------------------------------------------------
# End-to-end happy-path: bucketing + LLM call + summary write + delete
# ---------------------------------------------------------------------------


class _RecordingLLM:
    """Stand-in for build_summary_text_via_llm."""

    def __init__(self, returns: str | None = "LLM-summary-text"):
        self.returns = returns
        self.calls: list[dict[str, Any]] = []

    async def __call__(
        self,
        previews: Sequence[str],
        *,
        source_table: str,
        model: str,
        timeout_s: int,
        prompt_template: str | None = None,
    ) -> str | None:
        self.calls.append({
            "previews": list(previews),
            "source_table": source_table,
            "model": model,
            "timeout_s": timeout_s,
            "prompt_template": prompt_template,
        })
        return self.returns


@pytest.mark.unit
class TestEndToEnd:
    @pytest.mark.asyncio
    async def test_two_buckets_one_summary_each(self):
        pool = FakePool(raw_rows=_two_days_of_audit_rows())
        llm = _RecordingLLM(returns="dense day summary")
        with patch.object(mod, "build_summary_text_via_llm", llm):
            result = await mod.summarize_to_table(
                None, site_config=None,
                row=_audit_policy_row(), pool=pool,
            )

        assert result["buckets"] == 2
        assert result["summarized"] == 2
        assert result["deleted"] == 5
        # One LLM call per bucket
        assert len(llm.calls) == 2
        # source_table flows through to the LLM helper unchanged
        assert all(c["source_table"] == "audit_log" for c in llm.calls)
        # Two summary rows written, all 5 raw rows deleted
        assert len(pool.inserted) == 2
        assert sum(len(d) for d in pool.deleted_ids) == 5

    @pytest.mark.asyncio
    async def test_falls_back_to_joined_preview_when_llm_returns_none(self):
        pool = FakePool(raw_rows=_two_days_of_audit_rows())
        llm = _RecordingLLM(returns=None)  # LLM unavailable
        with patch.object(mod, "build_summary_text_via_llm", llm):
            result = await mod.summarize_to_table(
                None, site_config=None,
                row=_audit_policy_row(), pool=pool,
            )

        assert result["summarized"] == 2
        # Each insert's args include the summary_method as the last
        # text-typed positional arg. The handler appends
        # summary_text + summary_method last, so check both inserted
        # rows used joined_preview.
        for ins in pool.inserted:
            args = ins["args"]
            # Last two args are (summary_text, summary_method)
            assert args[-1] == "joined_preview"

    @pytest.mark.asyncio
    async def test_uses_settings_for_model_and_timeout(self):
        pool = FakePool(
            raw_rows=_two_days_of_audit_rows(),
            settings={
                "memory_compression_summary_model": "test-model:7b",
                "memory_compression_summary_timeout_seconds": "42",
            },
        )
        llm = _RecordingLLM(returns="ok")
        with patch.object(mod, "build_summary_text_via_llm", llm):
            await mod.summarize_to_table(
                None, site_config=None,
                row=_audit_policy_row(), pool=pool,
            )
        assert llm.calls
        assert llm.calls[0]["model"] == "test-model:7b"
        assert llm.calls[0]["timeout_s"] == 42

    @pytest.mark.asyncio
    async def test_dry_run_counts_buckets_no_writes(self):
        pool = FakePool(raw_rows=_two_days_of_audit_rows())
        cfg = dict(_audit_policy_row()["config"])
        cfg["dry_run"] = True
        llm = _RecordingLLM(returns="should-not-be-called")
        with patch.object(mod, "build_summary_text_via_llm", llm):
            result = await mod.summarize_to_table(
                None, site_config=None,
                row=_audit_policy_row(config=cfg), pool=pool,
            )
        assert result == {
            "dry_run": True,
            "buckets": 2,
            "summarized": 0,
            "deleted": 0,
        }
        assert pool.inserted == []
        assert pool.deleted_ids == []
        # LLM is not called in dry mode
        assert llm.calls == []

    @pytest.mark.asyncio
    async def test_no_buckets_returns_empty_result(self):
        # Rows are too recent — nothing older than ttl_days
        recent = datetime.now(timezone.utc)
        pool = FakePool(raw_rows=[
            _audit_row(1, recent, event_type="probe.ok"),
        ])
        llm = _RecordingLLM(returns="x")
        with patch.object(mod, "build_summary_text_via_llm", llm):
            result = await mod.summarize_to_table(
                None, site_config=None,
                row=_audit_policy_row(), pool=pool,
            )
        assert result == {
            "summarized": 0, "deleted": 0, "buckets": 0, "skipped": [],
        }
        assert llm.calls == []


# ---------------------------------------------------------------------------
# Transactional safety: failed INSERT must NOT trigger DELETE
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestTransactionalSafety:
    @pytest.mark.asyncio
    async def test_failed_insert_skips_delete(self):
        pool = FakePool(
            raw_rows=_two_days_of_audit_rows(),
            fail_insert=True,
        )
        llm = _RecordingLLM(returns="ok")
        with patch.object(mod, "build_summary_text_via_llm", llm):
            result = await mod.summarize_to_table(
                None, site_config=None,
                row=_audit_policy_row(), pool=pool,
            )
        # The handler caught the per-bucket failure and continued; both
        # buckets failed so summarized=0 and skipped lists both.
        assert result["summarized"] == 0
        assert result["deleted"] == 0
        assert len(result["skipped"]) == 2
        # No DELETE landed at the pool level (transaction rollback
        # cleared tx_deletes before they could be promoted).
        assert pool.deleted_ids == []
        assert pool.inserted == []


# ---------------------------------------------------------------------------
# Handler registration — paired with __init__.load_all wiring
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRegistration:
    def test_handler_is_registered(self):
        from services.integrations import registry
        # Importing the module triggers the @register_handler decorator.
        # If load_all() or a direct import has run, this lookup succeeds.
        h = registry.lookup("retention", "summarize_to_table")
        assert h is mod.summarize_to_table
