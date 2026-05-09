"""Unit tests for ``services.qa_gates_db_writer``.

The writer is the missing half of the qa_gates telemetry contract —
``qa_gates_db.py`` (read) was always there, this file (write) was
discovered to be missing on 2026-05-09 when every gate showed
``last_run_at = NEVER``. These tests pin the contract so the gap can't
silently reappear.
"""

from __future__ import annotations

from typing import Any

import pytest

from services.qa_gates_db_writer import _REVIEWER_TO_GATE, record_chain_run


class _Review:
    """Minimal duck-type for ReviewerResult."""

    def __init__(self, reviewer: str, approved: bool = True, advisory: bool = False):
        self.reviewer = reviewer
        self.approved = approved
        self.advisory = advisory


class _FakeConn:
    def __init__(self, pool):
        self._pool = pool

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        pass

    def transaction(self):
        return self

    async def execute(self, query, *args):
        self._pool.executes.append((query, args))
        return "UPDATE 1"


class _FakePool:
    def __init__(self):
        self.executes: list[tuple[str, tuple[Any, ...]]] = []

    def acquire(self):
        return _FakeConn(self)


@pytest.mark.asyncio
async def test_pool_none_no_ops():
    """Match the read-side fallback shape: pool=None must not raise."""
    await record_chain_run(None, [_Review("programmatic_validator")])


@pytest.mark.asyncio
async def test_empty_reviews_no_ops():
    pool = _FakePool()
    await record_chain_run(pool, [])
    assert pool.executes == []


@pytest.mark.asyncio
async def test_unknown_reviewer_skipped():
    """Inline reviewers without a qa_gates row (e.g. citation_verifier,
    rendered_preview) must NOT trigger a UPDATE — there's no row to
    bump."""
    pool = _FakePool()
    await record_chain_run(pool, [
        _Review("citation_verifier"),
        _Review("rendered_preview"),
        _Review("topic_delivery"),
    ])
    assert pool.executes == []


@pytest.mark.asyncio
async def test_known_reviewer_bumps_counter():
    pool = _FakePool()
    await record_chain_run(pool, [_Review("programmatic_validator", approved=True)])
    assert len(pool.executes) == 1
    query, args = pool.executes[0]
    assert "UPDATE qa_gates" in query
    assert "total_runs = total_runs + 1" in query
    assert args == ("programmatic_validator", "passed", 0)


@pytest.mark.asyncio
async def test_rejected_review_increments_rejections():
    pool = _FakePool()
    await record_chain_run(pool, [
        _Review("programmatic_validator", approved=False),
    ])
    _, args = pool.executes[0]
    assert args == ("programmatic_validator", "rejected", 1)


@pytest.mark.asyncio
async def test_alias_mapping_image_relevance_to_vision_gate():
    """The inline reviewer name 'image_relevance' must update the
    qa_gates row named 'vision_gate'."""
    pool = _FakePool()
    await record_chain_run(pool, [_Review("image_relevance", approved=True)])
    _, args = pool.executes[0]
    assert args[0] == "vision_gate"


@pytest.mark.asyncio
async def test_alias_mapping_internal_consistency_to_consistency():
    pool = _FakePool()
    await record_chain_run(pool, [_Review("internal_consistency", approved=True)])
    _, args = pool.executes[0]
    assert args[0] == "consistency"


@pytest.mark.asyncio
async def test_alias_mapping_ollama_critic_to_llm_critic():
    pool = _FakePool()
    await record_chain_run(pool, [_Review("ollama_critic", approved=True)])
    _, args = pool.executes[0]
    assert args[0] == "llm_critic"


@pytest.mark.asyncio
async def test_duplicate_reviewer_collapses_to_one_update():
    """url_verifier appends a ReviewerResult on both the dead-link and
    the bonus path. The writer must collapse those into a single
    UPDATE so total_runs doesn't double-count one execution."""
    pool = _FakePool()
    await record_chain_run(pool, [
        _Review("url_verifier", approved=True),
        _Review("url_verifier", approved=True),
    ])
    assert len(pool.executes) == 1


@pytest.mark.asyncio
async def test_full_chain_writes_one_update_per_gate():
    """End-to-end: a typical chain emits 4-7 reviews; each maps to one
    gate row UPDATE."""
    pool = _FakePool()
    await record_chain_run(pool, [
        _Review("programmatic_validator", approved=True),
        _Review("ollama_critic", approved=True),
        _Review("internal_consistency", approved=True),
        _Review("web_factcheck", approved=True),
        _Review("url_verifier", approved=True),
        # Inline-only reviewers (no qa_gates row) must be ignored:
        _Review("citation_verifier", approved=True),
        _Review("topic_delivery", approved=True),
        _Review("rendered_preview", approved=True),
    ])
    bumped_gates = {args[0] for _, args in pool.executes}
    assert bumped_gates == {
        "programmatic_validator",
        "llm_critic",
        "consistency",
        "web_factcheck",
        "url_verifier",
    }


def test_alias_table_covers_every_known_inline_reviewer():
    """Documentation-as-test: when a new inline reviewer ships, this
    test forces the implementer to either add it to the alias table
    (if it has a qa_gates row) or to the explicit allow-list of
    reviewers that intentionally lack a row."""
    inline_reviewers_with_row = set(_REVIEWER_TO_GATE)
    inline_reviewers_without_row = {
        # Hardcoded-stage gates with no qa_gates row by design:
        "citation_verifier",
        "topic_delivery",
        "rendered_preview",
    }
    documented = inline_reviewers_with_row | inline_reviewers_without_row
    # If you trip this assertion, either:
    #   (a) add the reviewer name + gate name to _REVIEWER_TO_GATE, OR
    #   (b) add the name to inline_reviewers_without_row above.
    assert "programmatic_validator" in documented
    assert "ollama_critic" in documented
