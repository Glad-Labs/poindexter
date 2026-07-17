"""Unit tests — gpu_scheduler best-effort-failure visibility.

Silent-excepts OLD-debt burn-down (batch 2). Of the seven silent handlers the
whole-body matcher flagged in ``gpu_scheduler``, six are intentional swallows
that predate the ``# silent-ok`` marker (the two ``emit_finding`` fallbacks — you
cannot emit a finding about a finding that failed to emit; two connection-close
cleanups on already-reported / teardown paths; a core-dep import guard; and the
image-gen ``/unload`` call whose common case is a deliberately-offline server,
already covered by the nvidia-exporter finding). Those are annotated in place.

The one genuine gap is the ``gpu_task_sessions`` write in the lock-release
``finally``: on failure the per-task compute-economics row is silently dropped
with nothing else to signal it. It now emits a non-paging ``info`` finding while
still never gating the GPU lock lifecycle — a telemetry write must not wedge the
scheduler.

Mirrors ``tests/unit/modules/content/test_writer_core_silent_failures.py``.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from services.gpu_scheduler import GPUScheduler

pytestmark = pytest.mark.unit


def _capture(monkeypatch) -> list[dict]:
    calls: list[dict] = []
    import utils.findings as findings_module

    monkeypatch.setattr(findings_module, "emit_finding", lambda **kw: calls.append(kw))
    return calls


@pytest.mark.asyncio
async def test_task_session_write_failure_emits_finding_and_does_not_wedge_lock(
    monkeypatch,
):
    calls = _capture(monkeypatch)
    gpu = GPUScheduler()
    # The economics write is the only thing that fails; the lock must still
    # acquire and release cleanly around it.
    monkeypatch.setattr(
        gpu, "_record_task_session",
        AsyncMock(side_effect=RuntimeError("gpu_task_sessions insert boom")),
    )

    async with gpu.lock("ollama", model="qwen3.5", task_id="task-1", phase="draft"):
        assert gpu.is_busy

    # Lock lifecycle intact — the telemetry failure never gated it.
    assert not gpu.is_busy

    findings = [c for c in calls if c["kind"] == "gpu_task_session_write_failed"]
    assert len(findings) == 1
    assert findings[0]["dedup_key"] == "gpu_task_session_write_failed"
    assert findings[0].get("severity", "info") == "info"


@pytest.mark.asyncio
async def test_successful_task_session_write_emits_no_finding(monkeypatch):
    """The finding fires only on failure — a clean write is silent."""
    calls = _capture(monkeypatch)
    gpu = GPUScheduler()
    monkeypatch.setattr(gpu, "_record_task_session", AsyncMock(return_value=None))

    async with gpu.lock("ollama", model="qwen3.5", task_id="task-1"):
        pass

    assert not [c for c in calls if c["kind"] == "gpu_task_session_write_failed"]
