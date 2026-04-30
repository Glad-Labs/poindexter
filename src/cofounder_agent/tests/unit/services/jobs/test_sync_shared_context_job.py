"""Unit tests for ``services/jobs/sync_shared_context.py``.

The heavy lifting of spawn / timeout / stderr handling lives in
``_subprocess_runner.run_python_script`` and is covered there.
Here we only verify delegation: default script path, config
overrides, and detail-string shape.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from services.jobs._subprocess_runner import ScriptResult
from services.jobs.sync_shared_context import SyncSharedContextJob


class TestContract:
    def test_has_required_attrs(self):
        job = SyncSharedContextJob()
        assert job.name == "sync_shared_context"
        assert job.schedule == "every 30 minutes"
        assert job.idempotent is True


class TestRun:
    @pytest.mark.asyncio
    async def test_successful_run_detail_prefixed(self):
        """detail should prefix with 'synced:' on success."""
        runner = AsyncMock(return_value=ScriptResult(
            ok=True, detail="Synced 5 files", returncode=0,
        ))
        with patch(
            "services.jobs.sync_shared_context.run_python_script", new=runner,
        ):
            job = SyncSharedContextJob()
            result = await job.run(None, {})
        assert result.ok is True
        assert result.changes_made == 1
        assert result.detail.startswith("synced:")
        assert "5 files" in result.detail

    @pytest.mark.asyncio
    async def test_failed_run_no_prefix(self):
        """On failure, surface the runner's detail unchanged (it already
        carries ``script exited N: …``)."""
        runner = AsyncMock(return_value=ScriptResult(
            ok=False, detail="script exited 2: ImportError",
        ))
        with patch(
            "services.jobs.sync_shared_context.run_python_script", new=runner,
        ):
            job = SyncSharedContextJob()
            result = await job.run(None, {})
        assert result.ok is False
        assert result.changes_made == 0
        assert "synced" not in result.detail
        assert "exited 2" in result.detail

    @pytest.mark.asyncio
    async def test_default_script_path_resolved(self):
        runner = AsyncMock(return_value=ScriptResult(ok=True, detail="ok"))
        with patch(
            "services.jobs.sync_shared_context.run_python_script", new=runner,
        ):
            job = SyncSharedContextJob()
            await job.run(None, {})
        argv = runner.call_args.args
        assert argv[0].endswith("/sync-shared-context.py")

    @pytest.mark.asyncio
    async def test_config_script_path_override(self):
        runner = AsyncMock(return_value=ScriptResult(ok=True, detail="ok"))
        with patch(
            "services.jobs.sync_shared_context.run_python_script", new=runner,
        ):
            job = SyncSharedContextJob()
            await job.run(None, {"script_path": "/custom.py"})
        argv = runner.call_args.args
        assert argv[0] == "/custom.py"

    @pytest.mark.asyncio
    async def test_config_timeout_threaded_through(self):
        runner = AsyncMock(return_value=ScriptResult(ok=True, detail="ok"))
        with patch(
            "services.jobs.sync_shared_context.run_python_script", new=runner,
        ):
            job = SyncSharedContextJob()
            await job.run(None, {"timeout_seconds": 60})
        assert runner.call_args.kwargs["timeout_s"] == 60
