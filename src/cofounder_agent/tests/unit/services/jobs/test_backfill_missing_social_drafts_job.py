"""Unit tests for services/jobs/backfill_missing_social_drafts.py.

SocialDraftsService mocked. Focus: no-_site_config skip, disabled no-op,
result-reporting (changes_made = real drafts created, not posts checked),
per-task-error notify_operator, query-failure surfacing, and the
lookback_days setting being read and forwarded (poindexter#863).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.jobs.backfill_missing_social_drafts import (
    BackfillMissingSocialDraftsJob,
)


def _site_config(settings: dict[str, str]) -> MagicMock:
    sc = MagicMock()
    sc.get.side_effect = lambda key, default="": settings.get(key, default)
    return sc


def _patch_svc(reconcile_return=None, reconcile_raises: BaseException | None = None):
    """Patch SocialDraftsService so its instance's reconcile is stubbed."""
    inst = MagicMock()
    if reconcile_raises is not None:
        inst.reconcile_missing_drafts = AsyncMock(side_effect=reconcile_raises)
    else:
        inst.reconcile_missing_drafts = AsyncMock(
            return_value=reconcile_return
            or {"candidates_checked": 0, "drafts_created": 0, "errors": []}
        )
    ctor = MagicMock(return_value=inst)
    return patch("services.social_drafts.SocialDraftsService", ctor), inst


@pytest.mark.asyncio
async def test_no_site_config_returns_not_ok():
    job = BackfillMissingSocialDraftsJob()
    result = await job.run(pool=MagicMock(), config={})
    assert result.ok is False
    assert "_site_config" in result.detail


@pytest.mark.asyncio
async def test_disabled_is_noop_and_never_touches_db():
    job = BackfillMissingSocialDraftsJob()
    cfg = {"_site_config": _site_config({"social_drafts_enabled": "false"})}
    ctx, inst = _patch_svc()
    with ctx:
        result = await job.run(pool=MagicMock(), config=cfg)
    assert result.ok is True
    assert "no-op" in result.detail
    inst.reconcile_missing_drafts.assert_not_called()


@pytest.mark.asyncio
async def test_happy_path_reports_drafts_created_not_posts_checked():
    job = BackfillMissingSocialDraftsJob()
    cfg = {"_site_config": _site_config({"social_drafts_enabled": "true"})}
    ctx, inst = _patch_svc(
        reconcile_return={"candidates_checked": 5, "drafts_created": 2, "errors": []}
    )
    with ctx, patch(
        "services.jobs.backfill_missing_social_drafts.notify_operator",
        new_callable=AsyncMock,
    ) as notify:
        result = await job.run(pool=MagicMock(), config=cfg)
    assert result.ok is True
    assert result.changes_made == 2
    assert "5" in result.detail and "2" in result.detail
    inst.reconcile_missing_drafts.assert_awaited_once()
    notify.assert_not_awaited()


@pytest.mark.asyncio
async def test_per_task_errors_trigger_notify_operator():
    job = BackfillMissingSocialDraftsJob()
    cfg = {"_site_config": _site_config({"social_drafts_enabled": "true"})}
    ctx, _inst = _patch_svc(
        reconcile_return={
            "candidates_checked": 3,
            "drafts_created": 1,
            "errors": ["task-1: boom"],
        }
    )
    with ctx, patch(
        "services.jobs.backfill_missing_social_drafts.notify_operator",
        new_callable=AsyncMock,
    ) as notify:
        result = await job.run(pool=MagicMock(), config=cfg)
    assert result.ok is True
    notify.assert_awaited_once()
    assert notify.await_args.kwargs["critical"] is False


@pytest.mark.asyncio
async def test_reconcile_failure_returns_not_ok():
    job = BackfillMissingSocialDraftsJob()
    cfg = {"_site_config": _site_config({"social_drafts_enabled": "true"})}
    ctx, _inst = _patch_svc(reconcile_raises=RuntimeError("db down"))
    with ctx:
        result = await job.run(pool=MagicMock(), config=cfg)
    assert result.ok is False
    assert "db down" in result.detail


@pytest.mark.asyncio
async def test_lookback_days_setting_is_read_and_passed():
    job = BackfillMissingSocialDraftsJob()
    cfg = {
        "_site_config": _site_config(
            {
                "social_drafts_enabled": "true",
                "social_draft_backfill_lookback_days": "30",
            }
        )
    }
    ctx, inst = _patch_svc()
    with ctx:
        await job.run(pool=MagicMock(), config=cfg)
    inst.reconcile_missing_drafts.assert_awaited_once()
    assert inst.reconcile_missing_drafts.await_args.args[-1] == 30
