"""A gpu_lock_timeout finding must name WHO lost the work (poindexter#914).

Measured 2026-09-18 over a 30-day window: 429 `gpu_lock_timeout` findings, of
which **408 were `owner=ollama, stage=in_process` and nothing more**. A writer
burning its full budget and an advisory rail giving up produced the identical
row, so "which work is being silently lost?" — the question #914's remaining
phase (deferred completion) exists to answer — was unanswerable from the data.

The load-bearing field is `max_wait_s`. Admission (`services/gpu_admission.py`)
only engages when a caller declares a budget, so `None` means the caller went
straight to the raw lock and waited out the ceiling. The two rates diverge
sharply across consecutive 30-day windows — admission rejections 819 → 41 while
lock timeouts held 445 → 429 — which is the signature of waiters bypassing
admission rather than admission working.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from poindexter.services.gpu_scheduler import GPUScheduler

pytestmark = pytest.mark.unit

_MODULE = "poindexter.utils.findings.emit_finding"


def _emit(**over):
    kwargs = {
        "owner": "ollama",
        "stage": "in_process",
        "timeout_s": 900.0,
        "holder": None,
    }
    kwargs.update(over)
    with patch(_MODULE) as mock:
        GPUScheduler()._emit_lock_timeout_finding(**kwargs)
    assert mock.call_count == 1
    return mock.call_args.kwargs


class TestTimeoutAttribution:
    def test_extra_carries_phase_task_and_priority(self):
        extra = _emit(
            phase="qa_vision", task_id="4a23f39e", priority="background",
            max_wait_s=45.0,
        )["extra"]
        assert extra["phase"] == "qa_vision"
        assert extra["task_id"] == "4a23f39e"
        assert extra["priority"] == "background"

    def test_declared_budget_marks_admission_engaged(self):
        extra = _emit(max_wait_s=45.0)["extra"]
        assert extra["max_wait_s"] == 45.0
        assert extra["admission_engaged"] is True

    def test_no_budget_marks_admission_NOT_engaged(self):
        """The distinction the 30-day audit could not make: admission judged
        this hopeless, versus admission never ran at all."""
        extra = _emit(max_wait_s=None)["extra"]
        assert extra["max_wait_s"] is None
        assert extra["admission_engaged"] is False

    def test_context_is_optional_so_no_call_site_breaks(self):
        extra = _emit()["extra"]
        assert extra["owner"] == "ollama" and extra["stage"] == "in_process"
        assert extra["phase"] is None and extra["admission_engaged"] is False

    def test_dedup_key_stays_owner_scoped(self):
        """390 ollama timeouts must not become 390 alerts. Dedup throttles
        DELIVERY; every row still lands in audit_log, so the new detail is
        queryable without changing alert volume."""
        a = _emit(phase="qa_vision", task_id="aaa")
        b = _emit(phase="generate_draft", task_id="bbb")
        assert a["dedup_key"] == b["dedup_key"] == "gpu-lock-timeout:ollama"

    def test_severity_and_kind_unchanged(self):
        kw = _emit()
        assert kw["kind"] == "gpu_lock_timeout"
        assert kw["severity"] == "warn"

    def test_emitter_never_raises(self):
        """A diagnostic must never take down the lock path it describes."""
        with patch(_MODULE, side_effect=RuntimeError("audit sink down")):
            GPUScheduler()._emit_lock_timeout_finding(
                owner="ollama", stage="in_process", timeout_s=900.0, holder=None,
            )
