"""Tests for the writer_rag_mode dispatcher."""

import pytest

from services.writer_rag_modes import dispatch_writer_mode

pytestmark = pytest.mark.asyncio


async def test_dispatch_calls_two_pass_handler(monkeypatch):
    called = {}

    async def fake_handler(*, topic, angle, niche_id, pool, **kw):
        called["mode"] = "TWO_PASS"
        return {"draft": "..."}

    monkeypatch.setattr("services.writer_rag_modes.two_pass.run", fake_handler)
    out = await dispatch_writer_mode(
        mode="TWO_PASS", topic="t", angle="a", niche_id="n", pool=None,
    )
    assert called["mode"] == "TWO_PASS"
    assert "draft" in out


async def test_dispatch_unknown_mode_raises():
    # Modes deleted 2026-05-28 (TOPIC_ONLY / CITATION_BUDGET / STORY_SPINE /
    # DETERMINISTIC_COMPOSITOR) all now raise — same path as a typo.
    for legacy in ("TOPIC_ONLY", "CITATION_BUDGET", "STORY_SPINE",
                   "DETERMINISTIC_COMPOSITOR", "BOGUS"):
        with pytest.raises(ValueError, match="unknown writer_rag_mode"):
            await dispatch_writer_mode(
                mode=legacy, topic="t", angle="a", niche_id="n", pool=None,
            )
