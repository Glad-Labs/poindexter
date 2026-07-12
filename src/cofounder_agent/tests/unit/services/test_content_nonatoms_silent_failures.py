"""Unit tests — modules/content non-atom best-effort-failure visibility.

Gap-site burn-down (batch 4) — finishes off modules/content (batch 3 did
the atoms/ sub-tree). Four best-effort swallows across the content module
used to log at ``logger.debug`` (invisible at the prod log level) and
return a fallback. They now ``emit_finding`` a non-paging ``info`` finding
(visible on the Findings dashboard, deduped per site, never routed —
``info`` sits below the findings router's severity floor) while still
returning the fallback (non-blocking).

Mirrors ``test_content_atoms_silent_failures.py`` (batch 3).
"""

from __future__ import annotations

import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

import modules.content.auto_publish_gate as auto_publish_gate
import modules.content.internal_link_coherence as internal_link_coherence
import modules.content.stages.source_featured_image as source_featured_image
from modules.content.multi_model_qa import MultiModelQA
from services.site_config import SiteConfig

pytestmark = pytest.mark.unit


class _AcquireRaisingPool:
    """A pool whose ``acquire`` raises synchronously — drives
    ``async with pool.acquire()`` helpers into their except branch."""

    def acquire(self):
        raise RuntimeError("pool acquire boom")


def _capture(monkeypatch) -> list[dict]:
    """Patch ``utils.findings.emit_finding`` with a capture list and return it.

    The four sites under test import ``emit_finding`` locally (function
    body, not module top-level) — a fresh ``from utils.findings import
    emit_finding`` on each call always resolves the *current* attribute on
    the source module, so patching it there intercepts every site.
    """
    calls: list[dict] = []
    import utils.findings as findings_module

    monkeypatch.setattr(findings_module, "emit_finding", lambda **kw: calls.append(kw))
    return calls


def _settings_service(**overrides):
    svc = MagicMock()

    async def _get(key):
        return overrides.get(key)

    svc.get = AsyncMock(side_effect=_get)
    return svc


# --- auto_publish_gate: capability_outcomes lookup failure -----------------


@pytest.mark.asyncio
async def test_lookup_latest_capability_outcome_emits_finding_on_pool_error(
    monkeypatch,
):
    calls = _capture(monkeypatch)

    result = await auto_publish_gate._lookup_latest_capability_outcome(
        _AcquireRaisingPool(), "task-1",
    )

    assert result is None  # non-blocking — approve-time stamp just skips
    assert len(calls) == 1
    assert calls[0]["kind"] == "capability_outcomes_lookup_failed"
    assert calls[0]["dedup_key"] == "capability_outcomes_lookup_failed"
    assert calls[0].get("severity", "info") == "info"


# --- internal_link_coherence: inbound-link count failure --------------------


class _FetchRaisingPool:
    async def fetch(self, *args, **kwargs):
        raise RuntimeError("posts fetch boom")


@pytest.mark.asyncio
async def test_count_inbound_links_emits_finding_on_db_error(monkeypatch):
    calls = _capture(monkeypatch)

    result = await internal_link_coherence.count_inbound_links_to_slug(
        _FetchRaisingPool(), "some-post-slug",
    )

    assert result == 0  # non-blocking — cap just can't see this slug's count
    assert len(calls) == 1
    assert calls[0]["kind"] == "inbound_link_count_failed"
    assert calls[0]["dedup_key"] == "inbound_link_count_failed:some-post-slug"


# --- multi_model_qa: preview-screenshot service import guard ---------------


@pytest.mark.asyncio
async def test_check_rendered_preview_emits_finding_on_import_failure(monkeypatch):
    calls = _capture(monkeypatch)
    settings = _settings_service(
        qa_preview_screenshot_enabled="true",
        qa_preview_vision_model="gemma-vision",
    )
    qa = MultiModelQA(pool=None, settings_service=settings, site_config=SiteConfig())
    # Force the deferred import to fail without touching the real module.
    monkeypatch.setitem(sys.modules, "services.preview_screenshot", None)

    result = await qa._check_rendered_preview(
        "Some Title", "some topic", "https://preview.example/post",
    )

    assert result is None  # non-blocking — preview QA just skipped
    assert len(calls) == 1
    assert calls[0]["kind"] == "preview_screenshot_service_unavailable"
    assert calls[0]["dedup_key"] == "preview_screenshot_service_unavailable"


# --- source_featured_image: media_asset_recorder import guard --------------


@pytest.mark.asyncio
async def test_record_featured_image_asset_emits_finding_on_import_failure(
    monkeypatch,
):
    calls = _capture(monkeypatch)
    monkeypatch.setitem(sys.modules, "services.media_asset_recorder", None)

    result = await source_featured_image._record_featured_image_asset(
        site_config=object(),
        post_id=None,
        task_id="task-1",
        public_url="/media/featured.png",
        width=1200,
        height=630,
        provider_plugin="sdxl",
        metadata={},
        mime_type="image/png",
    )

    assert result is None
    assert len(calls) == 1
    assert calls[0]["kind"] == "media_asset_recorder_unavailable"
    # Same dedup_key as the batch-3 inline-image site (_image_helpers) —
    # same root cause (the module import itself), so they share one
    # cooldown rather than paging once per call site.
    assert calls[0]["dedup_key"] == "media_asset_recorder_unavailable"
