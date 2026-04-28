"""Tests for services/topic_dedup_semantic.py — semantic-embedding
deduplicator alternative to the lexical word-overlap baseline (#201).

These tests stub out the sentence-transformer model so the suite stays
fast (no actual embedding computation, no model download). The
``_get_model`` cache is bypassed via dependency injection of a fake
encoder.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from services.topic_dedup_semantic import (
    SemanticDeduplicator,
    get_deduplicator,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _Topic:
    """Minimal _TopicLike implementation for tests."""

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


def _make_dedup(values: dict | None = None) -> SemanticDeduplicator:
    return SemanticDeduplicator(pool=None, site_config=_site_config(values))


def _stub_embeddings(mapping: dict[str, list[float]]):
    """Return a fake encoder that maps each input string to a fixed vector.

    Vectors should be L2-normalized (sentence-transformers normalize by
    default) — caller's responsibility.
    """
    def _encode(texts, normalize_embeddings=True, show_progress_bar=False):
        return np.array([mapping[t] for t in texts])
    fake_model = MagicMock()
    fake_model.encode = _encode
    return fake_model


# ---------------------------------------------------------------------------
# Engine selector
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestEngineSelector:
    def test_default_returns_word_overlap_engine(self):
        from services.topic_dedup import TopicDeduplicator
        sc = _site_config()  # no setting
        engine = get_deduplicator(pool=None, site_config=sc)
        assert isinstance(engine, TopicDeduplicator)

    def test_bertopic_setting_returns_semantic_engine(self):
        sc = _site_config({"topic_dedup_engine": "bertopic"})
        engine = get_deduplicator(pool=None, site_config=sc)
        assert isinstance(engine, SemanticDeduplicator)

    def test_semantic_alias_works(self):
        sc = _site_config({"topic_dedup_engine": "semantic"})
        engine = get_deduplicator(pool=None, site_config=sc)
        assert isinstance(engine, SemanticDeduplicator)

    def test_unknown_value_falls_back_to_word_overlap(self):
        from services.topic_dedup import TopicDeduplicator
        sc = _site_config({"topic_dedup_engine": "made-up-engine"})
        engine = get_deduplicator(pool=None, site_config=sc)
        assert isinstance(engine, TopicDeduplicator)


# ---------------------------------------------------------------------------
# SemanticDeduplicator — intra-batch
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSemanticIntraBatch:
    def test_paraphrases_marked_dup(self):
        # Embedding map: a/b are paraphrases (cosine ≈ 0.95);
        # c is distinct (cosine ≈ 0 with both).
        mapping = {
            "Top Cybersecurity Threats 2026":   [1.0, 0.0, 0.0],
            "Major Cyber Risks 2026":           [0.95, 0.31, 0.0],   # very close to first
            "How to Train a Vintage LM":        [0.0, 0.0, 1.0],     # distinct
        }
        dedup = _make_dedup()
        topics = [_Topic(k) for k in mapping]
        with patch(
            "services.topic_dedup_semantic._get_model",
            return_value=_stub_embeddings(mapping),
        ):
            dedup.mark_intra_batch(topics)

        assert topics[0].is_duplicate is False
        assert topics[1].is_duplicate is True   # paraphrase of #0
        assert topics[2].is_duplicate is False  # distinct

    def test_below_threshold_left_alone(self):
        # All distant from each other.
        mapping = {
            "A": [1.0, 0.0, 0.0],
            "B": [0.0, 1.0, 0.0],
            "C": [0.0, 0.0, 1.0],
        }
        dedup = _make_dedup()
        topics = [_Topic(k) for k in mapping]
        with patch(
            "services.topic_dedup_semantic._get_model",
            return_value=_stub_embeddings(mapping),
        ):
            dedup.mark_intra_batch(topics)

        assert all(not t.is_duplicate for t in topics)

    def test_threshold_override_via_site_config(self):
        # Two pairs that score 0.5 — would NOT be marked at default 0.65,
        # but ARE marked when threshold lowered to 0.4 via app_settings.
        mapping = {
            "First Title":  [1.0, 0.0],
            "Second Title": [0.5, 0.866],   # cos = 0.5 with First
        }
        dedup = _make_dedup({"topic_dedup_intra_batch_threshold_semantic": 0.4})
        topics = [_Topic(k) for k in mapping]
        with patch(
            "services.topic_dedup_semantic._get_model",
            return_value=_stub_embeddings(mapping),
        ):
            dedup.mark_intra_batch(topics)
        assert topics[1].is_duplicate is True


# ---------------------------------------------------------------------------
# SemanticDeduplicator — vs-existing
# ---------------------------------------------------------------------------


class _AsyncRow(dict):
    """Asyncpg-style row that supports both ``row['key']`` and ``row.get``."""
    pass


class _FakePool:
    def __init__(self, posts: list[str], task_titles: list[str]):
        self.posts = [_AsyncRow(title=t) for t in posts]
        self.tasks = [_AsyncRow(topic=t, title=t) for t in task_titles]
        self._call_idx = 0

    async def fetch(self, sql: str, *args):
        # First call: posts. Second call: content_tasks.
        self._call_idx += 1
        if self._call_idx == 1:
            return self.posts
        return self.tasks


@pytest.mark.unit
class TestSemanticVsExisting:
    @pytest.mark.asyncio
    async def test_paraphrase_of_published_marked_dup(self):
        candidate = "Modern Cyber Threats Today"
        existing = "Top Cybersecurity Threats 2026"
        # Make the candidate cosine-similar to the existing post.
        mapping = {
            candidate: [0.95, 0.31, 0.0],
            existing: [1.0, 0.0, 0.0],
        }
        dedup = _make_dedup()
        topics = [_Topic(candidate)]
        pool = _FakePool([existing], [])
        dedup.pool = pool
        with patch(
            "services.topic_dedup_semantic._get_model",
            return_value=_stub_embeddings(mapping),
        ):
            await dedup.mark_against_existing(topics)
        assert topics[0].is_duplicate is True

    @pytest.mark.asyncio
    async def test_distinct_candidate_left_alone(self):
        candidate = "How to Train a Vintage LM"
        existing = "Top Cybersecurity Threats 2026"
        mapping = {
            candidate: [0.0, 0.0, 1.0],
            existing: [1.0, 0.0, 0.0],
        }
        dedup = _make_dedup()
        topics = [_Topic(candidate)]
        pool = _FakePool([existing], [])
        dedup.pool = pool
        with patch(
            "services.topic_dedup_semantic._get_model",
            return_value=_stub_embeddings(mapping),
        ):
            await dedup.mark_against_existing(topics)
        assert topics[0].is_duplicate is False

    @pytest.mark.asyncio
    async def test_no_existing_skips_cleanly(self):
        dedup = _make_dedup()
        topics = [_Topic("Something")]
        dedup.pool = _FakePool([], [])
        # No model load needed because there's nothing to compare against.
        await dedup.mark_against_existing(topics)
        assert topics[0].is_duplicate is False
