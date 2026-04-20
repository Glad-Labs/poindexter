"""Unit tests for ``services/jobs/sync_shared_context.py``.

Subprocess is mocked. Focus: script-resolution, returncode propagation,
timeout handling, spawn-failure, stderr capture on non-zero exits.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.jobs.sync_shared_context import SyncSharedContextJob


def _fake_proc(returncode: int = 0, stdout: bytes = b"", stderr: bytes = b"") -> MagicMock:
    proc = MagicMock()
    proc.returncode = returncode
    proc.communicate = AsyncMock(return_value=(stdout, stderr))
    proc.kill = MagicMock()
    proc.wait = AsyncMock(return_value=None)
    return proc


class TestContract:
    def test_has_required_attrs(self):
        job = SyncSharedContextJob()
        assert job.name == "sync_shared_context"
        assert job.schedule == "every 30 minutes"
        assert job.idempotent is True


class TestRun:
    @pytest.mark.asyncio
    async def test_successful_sync(self):
        proc = _fake_proc(returncode=0, stdout=b"Synced 5 files to shared-context")
        with patch(
            "services.jobs.sync_shared_context.asyncio.create_subprocess_exec",
            new=AsyncMock(return_value=proc),
        ):
            job = SyncSharedContextJob()
            result = await job.run(None, {})
        assert result.ok is True
        assert result.changes_made == 1
        assert "Synced 5 files" in result.detail

    @pytest.mark.asyncio
    async def test_empty_stdout_still_ok(self):
        proc = _fake_proc(returncode=0, stdout=b"")
        with patch(
            "services.jobs.sync_shared_context.asyncio.create_subprocess_exec",
            new=AsyncMock(return_value=proc),
        ):
            job = SyncSharedContextJob()
            result = await job.run(None, {})
        assert result.ok is True
        assert result.detail == "synced"

    @pytest.mark.asyncio
    async def test_nonzero_returncode_returns_not_ok(self):
        proc = _fake_proc(
            returncode=2,
            stderr=b"ImportError: No module named 'foo'",
        )
        with patch(
            "services.jobs.sync_shared_context.asyncio.create_subprocess_exec",
            new=AsyncMock(return_value=proc),
        ):
            job = SyncSharedContextJob()
            result = await job.run(None, {})
        assert result.ok is False
        assert "exited 2" in result.detail
        assert "ImportError" in result.detail  # stderr surfaced in detail

    @pytest.mark.asyncio
    async def test_timeout_kills_subprocess(self):
        proc = MagicMock()
        proc.kill = MagicMock()
        proc.wait = AsyncMock(return_value=None)
        proc.communicate = AsyncMock(side_effect=RuntimeError("should not be awaited"))

        async def _raise_timeout(*args: Any, **kwargs: Any) -> None:
            raise asyncio.TimeoutError("simulated")

        with patch(
            "services.jobs.sync_shared_context.asyncio.create_subprocess_exec",
            new=AsyncMock(return_value=proc),
        ), patch(
            "services.jobs.sync_shared_context.asyncio.wait_for",
            new=_raise_timeout,
        ):
            job = SyncSharedContextJob()
            result = await job.run(None, {"timeout_seconds": 5})
        assert result.ok is False
        assert "timeout" in result.detail.lower()
        proc.kill.assert_called_once()

    @pytest.mark.asyncio
    async def test_file_not_found_returns_not_ok(self):
        """Raising FileNotFoundError (python/script missing) should surface
        a clear ``ok=False``, not crash."""
        with patch(
            "services.jobs.sync_shared_context.asyncio.create_subprocess_exec",
            new=AsyncMock(side_effect=FileNotFoundError("python not on PATH")),
        ):
            job = SyncSharedContextJob()
            result = await job.run(None, {})
        assert result.ok is False
        assert "not found" in result.detail

    @pytest.mark.asyncio
    async def test_spawn_failure_other_exception(self):
        """Any other spawn failure should also surface as ok=False."""
        with patch(
            "services.jobs.sync_shared_context.asyncio.create_subprocess_exec",
            new=AsyncMock(side_effect=OSError("fork failed")),
        ):
            job = SyncSharedContextJob()
            result = await job.run(None, {})
        assert result.ok is False
        assert "spawn failed" in result.detail

    @pytest.mark.asyncio
    async def test_config_script_path_overrides_default(self):
        proc = _fake_proc(returncode=0, stdout=b"custom script ok")
        mock_exec = AsyncMock(return_value=proc)
        with patch(
            "services.jobs.sync_shared_context.asyncio.create_subprocess_exec",
            new=mock_exec,
        ):
            job = SyncSharedContextJob()
            await job.run(None, {"script_path": "/custom/path/sync.py"})
        # Check the script path arg
        args = mock_exec.call_args.args
        assert args[1] == "/custom/path/sync.py"
