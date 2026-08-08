"""Unit tests for ScheduleSocialDraftsJob — the social schedule queue driver."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.jobs.schedule_social_drafts import ScheduleSocialDraftsJob

pytestmark = pytest.mark.unit


def _site_config(**overrides) -> MagicMock:
    settings = {"social_drafts_enabled": "true"}
    settings.update(overrides)
    sc = MagicMock()
    sc.get.side_effect = lambda key, default="": settings.get(key, default)
    return sc


def _svc(auto=None, fired=None) -> MagicMock:
    svc = MagicMock()
    svc.auto_schedule_ready_drafts = AsyncMock(
        return_value=auto or {"scheduled": 0, "detail": "off"}
    )
    svc.fire_due_drafts = AsyncMock(
        return_value=fired
        or {"due": 0, "posted": 0, "blocked": 0, "failed": 0, "overdue": 0}
    )
    return svc


def _patch_service(svc):
    return patch("services.social_drafts.SocialDraftsService", return_value=svc)


class TestMetadata:
    def test_minute_cadence_bounds_slot_resolution(self):
        """The poll interval IS the queue's resolution — a slower cadence
        means a promo scheduled for 9:00 can go out at 9:05."""
        assert ScheduleSocialDraftsJob.schedule == "every 1 minute"

    def test_is_idempotent(self):
        assert ScheduleSocialDraftsJob.idempotent is True


@pytest.mark.asyncio
async def test_noop_without_site_config():
    result = await ScheduleSocialDraftsJob().run(MagicMock(), {})
    assert result.ok is False
    assert "_site_config" in result.detail


@pytest.mark.asyncio
async def test_noop_when_social_drafts_disabled():
    svc = _svc()
    with _patch_service(svc):
        result = await ScheduleSocialDraftsJob().run(
            MagicMock(),
            {"_site_config": _site_config(social_drafts_enabled="false")},
        )
    assert result.ok is True
    assert "no-op" in result.detail
    svc.fire_due_drafts.assert_not_awaited()


@pytest.mark.asyncio
async def test_auto_slot_runs_before_fire():
    """Ordering matters: a draft slotted for publish+0m should go out on the
    same sweep, not wait a minute for the next one."""
    calls: list[str] = []
    svc = _svc()
    svc.auto_schedule_ready_drafts = AsyncMock(
        side_effect=lambda *a, **k: (
            calls.append("auto"), {"scheduled": 1, "detail": ""}
        )[1]
    )
    svc.fire_due_drafts = AsyncMock(
        side_effect=lambda *a, **k: (
            calls.append("fire"),
            {"due": 1, "posted": 1, "blocked": 0, "failed": 0, "overdue": 0},
        )[1]
    )
    with _patch_service(svc):
        result = await ScheduleSocialDraftsJob().run(
            MagicMock(), {"_site_config": _site_config()}
        )
    assert calls == ["auto", "fire"]
    assert result.changes_made == 2  # 1 slotted + 1 posted


@pytest.mark.asyncio
async def test_fire_still_runs_when_auto_schedule_raises():
    """Already-queued drafts are a live commitment with a time on them —
    a broken auto-slot pass must not strand them."""
    svc = _svc(fired={"due": 2, "posted": 2, "blocked": 0, "failed": 0, "overdue": 0})
    svc.auto_schedule_ready_drafts = AsyncMock(side_effect=RuntimeError("boom"))
    with _patch_service(svc):
        result = await ScheduleSocialDraftsJob().run(
            MagicMock(), {"_site_config": _site_config()}
        )
    svc.fire_due_drafts.assert_awaited_once()
    assert result.ok is True
    assert result.metrics["posted"] == 2


@pytest.mark.asyncio
async def test_fire_is_not_gated_on_auto_drip_being_enabled():
    """social_schedule_enabled governs AUTOMATIC slotting only.

    A draft the operator scheduled by hand must still fire with auto-drip off,
    or manual scheduling silently does nothing on a default install.
    """
    svc = _svc(
        auto={"scheduled": 0, "detail": "social_schedule_enabled=false"},
        fired={"due": 1, "posted": 1, "blocked": 0, "failed": 0, "overdue": 0},
    )
    with _patch_service(svc):
        result = await ScheduleSocialDraftsJob().run(
            MagicMock(),
            {"_site_config": _site_config(social_schedule_enabled="false")},
        )
    svc.fire_due_drafts.assert_awaited_once()
    assert result.metrics["posted"] == 1


@pytest.mark.asyncio
async def test_reports_every_outcome_bucket_in_metrics():
    """blocked/overdue must stay visible — they're the two "nothing went out
    and that was deliberate" cases, indistinguishable from success otherwise."""
    svc = _svc(
        fired={"due": 4, "posted": 1, "blocked": 1, "failed": 1, "overdue": 1}
    )
    with _patch_service(svc):
        result = await ScheduleSocialDraftsJob().run(
            MagicMock(), {"_site_config": _site_config()}
        )
    assert result.metrics == {
        "auto_scheduled": 0, "due": 4, "posted": 1,
        "blocked": 1, "failed": 1, "overdue": 1,
    }
    for bucket in ("blocked", "failed", "overdue"):
        assert bucket in result.detail


@pytest.mark.asyncio
async def test_fire_failure_surfaces_as_not_ok():
    svc = _svc()
    svc.fire_due_drafts = AsyncMock(side_effect=RuntimeError("postgres gone"))
    with _patch_service(svc):
        result = await ScheduleSocialDraftsJob().run(
            MagicMock(), {"_site_config": _site_config()}
        )
    assert result.ok is False
    assert "postgres gone" in result.detail
