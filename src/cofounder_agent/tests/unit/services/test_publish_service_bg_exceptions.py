"""
Unit tests — publish_service background-task exception surfacing (#708).

Covers the two fixes from Glad-Labs/poindexter#708:

1. ``_spawn_background`` done-callback:
   - A task that raises logs at ERROR with exc_info and forwards to
     SentryIntegration.capture_exception.
   - A task that succeeds does NOT log an error.
   - A cancelled task does NOT log an error.
   - The strong-reference set is cleaned up regardless of outcome.

2. ``_upload_media_to_r2_bg`` per-medium error isolation:
   - A podcast upload failure is logged at ERROR; video upload still runs.
   - A video upload failure is logged at ERROR; podcast upload still runs.
   - Both failures are reported independently.
"""

import asyncio
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.publish_service import _spawn_background, _upload_media_to_r2_bg
from services.site_config import SiteConfig

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

# Zero-delay SiteConfig so _upload_media_to_r2_bg doesn't sleep 240 s.
_FAST_SC = SiteConfig(initial_config={"media_upload_delay_seconds": "0"})


async def _raise(exc: Exception):
    """Coroutine that raises *exc* immediately."""
    raise exc


async def _succeed():
    """Coroutine that returns normally."""


# ---------------------------------------------------------------------------
# _spawn_background — done-callback exception surfacing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_spawn_background_logs_task_exception(caplog):
    """A task that raises must produce an ERROR log containing the task name
    and the exception message."""
    import logging

    boom = RuntimeError("media upload exploded")
    with caplog.at_level(logging.ERROR, logger="services.publish_service"):
        task = _spawn_background(_raise(boom), name="test_boom")
        await asyncio.gather(task, return_exceptions=True)

    error_records = [r for r in caplog.records if r.levelname == "ERROR"]
    assert error_records, "Expected at least one ERROR log"
    combined = " ".join(r.message for r in error_records)
    assert "test_boom" in combined, "Task name not found in error log"
    assert "media upload exploded" in combined, "Exception message not found in error log"


@pytest.mark.asyncio
async def test_spawn_background_forwards_to_error_tracker():
    """A task exception is forwarded to SentryIntegration.capture_exception."""
    boom = ValueError("r2 unreachable")

    mock_tracker = MagicMock()
    mock_tracker.capture_exception = MagicMock()

    fake_sentry_mod = MagicMock()
    fake_sentry_mod.SentryIntegration = mock_tracker

    with patch.dict(sys.modules, {"services.sentry_integration": fake_sentry_mod}):
        task = _spawn_background(_raise(boom), name="r2_upload(post-xyz)")
        await asyncio.gather(task, return_exceptions=True)

    mock_tracker.capture_exception.assert_called_once()
    args, kwargs = mock_tracker.capture_exception.call_args
    assert args[0] is boom
    assert kwargs.get("context", {}).get("task_name") == "r2_upload(post-xyz)"


@pytest.mark.asyncio
async def test_spawn_background_no_error_log_on_success(caplog):
    """A task that succeeds must NOT produce an ERROR log."""
    import logging

    with caplog.at_level(logging.ERROR, logger="services.publish_service"):
        task = _spawn_background(_succeed(), name="clean_task")
        await asyncio.gather(task, return_exceptions=True)

    error_records = [r for r in caplog.records if r.levelname == "ERROR"]
    assert not error_records, f"Unexpected ERROR records: {error_records}"


@pytest.mark.asyncio
async def test_spawn_background_no_error_log_on_cancel(caplog):
    """A cancelled task must NOT produce an ERROR log."""
    import logging

    async def _wait_forever():
        await asyncio.sleep(9999)

    with caplog.at_level(logging.ERROR, logger="services.publish_service"):
        task = _spawn_background(_wait_forever(), name="cancelled_task")
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    error_records = [r for r in caplog.records if r.levelname == "ERROR"]
    assert not error_records, f"Unexpected ERROR records for cancelled task: {error_records}"


