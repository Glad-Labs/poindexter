"""Unit tests for ``services/jobs/cancel_orphaned_social_drafts.py``.

SocialDraftsService mocked. Focus: no-_site_config skip, disabled no-op,
cancel-count reporting, and query-failure surfacing.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.jobs.cancel_orphaned_social_drafts import CancelOrphanedSocialDraftsJob


def _site_config(settings: dict[str, str]) -> MagicMock:
    sc = MagicMock()
    sc.get.side_effect = lambda key, default="": settings.get(key, default)
    return sc


def _patch_svc(cancel_return=0, cancel_raises: BaseException | None = None):
    """Patch SocialDraftsService so its instance's reaper is stubbed."""
    inst = MagicMock()
    if cancel_raises is not None:
        inst.cancel_orphaned_for_rejected_tasks = AsyncMock(side_effect=cancel_raises)
    else:
        inst.cancel_orphaned_for_rejected_tasks = AsyncMock(return_value=cancel_return)
    ctor = MagicMock(return_value=inst)
    return patch("services.social_drafts.SocialDraftsService", ctor), inst


@pytest.mark.asyncio
async def test_no_site_config_returns_not_ok():
    job = CancelOrphanedSocialDraftsJob()
    result = await job.run(pool=MagicMock(), config={})
    assert result.ok is False
    assert "_site_config" in result.detail


@pytest.mark.asyncio
async def test_disabled_is_noop_and_never_touches_db():
    job = CancelOrphanedSocialDraftsJob()
    cfg = {"_site_config": _site_config({"social_drafts_enabled": "false"})}
    ctx, inst = _patch_svc()
    with ctx:
        result = await job.run(pool=MagicMock(), config=cfg)
    assert result.ok is True
    assert "no-op" in result.detail
    inst.cancel_orphaned_for_rejected_tasks.assert_not_called()


@pytest.mark.asyncio
async def test_cancels_and_reports_count():
    job = CancelOrphanedSocialDraftsJob()
    cfg = {"_site_config": _site_config({"social_drafts_enabled": "true"})}
    ctx, inst = _patch_svc(cancel_return=3)
    with ctx:
        result = await job.run(pool=MagicMock(), config=cfg)
    assert result.ok is True
    assert "3" in result.detail
    inst.cancel_orphaned_for_rejected_tasks.assert_awaited_once()


@pytest.mark.asyncio
async def test_zero_cancelled_still_ok():
    job = CancelOrphanedSocialDraftsJob()
    cfg = {"_site_config": _site_config({"social_drafts_enabled": "1"})}
    ctx, _inst = _patch_svc(cancel_return=0)
    with ctx:
        result = await job.run(pool=MagicMock(), config=cfg)
    assert result.ok is True
    assert "cancelled 0" in result.detail


@pytest.mark.asyncio
async def test_query_failure_returns_not_ok():
    job = CancelOrphanedSocialDraftsJob()
    cfg = {"_site_config": _site_config({"social_drafts_enabled": "true"})}
    ctx, _inst = _patch_svc(cancel_raises=RuntimeError("boom"))
    with ctx:
        result = await job.run(pool=MagicMock(), config=cfg)
    assert result.ok is False
    assert "boom" in result.detail
