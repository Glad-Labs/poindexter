"""Unit tests for ``services/jobs/backfill_videos.py``.

Cloud asyncpg connection + filesystem checks are mocked. Covers the
"podcast exists && video doesn't" gate and the GPU-bound max_per_cycle
cap.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.jobs.backfill_videos import BackfillVideosJob


def _sc(value: Any = "") -> MagicMock:
    """Build a mock SiteConfig that returns ``value`` from any ``.get()`` call.

    Replaces the legacy ``patch("services.site_config.site_config.get", ...)``
    pattern after the job migrated to the DI seam (glad-labs-stack#330).
    """
    sc = MagicMock()
    sc.get.return_value = value
    return sc


def _fake_asyncpg(rows: list[dict] | None = None):
    cloud_conn = AsyncMock()
    cloud_conn.fetch = AsyncMock(return_value=rows or [])
    cloud_conn.close = AsyncMock(return_value=None)

    async def _connect(url: str) -> Any:
        return cloud_conn

    fake = MagicMock()
    fake.connect = _connect
    return fake, cloud_conn


def _row(post_id: str = "abc123", title: str = "Hello") -> dict:
    return {"id": post_id, "title": title, "content": "Body text"}


@pytest.mark.unit
class TestBackfillVideosJobMetadata:
    def test_name(self):
        assert BackfillVideosJob.name == "backfill_videos"

    def test_schedule(self):
        assert "6 hours" in BackfillVideosJob.schedule

    def test_idempotent(self):
        assert BackfillVideosJob.idempotent is True


@pytest.mark.unit
@pytest.mark.asyncio
class TestBackfillVideosJobRun:
    async def test_skips_when_no_database_url(self):
        job = BackfillVideosJob()
        result = await job.run(MagicMock(), {"_site_config": _sc("")})
        assert result.ok is True
        assert result.changes_made == 0
        assert "no database_url" in result.detail

    async def test_skips_when_asyncpg_unavailable(self):
        job = BackfillVideosJob()
        with patch.dict("sys.modules", {"asyncpg": None}):
            result = await job.run(MagicMock(), {"_site_config": _sc("postgres://cloud")})
        assert result.ok is False
        assert "asyncpg" in result.detail

    async def test_skips_post_when_podcast_missing(self, tmp_path):
        """Video generation requires a podcast — skip posts without one."""
        job = BackfillVideosJob()
        fake_asyncpg, _ = _fake_asyncpg(rows=[_row("p1")])

        with patch.dict("sys.modules", {"asyncpg": fake_asyncpg}), \
             patch("services.video_service.generate_video_for_post",
                   new=AsyncMock()) as gen_mock, \
             patch("services.podcast_service.PODCAST_DIR", tmp_path / "podcasts"), \
             patch("services.video_service.VIDEO_DIR", tmp_path / "videos"):
            (tmp_path / "podcasts").mkdir(parents=True)
            (tmp_path / "videos").mkdir(parents=True)
            result = await job.run(MagicMock(), {"_site_config": _sc("postgres://cloud")})

        assert result.ok is True
        assert result.changes_made == 0
        gen_mock.assert_not_awaited()

    async def test_skips_post_when_video_already_exists(self, tmp_path):
        job = BackfillVideosJob()
        fake_asyncpg, _ = _fake_asyncpg(rows=[_row("p1")])

        (tmp_path / "podcasts").mkdir(parents=True)
        (tmp_path / "videos").mkdir(parents=True)
        # Podcast AND video already exist → skip.
        (tmp_path / "podcasts" / "p1.mp3").write_bytes(b"fake")
        (tmp_path / "videos" / "p1.mp4").write_bytes(b"fake")

        with patch.dict("sys.modules", {"asyncpg": fake_asyncpg}), \
             patch("services.video_service.generate_video_for_post",
                   new=AsyncMock()) as gen_mock, \
             patch("services.podcast_service.PODCAST_DIR", tmp_path / "podcasts"), \
             patch("services.video_service.VIDEO_DIR", tmp_path / "videos"):
            result = await job.run(MagicMock(), {"_site_config": _sc("postgres://cloud")})

        assert result.ok is True
        assert result.changes_made == 0
        gen_mock.assert_not_awaited()

    async def test_generates_video_when_podcast_present_video_missing(self, tmp_path):
        job = BackfillVideosJob()
        fake_asyncpg, _ = _fake_asyncpg(rows=[_row("p1", "My Post")])

        (tmp_path / "podcasts").mkdir(parents=True)
        (tmp_path / "videos").mkdir(parents=True)
        (tmp_path / "podcasts" / "p1.mp3").write_bytes(b"fake podcast")

        gen_result = MagicMock(success=True)
        with patch.dict("sys.modules", {"asyncpg": fake_asyncpg}), \
             patch("services.video_service.generate_video_for_post",
                   new=AsyncMock(return_value=gen_result)) as gen_mock, \
             patch("services.podcast_service.PODCAST_DIR", tmp_path / "podcasts"), \
             patch("services.video_service.VIDEO_DIR", tmp_path / "videos"):
            result = await job.run(MagicMock(), {"_site_config": _sc("postgres://cloud")})

        assert result.ok is True
        assert result.changes_made == 1
        gen_mock.assert_awaited_once()

    async def test_max_per_cycle_caps_generation(self, tmp_path):
        job = BackfillVideosJob()
        # 3 candidate posts, all with podcasts, no videos.
        rows = [_row(f"p{i}") for i in range(3)]
        fake_asyncpg, _ = _fake_asyncpg(rows=rows)

        (tmp_path / "podcasts").mkdir(parents=True)
        (tmp_path / "videos").mkdir(parents=True)
        for i in range(3):
            (tmp_path / "podcasts" / f"p{i}.mp3").write_bytes(b"fake")

        gen_result = MagicMock(success=True)
        with patch.dict("sys.modules", {"asyncpg": fake_asyncpg}), \
             patch("services.video_service.generate_video_for_post",
                   new=AsyncMock(return_value=gen_result)) as gen_mock, \
             patch("services.podcast_service.PODCAST_DIR", tmp_path / "podcasts"), \
             patch("services.video_service.VIDEO_DIR", tmp_path / "videos"):
            # Default max_per_cycle=1 → only 1 should generate.
            result = await job.run(MagicMock(), {"_site_config": _sc("postgres://cloud")})

        assert result.ok is True
        assert result.changes_made == 1
        assert gen_mock.await_count == 1

    async def test_generation_failure_does_not_block_others(self, tmp_path):
        """If generate_video_for_post raises, the job keeps scanning."""
        job = BackfillVideosJob()
        rows = [_row(f"p{i}") for i in range(2)]
        fake_asyncpg, _ = _fake_asyncpg(rows=rows)

        (tmp_path / "podcasts").mkdir(parents=True)
        (tmp_path / "videos").mkdir(parents=True)
        for i in range(2):
            (tmp_path / "podcasts" / f"p{i}.mp3").write_bytes(b"fake")

        call_count = 0

        async def _flaky(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("GPU OOM")
            return MagicMock(success=True)

        with patch.dict("sys.modules", {"asyncpg": fake_asyncpg}), \
             patch("services.video_service.generate_video_for_post", new=_flaky), \
             patch("services.podcast_service.PODCAST_DIR", tmp_path / "podcasts"), \
             patch("services.video_service.VIDEO_DIR", tmp_path / "videos"):
            # max_per_cycle=2 so both candidates are attempted.
            result = await job.run(
                MagicMock(),
                {"max_per_cycle": 2, "_site_config": _sc("postgres://cloud")},
            )

        assert result.ok is True
        # First failed, second succeeded.
        assert result.changes_made == 1
        assert call_count == 2
