"""Tests for services/topic_dedup_guard.py — the pre-enqueue semantic
dedup guard for manually-injected topics.

The topic-discovery pipeline already dedups AUTO-discovered topics
(``services.topic_dedup`` / ``topic_dedup_semantic``). But a topic
injected directly via the ``create_post`` MCP tool / ``POST /api/tasks``
bypassed discovery entirely, so a near-duplicate of an already-published
post could sail straight into the pipeline, pass every QA rail, and
reach ``awaiting_approval`` (real incident: "Quantization and VRAM…" was
~75% identical to the published "The VRAM Currency Problem").

This guard runs the same pgvector similarity search the
``find_similar_posts`` tool uses (over ``source_table='posts'``) and
refuses to enqueue when the closest published post is at/above
``app_settings.create_post_dedup_threshold`` (default 0.75), unless the
caller passes ``force=True``.
"""

from __future__ import annotations

import pytest

from poindexter.memory.client import MemoryHit
from services.site_config import SiteConfig
from services.topic_dedup_guard import (
    DuplicateTopicError,
    assert_topic_not_duplicate,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _hit(
    similarity: float,
    *,
    title: str = "The VRAM Currency Problem",
    post_id: str = "post/bb10de87",
) -> MemoryHit:
    return MemoryHit(
        source_table="posts",
        source_id=post_id,
        similarity=similarity,
        text_preview="The VRAM you can afford decides the model you can run…",
        writer=None,
        origin_path=None,
        metadata={"title": title},
    )


class _FakeMemoryClient:
    """Records the search arguments and returns a canned hit list.

    ``find_similar_posts`` mirrors ``poindexter.memory.MemoryClient`` so the
    guard can be exercised without a DB or an embedding model.
    """

    def __init__(
        self,
        hits: list[MemoryHit] | None = None,
        *,
        raises: Exception | None = None,
    ) -> None:
        self._hits = hits or []
        self._raises = raises
        self.calls: list[dict] = []

    async def find_similar_posts(
        self, topic: str, *, limit: int = 5, min_similarity: float = 0.75
    ) -> list[MemoryHit]:
        self.calls.append(
            {"topic": topic, "limit": limit, "min_similarity": min_similarity}
        )
        if self._raises is not None:
            raise self._raises
        return list(self._hits)


def _site_config(threshold: str = "0.75") -> SiteConfig:
    return SiteConfig(initial_config={"create_post_dedup_threshold": threshold})


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_duplicate_topic_raises():
    """A topic close to a published post is refused, with match details."""
    mem = _FakeMemoryClient(hits=[_hit(0.82)])

    with pytest.raises(DuplicateTopicError) as exc:
        await assert_topic_not_duplicate(
            "Quantization and VRAM: how to fit a large language model",
            site_config=_site_config(),
            memory_client=mem,
        )

    err = exc.value
    # The error names the offending post and surfaces the score so the
    # operator can judge the call — fail loud with context.
    assert "The VRAM Currency Problem" in str(err)
    assert err.similarity == pytest.approx(0.82)
    # Remediation is spelled out in the message.
    assert "force" in str(err).lower()


@pytest.mark.parametrize("rerank_logit", [-10.21, -3.4, 0.0, 0.3, 0.74])
async def test_rerank_logit_below_cosine_threshold_still_blocks(rerank_logit):
    """A near-duplicate must block even when its ``.similarity`` is a low
    or negative cross-encoder rerank logit.

    Regression for the silent under-block: with the RAG reranker on (the
    prod default), ``find_similar_posts`` applies ``min_similarity`` as a
    TRUE cosine floor at the base pgvector level, then the cross-encoder
    OVERWRITES ``MemoryHit.similarity`` with a raw logit (unbounded,
    routinely negative). So every hit the search returns is already
    cosine ≥ threshold — but re-checking ``.similarity >= threshold``
    compares a logit against a cosine floor and lets a genuine duplicate
    through. The guard must trust the floor and block on any returned hit.
    """
    # cosine ≥ 0.75 is already guaranteed by the search floor; the hit
    # surfaces with a sub-threshold rerank logit as its ``.similarity``.
    mem = _FakeMemoryClient(hits=[_hit(rerank_logit)])

    with pytest.raises(DuplicateTopicError):
        await assert_topic_not_duplicate(
            "Quantization and VRAM: how to fit a large language model",
            site_config=_site_config(),
            memory_client=mem,
        )


def test_duplicate_error_message_coherent_for_negative_rerank_logit():
    """The operator-facing message stays coherent when ``.similarity`` is a
    negative rerank logit (the prod rerank path).

    It must name the colliding post and the dedup threshold it cleared, keep
    the force remediation, and NOT label the surfaced score as a cosine or
    render a self-contradictory ``score ≥ threshold`` inequality like
    "cosine −3.40 ≥ threshold 0.75".
    """
    err = DuplicateTopicError(
        topic="Quantization and VRAM",
        match=_hit(-3.4),  # cross-encoder logit, not a cosine
        threshold=0.75,
    )
    msg = str(err)
    assert "The VRAM Currency Problem" in msg  # names the offending post
    assert "0.75" in msg  # names the threshold it cleared
    assert "force" in msg.lower()  # remediation preserved
    # The raw per-hit score is a rerank logit here — it must NOT leak into
    # the message, where pairing it with the threshold produced the
    # "cosine −3.40 ≥ 0.75" contradiction. Only the (true cosine) threshold
    # the match provably cleared is surfaced.
    assert "-3.4" not in msg


async def test_distinct_topic_passes():
    """A topic with no near-duplicate is allowed through (no raise)."""
    mem = _FakeMemoryClient(hits=[])  # nothing at/above threshold

    await assert_topic_not_duplicate(
        "A field guide to async Rust ownership",
        site_config=_site_config(),
        memory_client=mem,
    )


async def test_force_overrides_duplicate():
    """force=True bypasses the guard even for a dead-on duplicate, and
    does not waste an embedding search."""
    mem = _FakeMemoryClient(hits=[_hit(0.99)])

    await assert_topic_not_duplicate(
        "Quantization and VRAM",
        site_config=_site_config(),
        memory_client=mem,
        force=True,
    )

    assert mem.calls == []  # short-circuits before searching


async def test_threshold_read_from_site_config():
    """The configured threshold is passed through as the search floor."""
    mem = _FakeMemoryClient(hits=[])

    await assert_topic_not_duplicate(
        "Some topic",
        site_config=_site_config(threshold="0.9"),
        memory_client=mem,
    )

    assert mem.calls
    assert mem.calls[0]["min_similarity"] == pytest.approx(0.9)


async def test_search_error_fails_open():
    """A search-infra failure must not block creation — fail open."""
    mem = _FakeMemoryClient(raises=RuntimeError("pgvector unavailable"))

    # Should NOT raise: a broken dedup index is no reason to refuse the
    # operator's explicit request.
    await assert_topic_not_duplicate(
        "Quantization and VRAM",
        site_config=_site_config(),
        memory_client=mem,
    )
