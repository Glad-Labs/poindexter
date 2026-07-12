"""Tests for services/topic_dedup_content.py — the content-embedding topic
deduper (spec §1.4, 2026-07-12). Reuses ``MemoryClient.find_similar_posts``
over post CONTENT; the client is stubbed so the suite needs no DB / model.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from services.topic_dedup_content import (
    DEFAULT_EXISTING_THRESHOLD,
    ContentEmbeddingDeduplicator,
)


class _Topic:
    def __init__(self, title: str) -> None:
        self.title = title
        self.is_duplicate = False


def _site_config(values: dict | None = None) -> MagicMock:
    sc = MagicMock()
    values = values or {}
    sc.get.side_effect = lambda key, default="": values.get(key, default)
    sc.get_float.side_effect = lambda key, default: values.get(key, default)
    sc.get_int.side_effect = lambda key, default: values.get(key, default)
    return sc


def _make_dedup(values: dict | None = None) -> ContentEmbeddingDeduplicator:
    return ContentEmbeddingDeduplicator(pool=None, site_config=_site_config(values))


class _FakeMem:
    """Async-context-manager stand-in for poindexter.memory.MemoryClient."""

    def __init__(self, hits_by_title: dict | None = None, raise_on: set | None = None):
        self._hits = hits_by_title or {}
        self._raise_on = set(raise_on or ())
        self.calls: list[tuple] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_a):
        return False

    async def find_similar_posts(self, topic, *, limit=1, min_similarity=0.75):
        self.calls.append((topic, limit, min_similarity))
        if topic in self._raise_on:
            raise RuntimeError("boom")
        return self._hits.get(topic, [])


@pytest.mark.unit
class TestThreshold:
    def test_default_threshold(self):
        assert _make_dedup()._threshold() == DEFAULT_EXISTING_THRESHOLD
        assert DEFAULT_EXISTING_THRESHOLD == 0.70

    def test_threshold_override(self):
        assert _make_dedup({"topic_dedup_existing_threshold_content": 0.62})._threshold() == 0.62


@pytest.mark.unit
class TestMarkAgainstExisting:
    @pytest.mark.asyncio
    async def test_marks_dup_on_content_hit(self):
        title = "GPU VRAM Budgeting for Local AI Inference"
        dedup = _make_dedup()
        topics = [_Topic(title)]
        fake = _FakeMem(hits_by_title={title: [{"title": "The VRAM Currency Problem"}]})
        with patch("poindexter.memory.MemoryClient", lambda: fake):
            await dedup.mark_against_existing(topics)
        assert topics[0].is_duplicate is True

    @pytest.mark.asyncio
    async def test_distinct_left_alone(self):
        dedup = _make_dedup()
        topics = [_Topic("A totally unrelated topic about sourdough")]
        fake = _FakeMem(hits_by_title={})  # no hits => not a dup
        with patch("poindexter.memory.MemoryClient", lambda: fake):
            await dedup.mark_against_existing(topics)
        assert topics[0].is_duplicate is False

    @pytest.mark.asyncio
    async def test_threshold_passed_as_min_similarity(self):
        dedup = _make_dedup({"topic_dedup_existing_threshold_content": 0.62})
        topics = [_Topic("Some Topic")]
        fake = _FakeMem()
        with patch("poindexter.memory.MemoryClient", lambda: fake):
            await dedup.mark_against_existing(topics)
        assert fake.calls and fake.calls[0][2] == 0.62

    @pytest.mark.asyncio
    async def test_fail_open_per_candidate_search_error(self):
        # A search that raises must NOT mark the candidate and must NOT crash.
        dedup = _make_dedup()
        topics = [_Topic("Boom Topic"), _Topic("Fine Topic")]
        fake = _FakeMem(
            hits_by_title={"Fine Topic": [{"title": "x"}]},
            raise_on={"Boom Topic"},
        )
        with patch("poindexter.memory.MemoryClient", lambda: fake):
            await dedup.mark_against_existing(topics)
        assert topics[0].is_duplicate is False  # errored -> fail open
        assert topics[1].is_duplicate is True   # subsequent candidate still checked

    @pytest.mark.asyncio
    async def test_fail_open_when_client_unavailable(self):
        dedup = _make_dedup()
        topics = [_Topic("Anything")]
        boom = MagicMock(side_effect=RuntimeError("no client"))
        with patch("poindexter.memory.MemoryClient", boom):
            await dedup.mark_against_existing(topics)  # must not raise
        assert topics[0].is_duplicate is False

    @pytest.mark.asyncio
    async def test_skips_already_dup_and_blank_titles(self):
        already = _Topic("Already flagged")
        already.is_duplicate = True
        blank = _Topic("   ")
        dedup = _make_dedup()
        fake = _FakeMem()
        with patch("poindexter.memory.MemoryClient", lambda: fake):
            await dedup.mark_against_existing([already, blank])
        # Neither should have hit the search.
        assert fake.calls == []


@pytest.mark.unit
class TestMarkIntraBatch:
    def test_delegates_to_word_overlap(self):
        # Two near-identical titles collapse via the lexical intra-batch pass.
        dedup = _make_dedup()
        topics = [
            _Topic("GPU VRAM Budgeting Guide"),
            _Topic("GPU VRAM Budgeting Tutorial"),
        ]
        dedup.mark_intra_batch(topics)
        assert topics[0].is_duplicate is False
        assert topics[1].is_duplicate is True


@pytest.mark.unit
class TestEngineSelector:
    def test_content_embedding_engine_selected(self):
        from services.topic_dedup_semantic import get_deduplicator

        for val in ("content", "content_embedding"):
            sc = _site_config({"topic_dedup_engine": val})
            engine = get_deduplicator(pool=None, site_config=sc)
            assert isinstance(engine, ContentEmbeddingDeduplicator)

    def test_seeded_default_is_content_embedding(self):
        from services.settings_defaults import DEFAULTS

        assert DEFAULTS["topic_dedup_engine"] == "content_embedding"
