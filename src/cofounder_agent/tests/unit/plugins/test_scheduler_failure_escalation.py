"""Unit tests for PluginScheduler._escalate_job_failure (#302 / alert audit).

Pins the contract that a failed scheduled job is made LOUD, not swallowed:
- ordinary jobs -> emit_finding (deduped findings pipeline)
- alert-delivery jobs -> direct notify_operator (circular-safe), but only after
  N consecutive failed ticks ("exhaust before paging", #831), with cooldown
- master switch off -> silent (operator opt-out)
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from plugins.scheduler import PluginScheduler


def _scheduler(*, alert_enabled: bool = True, page_threshold: int = 2) -> PluginScheduler:
    sc = MagicMock()
    sc.get_bool.return_value = alert_enabled
    # scheduler_circular_job_page_threshold — consecutive failures before a
    # circular-safe job direct-pages (default 2). See _escalate_job_failure.
    sc.get_int.return_value = page_threshold
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
async def test_circular_safe_single_failure_holds_page():
    """A lone failed tick is usually a transient deploy race — don't page yet."""
    sched = _scheduler()  # threshold 2
    with patch("utils.findings.emit_finding") as emit, \
         patch("services.integrations.operator_notify.notify_operator", new=AsyncMock()) as notify:
        await sched._escalate_job_failure("render_alertmanager_config", "[Errno 2]")
    notify.assert_not_awaited()  # first consecutive failure is held
    emit.assert_not_called()  # and it must NOT route through findings either


@pytest.mark.unit
@pytest.mark.asyncio
async def test_alerting_infra_job_pages_after_consecutive_threshold():
    sched = _scheduler()  # threshold 2
    with patch("utils.findings.emit_finding") as emit, \
         patch("services.integrations.operator_notify.notify_operator", new=AsyncMock()) as notify:
        await sched._escalate_job_failure("findings_alert_router", "DB down")  # 1/2 — hold
        await sched._escalate_job_failure("findings_alert_router", "DB down")  # 2/2 — page
    notify.assert_awaited_once()
    assert notify.await_args.kwargs.get("critical") is True
    emit.assert_not_called()  # circular-safe: must NOT route through findings


@pytest.mark.unit
@pytest.mark.asyncio
async def test_circular_safe_success_resets_streak():
    """Any successful tick resets the streak, so a later lone failure holds again."""
    sched = _scheduler()  # threshold 2
    with patch("services.integrations.operator_notify.notify_operator", new=AsyncMock()) as notify:
        await sched._escalate_job_failure("render_alertmanager_config", "x")  # 1/2 — hold
        sched._reset_circular_failure_streak("render_alertmanager_config")     # success
        await sched._escalate_job_failure("render_alertmanager_config", "x")  # 1/2 again — hold
    notify.assert_not_awaited()  # never reached 2 consecutive because success reset it


@pytest.mark.unit
@pytest.mark.asyncio
async def test_circular_safe_notify_is_cooled_down():
    sched = _scheduler()  # threshold 2
    with patch("services.integrations.operator_notify.notify_operator", new=AsyncMock()) as notify:
        await sched._escalate_job_failure("render_alertmanager_config", "EACCES")  # 1/2 — hold
        await sched._escalate_job_failure("render_alertmanager_config", "EACCES")  # 2/2 — page
        await sched._escalate_job_failure("render_alertmanager_config", "EACCES")  # 3/2 — cooldown
    notify.assert_awaited_once()  # third call suppressed by cooldown, not re-paged


@pytest.mark.unit
@pytest.mark.asyncio
async def test_circular_page_threshold_is_configurable():
    sched = _scheduler(page_threshold=3)
    with patch("services.integrations.operator_notify.notify_operator", new=AsyncMock()) as notify:
        await sched._escalate_job_failure("render_prometheus_rules", "x")  # 1/3 — hold
        await sched._escalate_job_failure("render_prometheus_rules", "x")  # 2/3 — hold
        assert notify.await_count == 0
        await sched._escalate_job_failure("render_prometheus_rules", "x")  # 3/3 — page
    notify.assert_awaited_once()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_threshold_one_restores_immediate_page():
    """threshold=1 (or a misconfigured 0, floored to 1) pages on the first tick."""
    sched = _scheduler(page_threshold=1)
    with patch("services.integrations.operator_notify.notify_operator", new=AsyncMock()) as notify:
        await sched._escalate_job_failure("render_alertmanager_config", "x")  # 1/1 — page
    notify.assert_awaited_once()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_master_switch_off_is_silent():
    sched = _scheduler(alert_enabled=False)
    with patch("utils.findings.emit_finding") as emit, \
         patch("services.integrations.operator_notify.notify_operator", new=AsyncMock()) as notify:
        await sched._escalate_job_failure("findings_alert_router", "x")
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


@pytest.mark.unit
def test_circular_page_threshold_default_is_seeded():
    """The production threshold ships in settings_defaults (every-tunable-in-
    app_settings rule); default 2 = ride out one transient deploy-race tick."""
    from services.settings_defaults import DEFAULTS

    assert DEFAULTS["scheduler_circular_job_page_threshold"] == "2"
    assert int(DEFAULTS["scheduler_circular_job_page_threshold"]) >= 1
