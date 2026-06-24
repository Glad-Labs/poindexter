"""Tests for the embeddings_collapse retention handler."""

from __future__ import annotations

import json
from typing import Any

import pytest


# ---------------------------------------------------------------------------
# Helpers — minimal asyncpg pool substitute
# ---------------------------------------------------------------------------


def _vec(direction: str, jitter: float = 0.0, dim: int = 8) -> list[float]:
    """Two distinguishable direction vectors for k-means tests."""
    base = [0.0] * dim
    if direction == "A":
        base[0] = 1.0 + jitter
        base[1] = jitter * 0.1
    else:  # "B"
        base[dim - 1] = 1.0 + jitter
        base[dim - 2] = jitter * 0.1
    return base


def _vec_str(v: list[float]) -> str:
    return "[" + ",".join(repr(float(x)) for x in v) + "]"


def _embedding_row(
    row_id: int,
    source_id: str,
    vector: list[float],
    *,
    preview: str | None = None,
) -> dict[str, Any]:
    return {
        "id": row_id,
        "source_id": source_id,
        "text_preview": preview or f"preview for {source_id}",
        "metadata": json.dumps({"origin": "test"}),
        "embedding": _vec_str(vector),
        "embedding_model": "nomic-embed-text",
        "writer": "test",
        "origin_path": None,
    }


class _TxCtx:
    def __init__(self, conn: "_RecordingConn"):
        self.conn = conn

    async def __aenter__(self):
        self.conn.in_tx = True
        self.conn.tx_deletes = []
        self.conn.tx_inserts = []
        return None

    async def __aexit__(self, exc_type, exc, tb):
        if exc_type is not None:
            self.conn.tx_deletes = []
            self.conn.tx_inserts = []
        else:
            self.conn.pool.inserted.extend(self.conn.tx_inserts)
            self.conn.pool.deleted_ids.extend(self.conn.tx_deletes)
        self.conn.in_tx = False
        return False


class _RecordingConn:
    def __init__(self, pool: "FakePool"):
        self.pool = pool
        self.in_tx = False
        self.tx_inserts: list[dict[str, Any]] = []
        self.tx_deletes: list[int] = []

    def transaction(self) -> _TxCtx:
        return _TxCtx(self)

    async def fetch(self, query: str, *args: Any) -> list[dict[str, Any]]:
        if "FROM embeddings" in query and "source_table = $1" in query:
            return list(self.pool.candidate_rows)
        return []

    async def fetchrow(self, query: str, *args: Any) -> Any:
        if "INSERT INTO embeddings" in query:
            self.pool.insert_attempts += 1
            if self.pool.fail_insert:
                raise RuntimeError("simulated insert failure")
            rec = {
                "source_table": args[0],
                "source_id": args[1],
                "content_hash": args[2],
                "text_preview": args[3],
                "embedding_model": args[4],
                "embedding": args[5],
                "metadata": args[6],
                "id": 9000 + self.pool.insert_attempts,
            }
            self.tx_inserts.append(rec)
            return {"id": rec["id"]}
        return None

    async def fetchval(self, query: str, *args: Any) -> Any:
        if "SELECT 1 FROM embeddings" in query:
            if self.pool.fail_verify:
                return None
            row_id = args[0]
            return 1 if any(r["id"] == row_id for r in self.tx_inserts) else None
        return None

    async def execute(self, query: str, *args: Any) -> str:
        if "DELETE FROM embeddings" in query:
            self.tx_deletes.append(args[0])
            return "DELETE 1"
        return "OK"


class _AcquireCtx:
    def __init__(self, pool: "FakePool"):
        self.pool = pool

    async def __aenter__(self) -> _RecordingConn:
        return _RecordingConn(self.pool)

    async def __aexit__(self, *_: Any) -> None:
        return None


class FakePool:
    def __init__(
        self,
        *,
        candidate_rows: list[dict[str, Any]] | None = None,
        fail_verify: bool = False,
        fail_insert: bool = False,
    ):
        self.candidate_rows = list(candidate_rows or [])
        self.fail_verify = fail_verify
        self.fail_insert = fail_insert
        self.inserted: list[dict[str, Any]] = []
        self.deleted_ids: list[int] = []
        self.insert_attempts = 0

    def acquire(self) -> _AcquireCtx:
        return _AcquireCtx(self)


def _make_row(*, source_table: str = "claude_sessions", **config_overrides: Any) -> dict[str, Any]:
    config: dict[str, Any] = {
        "source_table": source_table,
        "age_days": 90,
        "cluster_size": 2,
        "summary_provider": "joined_preview",  # avoid LLM in unit tests
        "summary_timeout_s": 30,
        **config_overrides,
    }
    return {"name": f"embeddings.collapse.{source_table}", "config": config}


