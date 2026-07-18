"""Unit tests — finalize_task best-effort content-integrity write visibility.

Silent-excepts OLD-debt burn-down (batch 12). Two finalize-stage writes were
swallowed at DEBUG only (invisible in prod, INFO+), each silently degrading a
published post with no other automated signal (QA runs BEFORE finalize):

* ``_maybe_append_sources_section`` — appends the Sources footer when the writer
  cited inline URLs but omitted one. A persistent failure ships every citing
  post without its sources list.
* ``_snapshot_final_revision`` — the terminal ``content_revisions`` row per task
  (internal tracker Phase 3.A2). A persistent failure silently blinds the
  revision-history feedback loop (the batch-1 ``_snapshot_initial_draft`` class).

Both now emit a non-paging ``info`` finding on failure and still never raise (a
best-effort write must never break finalize). Lifted into module-level helpers
purely for this testability (batch-10/11 precedent). The third flagged handler
(the auto-publish-gate ``emit_finding`` guard) stays ``# silent-ok``.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

import modules.content.stages.finalize_task as ft

pytestmark = [pytest.mark.unit]


def _capture(monkeypatch) -> list[dict]:
    calls: list[dict] = []
    import utils.findings as findings_module

    monkeypatch.setattr(findings_module, "emit_finding", lambda **kw: calls.append(kw))
    return calls


# --------------------------------------------------------------------------
# _maybe_append_sources_section (sync)
# --------------------------------------------------------------------------


def test_sources_append_failure_emits_finding_and_returns_original(monkeypatch):
    calls = _capture(monkeypatch)
    monkeypatch.setattr(
        "services.citation_verifier.extract_urls",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("extract boom")),
    )

    out = ft._maybe_append_sources_section("original body", None)

    # Never raises; returns content unchanged.
    assert out == "original body"
    findings = [c for c in calls if c["kind"] == "sources_section_append_failed"]
    assert len(findings) == 1
    assert findings[0].get("severity") == "info"


def test_sources_append_success_emits_no_finding(monkeypatch):
    calls = _capture(monkeypatch)
    monkeypatch.setattr(
        "services.citation_verifier.extract_urls", lambda *a, **k: ["https://x.test"]
    )
    monkeypatch.setattr(
        "services.citation_verifier.append_sources_section",
        lambda content, urls: content + "\n\n## Sources\n- https://x.test",
    )

    out = ft._maybe_append_sources_section("body", None)

    assert out.endswith("https://x.test")
    assert calls == []


# --------------------------------------------------------------------------
# _snapshot_final_revision (async)
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_final_snapshot_failure_emits_finding(monkeypatch):
    calls = _capture(monkeypatch)
    monkeypatch.setattr(
        "services.content_revisions_logger.log_revision",
        AsyncMock(side_effect=RuntimeError("log_revision boom")),
    )

    # Never raises — snapshot is best-effort. MagicMock auto-provides .pool.
    await ft._snapshot_final_revision(
        MagicMock(),
        task_id="task-1",
        content="final body",
        title="Title",
        change_summary="Final revision at quality score 4",
        model_used="gemma-4-31B",
        quality_score=4,
    )

    findings = [c for c in calls if c["kind"] == "content_revisions_final_snapshot_failed"]
    assert len(findings) == 1
    assert findings[0].get("severity") == "info"
    assert "content_revisions_final_snapshot_failed" in findings[0]["dedup_key"]


@pytest.mark.asyncio
async def test_final_snapshot_success_emits_no_finding(monkeypatch):
    calls = _capture(monkeypatch)
    monkeypatch.setattr(
        "services.content_revisions_logger.log_revision", AsyncMock(return_value=None)
    )

    await ft._snapshot_final_revision(
        MagicMock(),
        task_id="task-1",
        content="final body",
        title="Title",
        change_summary="Final revision at quality score 4",
        model_used="gemma-4-31B",
        quality_score=4,
    )

    assert calls == []
