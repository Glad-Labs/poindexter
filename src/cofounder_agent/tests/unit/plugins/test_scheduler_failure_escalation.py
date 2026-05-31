"""Unit tests for PluginScheduler._escalate_job_failure (#302 / alert audit).

Pins the contract that a failed scheduled job is made LOUD, not swallowed:
- ordinary jobs -> emit_finding (deduped findings pipeline)
- alert-delivery jobs -> direct notify_operator (circular-safe), with cooldown
- master switch off -> silent (operator opt-out)
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from plugins.scheduler import PluginScheduler


def _scheduler(*, alert_enabled: bool = True) -> PluginScheduler:
    sc = MagicMock()
    sc.get_bool.return_value = alert_enabled
    return PluginScheduler(MagicMock(), site_config=sc)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_ordinary_job_failure_emits_finding():
    sched = _scheduler()
    with patch("utils.findings.emit_finding") as emit, \
         patch("services.integrations.operator_notify.notify_operator", new=AsyncMock()) as notify:
        await sched._escalate_job_failure("audit_published_quality", "boom")
    emit.assert_called_once()
    kwargs = emit.call_args.kwargs
    assert kwargs["kind"] == "job_failure"
    assert kwargs["severity"] == "warn"
    assert kwargs["dedup_key"] == "job-fail:audit_published_quality"
    notify.assert_not_called()  # ordinary jobs don't direct-notify


@pytest.mark.unit
@pytest.mark.asyncio
async def test_alerting_infra_job_failure_direct_notifies_critical():
    sched = _scheduler()
    with patch("utils.findings.emit_finding") as emit, \
         patch("services.integrations.operator_notify.notify_operator", new=AsyncMock()) as notify:
        await sched._escalate_job_failure("findings_alert_router", "DB down")
    notify.assert_awaited_once()
    assert notify.await_args.kwargs.get("critical") is True
    emit.assert_not_called()  # circular-safe: must NOT route through findings


@pytest.mark.unit
@pytest.mark.asyncio
async def test_circular_safe_notify_is_cooled_down():
    sched = _scheduler()
    with patch("services.integrations.operator_notify.notify_operator", new=AsyncMock()) as notify:
        await sched._escalate_job_failure("render_alertmanager_config", "EACCES")
        await sched._escalate_job_failure("render_alertmanager_config", "EACCES")  # within cooldown
    notify.assert_awaited_once()  # second call suppressed by cooldown


@pytest.mark.unit
@pytest.mark.asyncio
async def test_master_switch_off_is_silent():
    sched = _scheduler(alert_enabled=False)
    with patch("utils.findings.emit_finding") as emit, \
         patch("services.integrations.operator_notify.notify_operator", new=AsyncMock()) as notify:
        await sched._escalate_job_failure("findings_alert_router", "x")
        await sched._escalate_job_failure("audit_published_quality", "y")
    emit.assert_not_called()
    notify.assert_not_called()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_escalation_never_raises_into_loop():
    sched = _scheduler()
    # emit_finding blows up -> _escalate must swallow + log, not propagate.
    with patch("utils.findings.emit_finding", side_effect=RuntimeError("kaboom")):
        await sched._escalate_job_failure("some_job", "detail")  # must not raise
