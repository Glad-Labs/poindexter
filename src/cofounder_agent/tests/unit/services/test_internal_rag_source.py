"""Tests for InternalRagSource — generate candidates from internal corpus.

Spec: docs/superpowers/specs/2026-04-30-rag-pivot-niche-discovery-design.md
Plan: docs/superpowers/plans/2026-04-30-rag-pivot-niche-discovery.md (Task 5)
"""

import pytest
from unittest.mock import AsyncMock
from services.internal_rag_source import (
    InternalRagSource,
    InternalCandidate,
    VALID_SOURCE_KINDS,
)


pytestmark = pytest.mark.asyncio(loop_scope="session")


class _FakeConn:
    def __init__(self, rows):
        self._rows = rows
        self.last_args = None

    async def fetch(self, query, *args):
        self.last_args = (query, args)
        return self._rows


class _FakeAcquireCtx:
    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _FakePool:
    """Minimal pool stand-in for unit tests that don't need a real DB."""

    def __init__(self, rows=None, rows_by_table=None):
        self._rows_by_table = rows_by_table or {}
        self._default_rows = rows or []
        self.last_conn: _FakeConn | None = None

    def acquire(self):
        # Pick rows based on the source_table arg the caller will send.
        # We can't peek args here, so the conn snapshots them on fetch().
        conn = _FakeConn(self._default_rows)
        # When the caller calls fetch(query, source_table, limit) we want
        # to vary results per source_table. Override fetch on the fly.
        rows_by_table = self._rows_by_table

        async def fetch(query, *args):
            conn.last_args = (query, args)
            if args and args[0] in rows_by_table:
                return rows_by_table[args[0]]
            return self._default_rows

        conn.fetch = fetch  # type: ignore[assignment]
        self.last_conn = conn
        return _FakeAcquireCtx(conn)


async def test_generate_pulls_top_k_per_source_kind(db_pool, monkeypatch):
    # The source should query the embeddings table for each enabled source_kind
    # and return distilled candidates.
    src = InternalRagSource(db_pool)
    # Mock the LLM distillation step — it turns a snippet into (topic, angle).
    async def fake_distill(snippets):
        return ("How we handled OAuth phase 1", "Why client credentials grant first")
    monkeypatch.setattr(src, "_distill_topic_angle", fake_distill)

    candidates = await src.generate(
        niche_id="00000000-0000-0000-0000-000000000001",
        source_kinds=["claude_session", "brain_knowledge"],
        per_kind_limit=2,
    )
    assert all(isinstance(c, InternalCandidate) for c in candidates)
    # at most 2 * 2 = 4 candidates if data exists for both source_kinds
    assert len(candidates) <= 4
    if candidates:
        c = candidates[0]
        assert c.distilled_topic
        assert c.distilled_angle
        assert c.primary_ref
        assert isinstance(c.supporting_refs, list)


async def test_generate_rejects_unknown_source_kind():
    pool = _FakePool()
    src = InternalRagSource(pool)
    with pytest.raises(ValueError, match="unknown source_kinds"):
        await src.generate(
            niche_id="00000000-0000-0000-0000-000000000001",
            source_kinds=["claude_session", "not_a_real_kind"],
            per_kind_limit=2,
        )


async def test_generate_rejects_unknown_kinds_lists_only_invalid_ones():
    pool = _FakePool()
    src = InternalRagSource(pool)
    with pytest.raises(ValueError) as exc_info:
        await src.generate(
            niche_id="00000000-0000-0000-0000-000000000001",
            source_kinds=["bogus_one", "bogus_two"],
            per_kind_limit=1,
        )
    msg = str(exc_info.value)
    assert "bogus_one" in msg
    assert "bogus_two" in msg
    # The valid-kinds set should not be echoed as "unknown".
    assert "claude_session" not in msg


async def test_generate_with_empty_source_kinds_returns_empty():
    pool = _FakePool()
    src = InternalRagSource(pool)
    result = await src.generate(
        niche_id="00000000-0000-0000-0000-000000000001",
        source_kinds=[],
        per_kind_limit=5,
    )
    assert result == []


async def test_generate_skips_git_commit_kind_silently(monkeypatch):
    # ``git_commit`` is in VALID_SOURCE_KINDS but not implemented yet —
    # _fetch_recent_snippets returns [] for it, so generate should
    # produce no candidates without raising.
    pool = _FakePool()
    src = InternalRagSource(pool)
    distill = AsyncMock(return_value=("t", "a"))
    monkeypatch.setattr(src, "_distill_topic_angle", distill)

    result = await src.generate(
        niche_id="00000000-0000-0000-0000-000000000001",
        source_kinds=["git_commit"],
        per_kind_limit=3,
    )
    assert result == []
    distill.assert_not_awaited()


