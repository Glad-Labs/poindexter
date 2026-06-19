"""Unit tests for the media.persist Stage-2 atom (epic poindexter#689, Plan 8).

Pins the contract: after the renders complete, move the long + short MP4s out of
the OS temp dir into the durable media dir (``VIDEO_DIR``) and record a
task-keyed ``media_assets`` row for each via the canonical
``media_asset_recorder.record_media_asset`` writer. Best-effort throughout — a
missing render or a no-pool environment never raises (the renders already emit
their own findings on failure).

``media_assets`` is task-keyed here (``post_id=None``): the post may not exist
yet at Stage-2 time. The post-keyed Gate-2 approval + distribution lane resolves
the post later (Plan 8 / 8b-2).
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from modules.content.atoms import media_persist
from modules.content.atoms.media_persist import run as persist_run


def _write_tmp(path) -> str:
    """Create a small fake render file and return its path."""
    path.write_bytes(b"\x00" * 2048)
    return str(path)


class _FakeConn:
    """Connection stand-in whose ``fetch`` answers the idempotency-guard query
    with the configured set of already-recorded asset types."""

    def __init__(self, existing_types):
        self._existing_types = set(existing_types)

    async def fetch(self, _sql, *_args):
        return [{"type": t} for t in self._existing_types]


class _FakeAcquire:
    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, *exc):
        return False


class _FakePool:
    """asyncpg-pool stand-in for media.persist tests. ``existing_types`` seeds the
    guard query so a flavor already on the task is skipped (idempotency)."""

    def __init__(self, existing_types=()):
        self._conn = _FakeConn(existing_types)

    def acquire(self):
        return _FakeAcquire(self._conn)


@pytest.fixture
def patched_recorder(monkeypatch):
    """Patch the canonical media_assets writer + the durable dir.

    Returns (recorder_mock, durable_dir). The recorder returns a fresh fake id
    per call so the atom's return value is assertable.
    """
    ids = iter(["asset-long", "asset-short", "asset-3", "asset-4"])
    rec = AsyncMock(side_effect=lambda **kw: next(ids))
    monkeypatch.setattr(media_persist, "record_media_asset", rec)
    return rec


@pytest.mark.asyncio
async def test_persist_moves_renders_and_records_both_assets(
    tmp_path, monkeypatch, patched_recorder
):
    durable = tmp_path / "durable_video"
    monkeypatch.setattr("services.video_service.VIDEO_DIR", durable)

    long_src = _write_tmp(tmp_path / "media_t1_long_video_path.mp4")
    short_src = _write_tmp(tmp_path / "media_t1_short_video_path.mp4")
    pool = _FakePool()

    state = {
        "task_id": "t1",
        "pool": pool,
        "long_video_path": long_src,
        "short_video_path": short_src,
        "video_shot_list": {"aspect": "16:9", "total_duration_s": 90.0},
        "short_shot_list": {"aspect": "9:16", "total_duration_s": 30.0},
    }
    result = await persist_run(state)

    # Files moved into the durable dir under task-keyed names; temp gone.
    assert (durable / "t1.mp4").exists()
    assert (durable / "t1_short.mp4").exists()
    import os
    assert not os.path.exists(long_src)
    assert not os.path.exists(short_src)

    # One media_assets row per asset, task-keyed (post_id=None).
    assert patched_recorder.await_count == 2
    kinds = {c.kwargs["asset_type"] for c in patched_recorder.await_args_list}
    assert kinds == {"video", "video_short"}
    for c in patched_recorder.await_args_list:
        assert c.kwargs["task_id"] == "t1"
        assert c.kwargs["post_id"] is None
        assert c.kwargs["storage_provider"] == "local"

    assert result["media_assets_recorded"] == ["asset-long", "asset-short"]


@pytest.mark.asyncio
async def test_persist_derives_dims_and_duration_from_shot_list(
    tmp_path, monkeypatch, patched_recorder
):
    durable = tmp_path / "durable_video"
    monkeypatch.setattr("services.video_service.VIDEO_DIR", durable)
    _write_tmp(tmp_path / "long.mp4")

    state = {
        "task_id": "t2",
        "pool": _FakePool(),
        "long_video_path": str(tmp_path / "long.mp4"),
        "short_video_path": "",
        "video_shot_list": {"aspect": "9:16", "total_duration_s": 12.5},
    }
    await persist_run(state)

    call = patched_recorder.await_args_list[0]
    # 9:16 vertical → 1080×1920; duration in ms.
    assert call.kwargs["width"] == 1080
    assert call.kwargs["height"] == 1920
    assert call.kwargs["duration_ms"] == 12500
    assert call.kwargs["file_size_bytes"] > 0


@pytest.mark.asyncio
async def test_persist_skips_missing_or_empty_renders(
    tmp_path, monkeypatch, patched_recorder
):
    """Empty long path + a short path pointing at a non-existent file → no
    records, empty result. The renders own their own failure findings."""
    durable = tmp_path / "durable_video"
    monkeypatch.setattr("services.video_service.VIDEO_DIR", durable)

    state = {
        "task_id": "t3",
        "pool": _FakePool(),
        "long_video_path": "",
        "short_video_path": str(tmp_path / "does_not_exist.mp4"),
    }
    result = await persist_run(state)

    patched_recorder.assert_not_awaited()
    assert result["media_assets_recorded"] == []


@pytest.mark.asyncio
async def test_persist_no_pool_is_best_effort(tmp_path, monkeypatch, patched_recorder):
    durable = tmp_path / "durable_video"
    monkeypatch.setattr("services.video_service.VIDEO_DIR", durable)
    _write_tmp(tmp_path / "long.mp4")

    state = {
        "task_id": "t4",
        "long_video_path": str(tmp_path / "long.mp4"),
        "short_video_path": "",
    }
    result = await persist_run(state)  # no pool / no database_service

    patched_recorder.assert_not_awaited()
    assert result["media_assets_recorded"] == []


@pytest.mark.asyncio
async def test_persist_pool_from_database_service(tmp_path, monkeypatch, patched_recorder):
    durable = tmp_path / "durable_video"
    monkeypatch.setattr("services.video_service.VIDEO_DIR", durable)
    _write_tmp(tmp_path / "long.mp4")
    pool = _FakePool()

    state = {
        "task_id": "t5",
        "database_service": SimpleNamespace(pool=pool),
        "long_video_path": str(tmp_path / "long.mp4"),
        "short_video_path": "",
        "video_shot_list": {"aspect": "16:9"},
    }
    await persist_run(state)

    assert patched_recorder.await_args_list[0].kwargs["pool"] is pool


@pytest.mark.asyncio
async def test_persist_skips_flavor_already_recorded(
    tmp_path, monkeypatch, patched_recorder
):
    """Idempotency guard: a graph re-execution for a task that already recorded
    the long video must NOT re-persist it (no duplicate task-keyed row); the
    not-yet-recorded short still persists."""
    durable = tmp_path / "durable_video"
    monkeypatch.setattr("services.video_service.VIDEO_DIR", durable)

    long_src = _write_tmp(tmp_path / "media_t6_long_video_path.mp4")
    short_src = _write_tmp(tmp_path / "media_t6_short_video_path.mp4")

    state = {
        "task_id": "t6",
        "pool": _FakePool(existing_types={"video"}),  # long already recorded
        "long_video_path": long_src,
        "short_video_path": short_src,
        "video_shot_list": {"aspect": "16:9"},
        "short_shot_list": {"aspect": "9:16"},
    }
    result = await persist_run(state)

    # Only the short was recorded — the long was skipped by the guard.
    assert patched_recorder.await_count == 1
    assert patched_recorder.await_args_list[0].kwargs["asset_type"] == "video_short"
    # The skipped long render was left in temp (not moved into the durable dir).
    import os
    assert os.path.exists(long_src)
    assert not os.path.exists(short_src)
    assert (durable / "t6_short.mp4").exists()
    assert not (durable / "t6.mp4").exists()
    assert result["media_assets_recorded"] == ["asset-long"]


@pytest.mark.asyncio
async def test_persist_skips_all_flavors_when_both_recorded(
    tmp_path, monkeypatch, patched_recorder
):
    """A full re-execution for a task that already has both flavors is a complete
    no-op — nothing recorded, both renders left untouched in temp."""
    durable = tmp_path / "durable_video"
    monkeypatch.setattr("services.video_service.VIDEO_DIR", durable)

    long_src = _write_tmp(tmp_path / "media_t7_long_video_path.mp4")
    short_src = _write_tmp(tmp_path / "media_t7_short_video_path.mp4")

    state = {
        "task_id": "t7",
        "pool": _FakePool(existing_types={"video", "video_short"}),
        "long_video_path": long_src,
        "short_video_path": short_src,
    }
    result = await persist_run(state)

    patched_recorder.assert_not_awaited()
    import os
    assert os.path.exists(long_src)
    assert os.path.exists(short_src)
    assert result["media_assets_recorded"] == []


@pytest.mark.asyncio
async def test_persist_requires_task_id():
    with pytest.raises(ValueError):
        await persist_run({"long_video_path": "/tmp/x.mp4"})
