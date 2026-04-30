"""Tests for the writer_rag_mode dispatcher.

Spec: docs/superpowers/specs/2026-04-30-rag-pivot-niche-discovery-design.md
Plan: docs/superpowers/plans/2026-04-30-rag-pivot-niche-discovery.md (Task 9)
"""

import pytest

from services.writer_rag_modes import dispatch_writer_mode

pytestmark = pytest.mark.asyncio


async def test_dispatch_calls_topic_only_handler(monkeypatch):
    called = {}

    async def fake_handler(*, topic, angle, niche_id, pool, **kw):
        called["mode"] = "TOPIC_ONLY"
        return {"draft": "..."}

    monkeypatch.setattr("services.writer_rag_modes.topic_only.run", fake_handler)
    out = await dispatch_writer_mode(
        mode="TOPIC_ONLY", topic="t", angle="a", niche_id="n", pool=None,
    )
    assert called["mode"] == "TOPIC_ONLY"
    assert "draft" in out


async def test_dispatch_unknown_mode_raises():
    with pytest.raises(ValueError, match="unknown writer_rag_mode"):
        await dispatch_writer_mode(
            mode="BOGUS", topic="t", angle="a", niche_id="n", pool=None,
        )