async def test_fetch_recent_snippets_maps_source_kinds_to_tables():
    # Each source_kind should resolve to the right embeddings.source_table value.
    expected = {
        "claude_session": "claude_sessions",
        "brain_knowledge": "brain",
        "audit_event": "audit",
        "decision_log": "memory",
        "memory_file": "memory",
        "post_history": "posts",
    }
    for kind, table in expected.items():
        pool = _FakePool(rows=[])
        src = InternalRagSource(pool)
        await src._fetch_recent_snippets(kind, limit=3)
        assert pool.last_conn is not None
        assert pool.last_conn.last_args is not None
        _, args = pool.last_conn.last_args
        assert args[0] == table, f"{kind} should map to {table}, got {args[0]}"
        assert args[1] == 3


async def test_fetch_recent_snippets_returns_empty_for_unmapped_kind():
    pool = _FakePool()
    src = InternalRagSource(pool)
    rows = await src._fetch_recent_snippets("git_commit", limit=5)
    assert rows == []
    # Pool should never have been acquired for an unmapped kind.
    assert pool.last_conn is None


async def test_fetch_recent_snippets_handles_null_text_preview():
    rows = [
        {"source_id": "abc-123", "text_preview": None},
        {"source_id": "def-456", "text_preview": "real preview"},
    ]
    pool = _FakePool(rows=rows)
    src = InternalRagSource(pool)
    out = await src._fetch_recent_snippets("brain_knowledge", limit=2)
    assert out == [
        ("abc-123", "", []),
        ("def-456", "real preview", []),
    ]


async def test_generate_aggregates_across_multiple_kinds(monkeypatch):
    rows_by_table = {
        "claude_sessions": [
            {"source_id": "cs-1", "text_preview": "session one"},
            {"source_id": "cs-2", "text_preview": "session two"},
        ],
        "brain": [
            {"source_id": "bk-1", "text_preview": "brain entry"},
        ],
    }
    pool = _FakePool(rows_by_table=rows_by_table)
    src = InternalRagSource(pool)

    seen_snippets: list[list[str]] = []

    async def fake_distill(snippets):
        seen_snippets.append(snippets)
        return (f"topic-{len(seen_snippets)}", f"angle-{len(seen_snippets)}")

    monkeypatch.setattr(src, "_distill_topic_angle", fake_distill)

    result = await src.generate(
        niche_id="00000000-0000-0000-0000-000000000001",
        source_kinds=["claude_session", "brain_knowledge"],
        per_kind_limit=10,
    )
    assert len(result) == 3
    kinds = [c.source_kind for c in result]
    assert kinds.count("claude_session") == 2
    assert kinds.count("brain_knowledge") == 1
    primary_refs = {c.primary_ref for c in result}
    assert primary_refs == {"cs-1", "cs-2", "bk-1"}
    # distill saw the snippet text — supporting refs were [] so the input
    # list was just [snippet].
    assert ["session one"] in seen_snippets
    assert ["brain entry"] in seen_snippets


async def test_generate_propagates_per_kind_limit_to_fetch(monkeypatch):
    pool = _FakePool(rows=[])
    src = InternalRagSource(pool)

    seen_limits: list[int] = []
    real_fetch = src._fetch_recent_snippets

    async def spy(kind, limit):
        seen_limits.append(limit)
        return await real_fetch(kind, limit)

    monkeypatch.setattr(src, "_fetch_recent_snippets", spy)

    await src.generate(
        niche_id="00000000-0000-0000-0000-000000000001",
        source_kinds=["claude_session", "audit_event"],
        per_kind_limit=7,
    )
    assert seen_limits == [7, 7]


async def test_internal_candidate_defaults():
    c = InternalCandidate(
        source_kind="claude_session",
        primary_ref="abc",
        distilled_topic="t",
        distilled_angle="a",
    )
    assert c.supporting_refs == []
    assert c.raw_snippet == ""
    # Defaults must not share state across instances.
    c.supporting_refs.append({"x": 1})
    c2 = InternalCandidate(
        source_kind="brain_knowledge",
        primary_ref="def",
        distilled_topic="t2",
        distilled_angle="a2",
    )
    assert c2.supporting_refs == []


async def test_valid_source_kinds_includes_all_supported_kinds():
    # Lock down the public list — adding a kind here is a contract change
    # callers (NicheService.set_sources, taps wiring) need to know about.
    assert set(VALID_SOURCE_KINDS) == {
        "claude_session",
        "brain_knowledge",
        "audit_event",
        "git_commit",
        "decision_log",
        "memory_file",
        "post_history",
    }