# ---------------------------------------------------------------------------
# Handler contract tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_missing_source_table_raises():
    from services.integrations.handlers.retention_embeddings_collapse import (
        embeddings_collapse,
    )

    pool = FakePool()
    row: dict[str, Any] = {"name": "embeddings.collapse.test", "config": {}}
    with pytest.raises((ValueError, KeyError)):
        await embeddings_collapse(None, site_config=None, row=row, pool=pool)


@pytest.mark.asyncio
async def test_insufficient_candidates_returns_zeros():
    """A single candidate row is not clustered — clustering needs >= 2."""
    from services.integrations.handlers.retention_embeddings_collapse import (
        embeddings_collapse,
    )

    pool = FakePool(candidate_rows=[_embedding_row(1, "s1", _vec("A"))])
    row = _make_row(source_table="claude_sessions")
    result = await embeddings_collapse(None, site_config=None, row=row, pool=pool)

    assert result["deleted"] == 0
    assert result["summarized"] == 0
    assert result["clusters"] == 0
    assert result["source_table"] == "claude_sessions"
    assert pool.insert_attempts == 0


@pytest.mark.asyncio
async def test_empty_candidates_returns_zeros():
    from services.integrations.handlers.retention_embeddings_collapse import (
        embeddings_collapse,
    )

    pool = FakePool(candidate_rows=[])
    row = _make_row(source_table="brain")
    result = await embeddings_collapse(None, site_config=None, row=row, pool=pool)

    assert result["deleted"] == 0
    assert result["summarized"] == 0
    assert result["clusters"] == 0


@pytest.mark.asyncio
async def test_two_clusters_writes_summaries_and_deletes_originals():
    """8 rows in two clear directions → 2 summary writes + 8 deletes."""
    from services.integrations.handlers.retention_embeddings_collapse import (
        embeddings_collapse,
    )

    low = [_embedding_row(i, f"low-{i}", _vec("A", jitter=i * 0.001)) for i in range(4)]
    high = [_embedding_row(10 + i, f"high-{i}", _vec("B", jitter=i * 0.001)) for i in range(4)]
    pool = FakePool(candidate_rows=low + high)
    row = _make_row(source_table="claude_sessions", cluster_size=2)

    result = await embeddings_collapse(None, site_config=None, row=row, pool=pool)

    assert result["summarized"] == 2
    assert result["deleted"] == 8
    assert result["clusters"] == 2
    assert result["source_table"] == "claude_sessions"
    assert len(pool.inserted) == 2
    for summary in pool.inserted:
        meta = json.loads(summary["metadata"])
        assert meta["is_summary"] is True
        assert meta["collapsed_count"] >= 2


@pytest.mark.asyncio
async def test_transaction_rollback_on_verify_failure_leaves_rows():
    """Verify failure inside the transaction must roll back — no committed writes."""
    from services.integrations.handlers.retention_embeddings_collapse import (
        embeddings_collapse,
    )

    low = [_embedding_row(i, f"low-{i}", _vec("A", jitter=i * 0.001)) for i in range(3)]
    high = [_embedding_row(10 + i, f"high-{i}", _vec("B", jitter=i * 0.001)) for i in range(3)]
    pool = FakePool(candidate_rows=low + high, fail_verify=True)
    row = _make_row(source_table="claude_sessions", cluster_size=2)

    result = await embeddings_collapse(None, site_config=None, row=row, pool=pool)

    assert pool.insert_attempts >= 1
    assert pool.inserted == []
    assert pool.deleted_ids == []
    assert result["deleted"] == 0
    assert result["summarized"] == 0


# ---------------------------------------------------------------------------
# Pure function tests
# ---------------------------------------------------------------------------


def test_kmeans_separates_two_obvious_clusters():
    from services.integrations.handlers.retention_embeddings_collapse import (
        kmeans_cluster,
    )

    low = [_vec("A", jitter=i * 0.001) for i in range(5)]
    high = [_vec("B", jitter=i * 0.001) for i in range(5)]
    assignments, centroids = kmeans_cluster(low + high, 2)

    assert len(assignments) == 10
    assert len(set(assignments[:5])) == 1
    assert len(set(assignments[5:])) == 1
    assert assignments[0] != assignments[5]
    assert len(centroids) == 2


def test_build_summary_text_truncates_long_preview():
    from services.integrations.handlers.retention_embeddings_collapse import (
        build_summary_text,
    )

    long_preview = "x" * 400
    result = build_summary_text([long_preview, "short"], chars_per_member=50)
    parts = result.split(" | ")
    assert len(parts) == 2
    assert parts[0].endswith("...")
    assert len(parts[0]) <= 53  # 50 + "..."
