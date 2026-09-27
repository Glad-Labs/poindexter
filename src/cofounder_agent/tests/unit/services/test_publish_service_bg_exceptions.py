"""
Unit tests — publish_service background-task exception surfacing (#708).

Covers ``_spawn_background``'s done-callback:

- A task that raises logs at ERROR with exc_info and forwards to
  SentryIntegration.capture_exception.
- A task that succeeds does NOT log an error.
- A cancelled task does NOT log an error.
- The strong-reference set is cleaned up regardless of outcome.

This file used to also cover ``_upload_media_to_r2_bg``'s per-medium error
isolation. That function (the "11e" fire-and-forget tail) was retired
2026-09-25 as dead code: unreachable from the default approve->stage_only->
promote flow, and its own drain timeout cancelled it mid-sleep on the rare
path that did reach it (see ``docs/architecture/services/publish_service.md``
and the migration
``20260925_222741_drop_the_media_upload_delay_seconds_setting_orphaned_by_the_11e_tail_retirement``).
"""

import asyncio
import sys
from unittest.mock import MagicMock, patch

import pytest

from poindexter.services.publish_service import _spawn_background

# ---------------------------------------------------------------------------
# _spawn_background — done-callback exception surfacing
# ---------------------------------------------------------------------------


async def _raise(exc: Exception):
    """Coroutine that raises *exc* immediately."""
    raise exc


async def _succeed():
    """Coroutine that returns normally."""


@pytest.mark.asyncio
async def test_spawn_background_logs_task_exception(caplog):
    """A task that raises must produce an ERROR log containing the task name
    and the exception message."""
    import logging

    boom = RuntimeError("media upload exploded")
    with caplog.at_level(logging.ERROR, logger="poindexter.services.publish_service"):
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

    with patch.dict(sys.modules, {"poindexter.services.sentry_integration": fake_sentry_mod}):
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

    with caplog.at_level(logging.ERROR, logger="poindexter.services.publish_service"):
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

    with caplog.at_level(logging.ERROR, logger="poindexter.services.publish_service"):
        task = _spawn_background(_wait_forever(), name="cancelled_task")
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    error_records = [r for r in caplog.records if r.levelname == "ERROR"]
    assert not error_records, f"Unexpected ERROR records for cancelled task: {error_records}"


@pytest.mark.asyncio
async def test_spawn_background_removes_task_from_strong_ref_set():
    """The _background_tasks strong-ref set is cleaned up on done."""
    import poindexter.services.publish_service as ps_mod

    initial_size = len(ps_mod._background_tasks)
    task = _spawn_background(_succeed(), name="cleanup_check")
    assert task in ps_mod._background_tasks, "Task should be in the set while pending"
    await asyncio.gather(task, return_exceptions=True)
    # Allow the done-callback to run
    await asyncio.sleep(0)
    assert task not in ps_mod._background_tasks, "Task should be removed after completion"
    assert len(ps_mod._background_tasks) == initial_size
