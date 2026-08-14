"""Unit tests — writer_core best-effort-failure visibility.

OLD-debt burn-down (batch 1) — the first slice of the 285-site backlog the
2026-07-12 GAP sweep deliberately scoped out. ``writer_core`` was the single
biggest file in that baseline (12 sites) and it is the live writer, so a
swallow here degrades every post it touches with nothing on a board to say so.

Four of the twelve silently changed writer behaviour and now ``emit_finding``
a non-paging ``info`` finding (Findings dashboard, deduped, below the router's
severity floor) while still returning their fallback — the writer must never
be blocked by its own telemetry:

* ``_build_real_slug_allowlist`` — an empty allowlist makes
  ``scrub_fabricated_links`` maximally aggressive, quietly deleting *legitimate*
  internal links.
* ``_snapshot_initial_draft`` — the content_revisions feedback loop loses the
  first-pass draft, so later diffs silently compare against nothing.
* ``_collect_research_context`` — the post gets written without the caller's
  research attached.
* ``_fetch_existing_titles`` — the title-diversity check is skipped entirely,
  so a duplicate title can ship unnoticed.

The remaining eight are documented ``# silent-ok`` (fail-safe config defaults,
deliberate fall-through, and the fail-path telemetry writes — you cannot emit a
finding about a finding that failed to emit).

Mirrors ``tests/unit/services/test_content_nonatoms_silent_failures.py``.
"""

from __future__ import annotations

import sys

import pytest

import modules.content.writer_core as writer_core
from modules.content.writer_core import GenerateContentStage

pytestmark = pytest.mark.unit


def _capture(monkeypatch) -> list[dict]:
    """Patch ``utils.findings.emit_finding`` and collect the calls.

    Every site imports ``emit_finding`` function-locally, so a fresh
    ``from utils.findings import emit_finding`` resolves the *current*
    attribute on the source module on each call — patching it here therefore
    intercepts all of them. A module-level import would bind a private copy at
    import time and this patch would never be seen.
    """
    calls: list[dict] = []
    import utils.findings as findings_module

    monkeypatch.setattr(findings_module, "emit_finding", lambda **kw: calls.append(kw))
    return calls


class _AcquireRaisingPool:
    """``async with pool.acquire()`` blows up on entry."""

    def acquire(self):
        raise RuntimeError("pool acquire boom")


# --- _build_real_slug_allowlist: internal-links cache unreadable -----------


class _ExplodingCacheGenerator:
    """A content generator whose links cache raises when read."""

    @property
    def _internal_links_cache(self):
        raise RuntimeError("cache layout shifted")


def test_build_real_slug_allowlist_collects_slugs():
    gen = type("_G", (), {"_internal_links_cache": [
        'See "https://x/posts/async-python"',
        "no link here",
        'and "https://x/posts/gpu-scheduling"',
    ]})()

    assert writer_core._build_real_slug_allowlist(gen) == {
        "async-python", "gpu-scheduling",
    }


def test_build_real_slug_allowlist_emits_finding_when_cache_unreadable(monkeypatch):
    calls = _capture(monkeypatch)

    result = writer_core._build_real_slug_allowlist(_ExplodingCacheGenerator())

    # Non-blocking: the writer proceeds, but with an EMPTY allowlist, which is
    # exactly the state that makes the scrub delete real links — hence visible.
    assert result == set()
    assert len(calls) == 1
    assert calls[0]["kind"] == "real_slug_allowlist_build_failed"
    assert calls[0]["dedup_key"] == "real_slug_allowlist_build_failed"
    assert calls[0].get("severity", "info") == "info"


# --- _snapshot_initial_draft: content_revisions write failed ---------------


@pytest.mark.asyncio
async def test_snapshot_initial_draft_emits_finding_on_logger_failure(monkeypatch):
    calls = _capture(monkeypatch)
    # Force the deferred import to fail without touching the real module.
    monkeypatch.setitem(sys.modules, "services.content_revisions_logger", None)

    await writer_core._snapshot_initial_draft(
        None,
        task_id="task-1",
        content="body",
        title="Title",
        model_used="gemma",
        quality_score=None,
    )

    assert len(calls) == 1
    assert calls[0]["kind"] == "content_revisions_snapshot_failed"
    assert calls[0]["dedup_key"] == "content_revisions_snapshot_failed"
    assert calls[0].get("severity", "info") == "info"


# --- _collect_research_context: caller research unreadable -----------------


class _GetTaskRaisingDB:
    pool = None

    async def get_task(self, _task_id):
        raise RuntimeError("get_task boom")


@pytest.mark.asyncio
async def test_collect_research_context_emits_finding_when_task_read_fails(
    monkeypatch,
):
    calls = _capture(monkeypatch)
    stage = GenerateContentStage()

    result = await stage._collect_research_context(
        _GetTaskRaisingDB(), "task-1", "some topic",
    )

    # Non-blocking: the writer still runs, just without the caller's research.
    assert isinstance(result, str)
    findings = [c for c in calls if c["kind"] == "task_research_context_read_failed"]
    assert len(findings) == 1
    assert findings[0]["dedup_key"] == "task_research_context_read_failed"
    assert findings[0].get("severity", "info") == "info"


# --- recent-title lookup: diversity check skipped -------------------------


@pytest.mark.asyncio
async def test_fetch_recent_titles_emits_finding_on_db_error(monkeypatch):
    """The writer's ``_fetch_existing_titles`` moved to a shared service.

    poindexter#1043 consolidated the writer copy, the atom copy, and the
    (previously absent) dev_diary lookup into
    ``services.title_avoidance.fetch_recent_titles``. The finding contract is
    unchanged: degraded, never fatal, and never silent.
    """
    from services.title_avoidance import fetch_recent_titles

    calls = _capture(monkeypatch)

    result = await fetch_recent_titles(
        _AcquireRaisingPool(), source="modules.content.writer_core"
    )

    # Non-blocking: returns the empty window, but the title-diversity check is
    # now skipped — a duplicate title can ship.
    assert result == []
    assert len(calls) == 1
    assert calls[0]["kind"] == "title_history_lookup_failed"
    assert calls[0]["dedup_key"] == "title_history_lookup_failed"
    assert calls[0].get("severity", "info") == "info"
