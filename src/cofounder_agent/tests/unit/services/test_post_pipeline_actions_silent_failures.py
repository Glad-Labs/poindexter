"""Unit tests — preview-QA persist visibility.

Silent-excepts OLD-debt burn-down (batch 20). ``_maybe_run_preview_qa`` persists
the visual-QA verdict onto ``content_tasks.metadata`` (``preview_qa_score`` /
``preview_qa_approved`` / ``preview_qa_feedback``). Swallowed at DEBUG only
(invisible in prod, INFO+), a failure means whatever reads that metadata — the
preview gate / console — never sees the verdict, while the ``logger.info`` on
the very next line still reports the score either way, so the log gives no hint
the write was lost.

The same ``try`` also wraps a deliberate fail-loud guard —
``raise RuntimeError("preview QA: no DB pool available")`` — which the handler
was silently neutralising, contrary to the project's no-silent-defaults rule.
The finding surfaces that case too.

``content_tasks`` is not ``audit_log``, so ``emit_finding`` is the correct
signal (contrast batches 17-19, where the failing write WAS audit_log and a
finding would have vanished in the same outage).

The function's OTHER handler — around the QA run itself — stays ``# silent-ok``
(already annotated by an earlier pass): a failed visual-QA run is one advisory
note among several rails, not a gate.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

import services.post_pipeline_actions as ppa

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


def _capture(monkeypatch) -> list[dict]:
    calls: list[dict] = []
    import utils.findings as findings_module

    monkeypatch.setattr(findings_module, "emit_finding", lambda **kw: calls.append(kw))
    return calls


class _Pool:
    """Pool whose ``execute`` is awaited directly (no acquire())."""

    def __init__(self, raises: bool = False) -> None:
        self._raises = raises
        self.executed: list[Any] = []

    async def execute(self, *args: Any) -> str:
        if self._raises:
            raise RuntimeError("content_tasks update boom")
        self.executed.append(args)
        return "UPDATE 1"


class _FakePreviewQA:
    """Stands in for MultiModelQA — returns a fixed visual-QA verdict."""

    def __init__(self, **_kwargs: Any) -> None:
        pass

    async def _check_rendered_preview(self, **_kwargs: Any) -> Any:
        return SimpleNamespace(score=88, feedback="renders cleanly", approved=True)


def _patch_qa(monkeypatch) -> None:
    import modules.content.api as content_api

    monkeypatch.setattr(content_api, "MultiModelQA", _FakePreviewQA)


class _Settings:
    """Settings stub that turns the opt-in preview-QA gate ON.

    Without this the helper short-circuits on
    ``qa_preview_screenshot_enabled`` (default ``"false"``) and returns ""
    before ever reaching the persist under test.
    """

    async def get_setting(self, key: str, default: Any = None) -> Any:
        if key == "qa_preview_screenshot_enabled":
            return "true"
        return default


async def _run(database_service: Any) -> str:
    return await ppa._maybe_run_preview_qa(
        database_service=database_service,
        settings_service=_Settings(),  # non-None -> skips get_service()
        site_config=SimpleNamespace(),
        task_id="task-1",
        topic="A Topic",
        preview_token="tok-1",
    )


async def test_preview_qa_persist_failure_emits_finding(monkeypatch):
    calls = _capture(monkeypatch)
    _patch_qa(monkeypatch)
    db = SimpleNamespace(cloud_pool=None, pool=_Pool(raises=True))

    note = await _run(db)

    # Never raises, and the caller still gets the QA note.
    assert "Visual QA" in note
    findings = [c for c in calls if c["kind"] == "preview_qa_persist_failed"]
    assert len(findings) == 1
    assert findings[0].get("severity") == "info"
    assert "preview_qa_persist_failed" in findings[0]["dedup_key"]


async def test_preview_qa_missing_pool_emits_finding(monkeypatch):
    """The deliberate fail-loud guard is surfaced, not silently neutralised."""
    calls = _capture(monkeypatch)
    _patch_qa(monkeypatch)
    db = SimpleNamespace(cloud_pool=None, pool=None)

    await _run(db)

    findings = [c for c in calls if c["kind"] == "preview_qa_persist_failed"]
    assert len(findings) == 1


async def test_preview_qa_persist_success_emits_no_finding(monkeypatch):
    calls = _capture(monkeypatch)
    _patch_qa(monkeypatch)
    pool = _Pool(raises=False)
    db = SimpleNamespace(cloud_pool=None, pool=pool)

    note = await _run(db)

    assert "Visual QA" in note
    assert pool.executed, "expected the content_tasks UPDATE to run"
    assert calls == []
