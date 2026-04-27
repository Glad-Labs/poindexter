"""Unit tests for ``services/jobs/prune_stale_embeddings.py`` (#106).

Fake asyncpg pool keyed on SQL prefixes — same pattern as
``test_approval_service.py``. Covers TTL filtering, the empty-string
"no TTL" sentinel, partial failure isolation, and the metrics shape
the Grafana panel will read.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from services.jobs.prune_stale_embeddings import (
    PruneStaleEmbeddingsJob,
    _parse_days,
)


# ---------------------------------------------------------------------------
# Fake pool — minimal in-memory store for embeddings + app_settings
# ---------------------------------------------------------------------------


class FakeConnection:
    def __init__(self, store: "FakeStore") -> None:
        self._store = store

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, sql: str, *args):
        sql_norm = " ".join(sql.split())
        if sql_norm.startswith("DELETE FROM embeddings WHERE source_table = $1"):
            source_table, cutoff = args
            if self._store.boom_on_source == source_table:
                raise RuntimeError("simulated DB error")
            before = len(self._store.embeddings)
            # SQL filters out is_summary=TRUE rows so the prune never
            # removes a CollapseOldEmbeddingsJob-written summary.
            self._store.embeddings = [
                e for e in self._store.embeddings
                if not (
                    e["source_table"] == source_table
                    and e["created_at"] < cutoff
                    and not e.get("is_summary", False)
                )
            ]
            deleted = before - len(self._store.embeddings)
            return f"DELETE {deleted}"
        raise AssertionError(
            f"FakeConnection.execute saw unexpected SQL: {sql_norm[:80]}"
        )

    async def fetch(self, sql: str, *args):
        sql_norm = " ".join(sql.split())
        if sql_norm.startswith("SELECT key, value FROM app_settings"):
            (prefix_pat,) = args
            prefix = prefix_pat.rstrip("%")
            return [
                {"key": k, "value": v}
                for k, v in self._store.app_settings.items()
                if k.startswith(prefix)
            ]
        raise AssertionError(
            f"FakeConnection.fetch saw unexpected SQL: {sql_norm[:80]}"
        )


class FakeStore:
    def __init__(self) -> None:
        self.embeddings: list[dict[str, Any]] = []
        self.app_settings: dict[str, str] = {}
        # When set, _prune_one_source raises for that source_table —
        # used to verify per-source failure isolation.
        self.boom_on_source: str | None = None


class FakePool:
    def __init__(self) -> None:
        self.store = FakeStore()

    def acquire(self):
        return FakeConnection(self.store)


def _embed(source_table: str, source_id: str, age_days: int) -> dict[str, Any]:
    return {
        "source_table": source_table,
        "source_id": source_id,
        "created_at": datetime.now(timezone.utc) - timedelta(days=age_days),
    }


@pytest.fixture
def fake_pool():
    return FakePool()


# ---------------------------------------------------------------------------
# _parse_days
# ---------------------------------------------------------------------------


class TestParseDays:
    def test_int_string(self):
        assert _parse_days("21") == 21

    def test_int_native(self):
        assert _parse_days(21) == 21

    def test_empty_returns_none(self):
        assert _parse_days("") is None

    def test_whitespace_returns_none(self):
        assert _parse_days("   ") is None

    def test_none_returns_none(self):
        assert _parse_days(None) is None

    def test_zero_returns_none(self):
        # Zero or negative is a "no TTL" guard — never delete.
        assert _parse_days("0") is None

    def test_negative_returns_none(self):
        assert _parse_days("-5") is None

    def test_non_numeric_returns_none(self):
        assert _parse_days("abc") is None


# ---------------------------------------------------------------------------
# PruneStaleEmbeddingsJob.run
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestRun:
    async def test_no_settings_returns_ok_with_zero(self, fake_pool):
        result = await PruneStaleEmbeddingsJob().run(pool=fake_pool, config={})
        assert result.ok is True
        assert result.changes_made == 0
        assert "nothing to prune" in result.detail

    async def test_prunes_only_old_rows(self, fake_pool):
        fake_pool.store.app_settings = {
            "embedding_retention_days.audit": "30",
        }
        fake_pool.store.embeddings = [
            _embed("audit", "1", age_days=10),    # within window — keep
            _embed("audit", "2", age_days=45),    # past window — drop
            _embed("audit", "3", age_days=100),   # past window — drop
            _embed("audit", "4", age_days=29),    # boundary — keep
        ]
        result = await PruneStaleEmbeddingsJob().run(pool=fake_pool, config={})
        assert result.ok is True
        assert result.changes_made == 2
        assert result.metrics["per_table"] == {"audit": 2}
        assert result.metrics["ttl_days"] == {"audit": 30}
        # Only fresh rows survive.
        survivors = sorted(e["source_id"] for e in fake_pool.store.embeddings)
        assert survivors == ["1", "4"]

    async def test_skips_source_with_empty_ttl(self, fake_pool):
        fake_pool.store.app_settings = {
            "embedding_retention_days.posts": "",  # empty = no TTL
            "embedding_retention_days.audit": "30",
        }
        fake_pool.store.embeddings = [
            _embed("posts", "p1", age_days=400),  # MUST stay despite age
            _embed("audit", "a1", age_days=400),  # gets pruned
        ]
        result = await PruneStaleEmbeddingsJob().run(pool=fake_pool, config={})
        assert result.changes_made == 1
        assert "posts" in result.metrics["skipped"]
        assert "audit" in result.metrics["per_table"]
        # The protected row survived.
        assert any(
            e["source_table"] == "posts" for e in fake_pool.store.embeddings
        )

    async def test_per_source_failure_does_not_kill_job(self, fake_pool):
        fake_pool.store.app_settings = {
            "embedding_retention_days.broken": "30",
            "embedding_retention_days.audit": "30",
        }
        fake_pool.store.embeddings = [
            _embed("broken", "x", age_days=400),
            _embed("audit", "y", age_days=400),
        ]
        fake_pool.store.boom_on_source = "broken"
        result = await PruneStaleEmbeddingsJob().run(pool=fake_pool, config={})
        # Job still ok — audit got pruned despite broken's failure.
        assert result.ok is True
        assert result.metrics["per_table"] == {"audit": 1}
        assert "broken" not in result.metrics["per_table"]

    async def test_metrics_shape_for_grafana(self, fake_pool):
        fake_pool.store.app_settings = {
            "embedding_retention_days.audit": "30",
            "embedding_retention_days.brain": "365",
            "embedding_retention_days.posts": "",
        }
        fake_pool.store.embeddings = [
            _embed("audit", "1", age_days=400),
            _embed("brain", "2", age_days=400),
            _embed("posts", "3", age_days=400),
        ]
        result = await PruneStaleEmbeddingsJob().run(pool=fake_pool, config={})
        m = result.metrics
        assert set(m.keys()) == {
            "per_table", "skipped", "ttl_days", "total_pruned",
        }
        assert m["total_pruned"] == 2
        assert m["ttl_days"] == {"audit": 30, "brain": 365}
        assert m["skipped"] == ["posts"]

    async def test_summary_rows_protected_from_ttl(self, fake_pool):
        """Summary rows (is_summary=TRUE) must survive TTL pruning.

        CollapseOldEmbeddingsJob writes summaries that distill old
        raw rows. The whole point is to preserve semantic context
        after the originals age out — pruning summaries by TTL would
        defeat that. Both raw and summary rows of the same age must
        coexist when only raw is targeted.
        """
        fake_pool.store.app_settings = {
            "embedding_retention_days.audit": "30",
        }
        fake_pool.store.embeddings = [
            _embed("audit", "raw_old", age_days=400),
            {**_embed("audit", "summary_old", age_days=400), "is_summary": True},
        ]
        result = await PruneStaleEmbeddingsJob().run(pool=fake_pool, config={})
        # Only the raw row dropped; the summary row survived.
        assert result.changes_made == 1
        survivors = sorted(e["source_id"] for e in fake_pool.store.embeddings)
        assert survivors == ["summary_old"]

    async def test_invalid_ttl_treated_as_no_ttl(self, fake_pool):
        fake_pool.store.app_settings = {
            "embedding_retention_days.audit": "not-a-number",
        }
        fake_pool.store.embeddings = [
            _embed("audit", "1", age_days=400),
        ]
        result = await PruneStaleEmbeddingsJob().run(pool=fake_pool, config={})
        # Invalid value = treat as no-TTL = skip = no deletes.
        assert result.changes_made == 0
        assert "audit" in result.metrics["skipped"]


# ---------------------------------------------------------------------------
# Job protocol metadata
# ---------------------------------------------------------------------------


class TestProtocol:
    def test_required_attrs(self):
        job = PruneStaleEmbeddingsJob()
        assert job.name == "prune_stale_embeddings"
        assert job.idempotent is True
        # Cron expression — apscheduler-friendly.
        assert job.schedule == "17 3 * * *"
        assert isinstance(job.description, str) and len(job.description) > 20