@pytest.mark.asyncio
async def test_spawn_background_removes_task_from_strong_ref_set():
    """The _background_tasks strong-ref set is cleaned up on done."""
    import services.publish_service as ps_mod

    initial_size = len(ps_mod._background_tasks)
    task = _spawn_background(_succeed(), name="cleanup_check")
    assert task in ps_mod._background_tasks, "Task should be in the set while pending"
    await asyncio.gather(task, return_exceptions=True)
    # Allow the done-callback to run
    await asyncio.sleep(0)
    assert task not in ps_mod._background_tasks, "Task should be removed after completion"
    assert len(ps_mod._background_tasks) == initial_size


# ---------------------------------------------------------------------------
# _upload_media_to_r2_bg — per-medium error isolation
# ---------------------------------------------------------------------------


def _make_r2_mock(*, podcast_exc=None, video_exc=None):
    """Build a minimal R2UploadService mock."""
    r2 = AsyncMock()
    if podcast_exc is not None:
        r2.upload_podcast_episode = AsyncMock(side_effect=podcast_exc)
    else:
        r2.upload_podcast_episode = AsyncMock(return_value=None)
    if video_exc is not None:
        r2.upload_video_episode = AsyncMock(side_effect=video_exc)
    else:
        r2.upload_video_episode = AsyncMock(return_value=None)
    return r2


@pytest.mark.asyncio
async def test_upload_media_bg_podcast_failure_does_not_kill_video(caplog):
    """A podcast upload error is logged at ERROR; video upload still runs."""
    import logging

    r2 = _make_r2_mock(podcast_exc=OSError("S3 connection refused"))

    fake_r2_mod = MagicMock()
    fake_r2_mod.R2UploadService = MagicMock(return_value=r2)

    with patch.dict(sys.modules, {"services.r2_upload_service": fake_r2_mod}):
        with caplog.at_level(logging.ERROR, logger="services.publish_service"):
            await _upload_media_to_r2_bg(_FAST_SC, "post-abc")

    podcast_errors = [
        r for r in caplog.records
        if r.levelname == "ERROR" and "Podcast episode upload failed" in r.message
    ]
    assert podcast_errors, "Expected ERROR log for podcast upload failure"
    assert "post-abc" in podcast_errors[0].message

    # Video upload was still attempted despite podcast failure
    r2.upload_video_episode.assert_called_once_with("post-abc")


@pytest.mark.asyncio
async def test_upload_media_bg_video_failure_does_not_kill_podcast(caplog):
    """A video upload error is logged at ERROR; podcast upload is unaffected."""
    import logging

    r2 = _make_r2_mock(video_exc=ConnectionError("bucket not found"))

    fake_r2_mod = MagicMock()
    fake_r2_mod.R2UploadService = MagicMock(return_value=r2)

    with patch.dict(sys.modules, {"services.r2_upload_service": fake_r2_mod}):
        with caplog.at_level(logging.ERROR, logger="services.publish_service"):
            await _upload_media_to_r2_bg(_FAST_SC, "post-def")

    video_errors = [
        r for r in caplog.records
        if r.levelname == "ERROR" and "Video episode upload failed" in r.message
    ]
    assert video_errors, "Expected ERROR log for video upload failure"
    assert "post-def" in video_errors[0].message

    # Podcast upload completed normally
    r2.upload_podcast_episode.assert_called_once_with("post-def")


@pytest.mark.asyncio
async def test_upload_media_bg_both_failures_independent(caplog):
    """Both podcast and video failure logs are emitted independently."""
    import logging

    r2 = _make_r2_mock(
        podcast_exc=TimeoutError("podcast timeout"),
        video_exc=TimeoutError("video timeout"),
    )

    fake_r2_mod = MagicMock()
    fake_r2_mod.R2UploadService = MagicMock(return_value=r2)

    with patch.dict(sys.modules, {"services.r2_upload_service": fake_r2_mod}):
        with caplog.at_level(logging.ERROR, logger="services.publish_service"):
            await _upload_media_to_r2_bg(_FAST_SC, "post-ghi")

    error_msgs = [r.message for r in caplog.records if r.levelname == "ERROR"]
    assert any("Podcast episode upload failed" in m for m in error_msgs), (
        f"No podcast error log. Got: {error_msgs}"
    )
    assert any("Video episode upload failed" in m for m in error_msgs), (
        f"No video error log. Got: {error_msgs}"
    )
