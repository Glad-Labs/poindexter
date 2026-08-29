"""Unit tests for services/jobs/fanout_candidate_prune.py.

The object store is stubbed throughout — this job DELETES, and a unit run must
never reach a real bucket (the operator box has live credentials).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.jobs.fanout_candidate_prune import (
    _MIN_RETENTION_DAYS,
    _PREFIX,
    FanoutCandidatePruneJob,
)

NOW = datetime.now(timezone.utc)


def _sc(**over):
    cfg = dict(over)

    class _SC:
        def get(self, key, default=None):
            val = cfg.get(key)
            return default if val in (None, "") else val

    return _SC()


def _obj(key, days_old, size=1024):
    return {"key": key, "size": size,
            "last_modified": NOW - timedelta(days=days_old)}


def _svc(objects, *, deleted_ok=True, delete_exc=None):
    svc = MagicMock()
    svc.list_objects = AsyncMock(return_value=objects)
    if delete_exc is not None:
        svc.delete_object = AsyncMock(side_effect=delete_exc)
    else:
        svc.delete_object = AsyncMock(return_value=deleted_ok)
    return svc


async def _run(svc, **settings):
    with patch("services.r2_upload_service.R2UploadService",
               MagicMock(return_value=svc)):
        return await FanoutCandidatePruneJob().run(
            MagicMock(), {"_site_config": _sc(**settings)})


@pytest.mark.unit
def test_deletes_are_serialized_not_concurrent():
    """idempotent=False maps to apscheduler max_instances=1. An idempotent job
    gets max_instances=3, which for a delete pass means two sweeps racing over
    the same keys."""
    assert FanoutCandidatePruneJob.idempotent is False


@pytest.mark.unit
@pytest.mark.asyncio
async def test_prunes_only_objects_past_the_ttl():
    svc = _svc([
        _obj(f"{_PREFIX}20260101/t1/schnell-101010.png", 200),
        _obj(f"{_PREFIX}20260101/t1/klein-101010.png", 120),
        _obj(f"{_PREFIX}20260820/t2/qwen-101010.png", 5),   # young: keep
    ])
    res = await _run(svc, image_fanout_candidate_retention_days="90")
    assert res.ok is True
    assert res.changes_made == 2
    deleted = {c.args[0] for c in svc.delete_object.await_args_list}
    assert all("20260101" in k for k in deleted)
    assert res.metrics["stale"] == 2


@pytest.mark.unit
@pytest.mark.asyncio
async def test_never_deletes_outside_the_fanout_prefix():
    """The guard is re-asserted per key at the DELETE site, so a listing that
    somehow returns a foreign key cannot widen the blast radius."""
    svc = _svc([
        _obj("images/featured/live-post.jpg", 400),
        _obj("podcast/ep1.mp3", 400),
        _obj(f"{_PREFIX}20260101/t1/schnell-101010.png", 400),
    ])
    res = await _run(svc, image_fanout_candidate_retention_days="90")
    deleted = [c.args[0] for c in svc.delete_object.await_args_list]
    assert deleted == [f"{_PREFIX}20260101/t1/schnell-101010.png"]
    assert res.changes_made == 1


@pytest.mark.unit
@pytest.mark.asyncio
async def test_refuses_below_the_retention_floor():
    """A fat-fingered 0 reads as 'delete everything'. It must change nothing
    and say so, rather than silently substituting a default."""
    svc = _svc([_obj(f"{_PREFIX}20260101/t1/a-1.png", 400)])
    res = await _run(svc, image_fanout_candidate_retention_days="0")
    assert res.ok is False
    assert res.changes_made == 0
    assert "floor" in res.detail
    svc.list_objects.assert_not_awaited()
    svc.delete_object.assert_not_awaited()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_floor_boundary_is_allowed():
    svc = _svc([])
    res = await _run(svc,
                     image_fanout_candidate_retention_days=str(_MIN_RETENTION_DAYS))
    assert res.ok is True
    svc.list_objects.assert_awaited_once()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_per_run_cap_bounds_the_delete_and_reports_the_remainder():
    svc = _svc([_obj(f"{_PREFIX}20260101/t{i}/a-1.png", 400) for i in range(10)])
    res = await _run(svc, image_fanout_candidate_retention_days="90",
                     image_fanout_candidate_prune_max_deletes_per_run="3")
    assert res.changes_made == 3
    assert res.metrics["over_cap"] == 7
    assert "next pass continues" in res.detail


@pytest.mark.unit
@pytest.mark.asyncio
async def test_object_without_a_timestamp_is_kept():
    """Cannot prove it is old => keep. Retention errs toward keeping an image,
    never toward deleting an unknown."""
    svc = _svc([{"key": f"{_PREFIX}20260101/t1/a-1.png", "size": 10,
                 "last_modified": None}])
    res = await _run(svc, image_fanout_candidate_retention_days="90")
    assert res.changes_made == 0
    svc.delete_object.assert_not_awaited()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_listing_failure_is_reported_not_raised():
    svc = MagicMock()
    svc.list_objects = AsyncMock(side_effect=RuntimeError("store down"))
    res = await _run(svc, image_fanout_candidate_retention_days="90")
    assert res.ok is False
    assert "store down" in res.detail
    assert res.changes_made == 0


@pytest.mark.unit
@pytest.mark.asyncio
async def test_one_failed_delete_does_not_abandon_the_sweep():
    keys = [_obj(f"{_PREFIX}20260101/t{i}/a-1.png", 400) for i in range(3)]
    svc = _svc(keys)
    svc.delete_object = AsyncMock(side_effect=[True, RuntimeError("nope"), True])
    res = await _run(svc, image_fanout_candidate_retention_days="90")
    assert res.changes_made == 2
    assert res.metrics["failures"] == 1
    assert res.ok is False


@pytest.mark.unit
@pytest.mark.asyncio
async def test_missing_site_config_refuses_rather_than_guessing():
    """No SiteConfig means no resolvable retention window; guessing one is the
    silent default that must never gate a delete."""
    svc = _svc([_obj(f"{_PREFIX}20260101/t1/a-1.png", 400)])
    with patch("services.r2_upload_service.R2UploadService",
               MagicMock(return_value=svc)):
        res = await FanoutCandidatePruneJob().run(MagicMock(), {})
    assert res.ok is False
    assert res.changes_made == 0
    svc.delete_object.assert_not_awaited()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_empty_store_is_a_clean_noop():
    """Inert for the first 90 days of retained data by construction."""
    svc = _svc([])
    res = await _run(svc, image_fanout_candidate_retention_days="90")
    assert res.ok is True
    assert res.changes_made == 0
    assert res.metrics["listed"] == 0
