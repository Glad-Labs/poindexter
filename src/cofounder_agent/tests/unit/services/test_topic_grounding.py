"""Tests for topic_grounding — internal-corpus grounding of external topics."""

import pytest

from services.site_config import SiteConfig
from services.topic_grounding import (
    GroundingMatch,
    GroundingResult,
    internal_grounding,
)

pytestmark = pytest.mark.asyncio(loop_scope="session")


class _FakeConn:
    def __init__(self, row, *, raise_on_fetch=False):
        self._row = row
        self._raise = raise_on_fetch
        self.last_args = None

    async def fetchrow(self, query, *args):
        self.last_args = (query, args)
        if self._raise:
            raise RuntimeError("boom")
        return self._row


class _FakeAcquireCtx:
    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _FakePool:
    def __init__(self, row=None, *, raise_on_fetch=False):
        self.conn = _FakeConn(row, raise_on_fetch=raise_on_fetch)
        self.acquired = False

    def acquire(self):
        self.acquired = True
        return _FakeAcquireCtx(self.conn)


def _cfg(**over):
    base = {
        "niche_external_grounding_source_kinds": "post_history,claude_session",
        "niche_external_grounding_threshold": "0.55",
    }
    base.update(over)
    return SiteConfig(initial_config=base)


async def test_grounded_when_similarity_above_threshold():
    row = {
        "source_table": "posts", "source_id": "p1",
        "text_preview": "we shipped X", "similarity": 0.80,
    }
    pool = _FakePool(row)
    res = await internal_grounding(pool, [0.1, 0.2], site_config=_cfg())
    assert isinstance(res, GroundingResult)
    assert res.grounded is True
    assert res.similarity == pytest.approx(0.80)
    assert isinstance(res.match, GroundingMatch)
    assert res.match.source_table == "posts"
    assert res.match.source_id == "p1"


async def test_ungrounded_when_similarity_below_threshold():
    row = {
        "source_table": "posts", "source_id": "p1",
        "text_preview": "unrelated", "similarity": 0.20,
    }
    res = await internal_grounding(_FakePool(row), [0.1], site_config=_cfg())
    assert res.grounded is False
    assert res.similarity == pytest.approx(0.20)
    assert res.match is not None  # match is still returned for observability


async def test_empty_vector_fails_open_without_query():
    pool = _FakePool(row=None)
    res = await internal_grounding(pool, [], site_config=_cfg())
    assert res.grounded is True
    assert res.similarity is None
    assert res.match is None
    assert pool.acquired is False  # never touched the DB


async def test_query_error_fails_open():
    pool = _FakePool(row=None, raise_on_fetch=True)
    res = await internal_grounding(pool, [0.1], site_config=_cfg())
    assert res.grounded is True
    assert res.similarity is None
    assert res.match is None


async def test_empty_corpus_fails_open():
    # No matching rows (fresh install) -> fetchrow returns None -> grounded.
    res = await internal_grounding(_FakePool(row=None), [0.1], site_config=_cfg())
    assert res.grounded is True
    assert res.similarity is None
    assert res.match is None


async def test_unknown_source_kinds_are_skipped_and_fail_open():
    # Only ops kinds configured -> they map to nothing -> empty table list.
    cfg = _cfg(niche_external_grounding_source_kinds="audit_event,brain_knowledge")
    pool = _FakePool(row=None)
    res = await internal_grounding(pool, [0.1], site_config=cfg)
    assert res.grounded is True
    assert pool.acquired is False  # no valid tables -> no query


async def test_query_targets_only_configured_content_tables():
    row = {
        "source_table": "claude_sessions", "source_id": "s1",
        "text_preview": "session", "similarity": 0.9,
    }
    pool = _FakePool(row)
    await internal_grounding(pool, [0.1], site_config=_cfg())
    _query, args = pool.conn.last_args
    # args[1] is the text[] of source_tables passed to ANY($2)
    assert set(args[1]) == {"posts", "claude_sessions"}
