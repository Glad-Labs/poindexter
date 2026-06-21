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
