"""Unit tests for ``services/jobs/db_backup.py``.

Subprocess is mocked; we don't actually run pg_dump. Focus: script
resolution, timeout handling, returncode propagation into JobResult.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.jobs.db_backup import DbBackupJob


def _fake_proc(returncode: int = 0, stdout: bytes = b"", stderr: bytes = b"") -> MagicMock:
    proc = MagicMock()
    proc.returncode = returncode
    proc.communicate = AsyncMock(return_value=(stdout, stderr))
    proc.kill = MagicMock()
    proc.wait = AsyncMock(return_value=None)
    return proc


class TestContract:
    def test_has_required_attrs(self):
        job = DbBackupJob()
        assert job.name == "db_backup"
        assert job.schedule == "every 12 hours"
        assert job.idempotent is True


class TestScriptResolution:
    @pytest.mark.asyncio
    async def test_missing_script_returns_not_ok(self):
        job = DbBackupJob()
        result = await job.run(
            pool=None,
            config={"script_path": "/definitely/does/not/exist.sh"},
        )
        assert result.ok is False
        assert "not found" in result.detail


class TestRun:
    @pytest.mark.asyncio
    async def test_successful_backup(self, tmp_path):
        script = tmp_path / "backup.sh"
        script.write_text("#!/bin/bash\necho ok\n")
        proc = _fake_proc(returncode=0, stdout=b"backup ok")

        with patch(
            "services.jobs.db_backup.asyncio.create_subprocess_exec",
            new=AsyncMock(return_value=proc),
        ):
            job = DbBackupJob()
            result = await job.run(
                pool=None,
                config={"script_path": str(script), "timeout_seconds": 30},
            )

        assert result.ok is True
        assert result.changes_made == 1
        assert "completed" in result.detail.lower()

    @pytest.mark.asyncio
    async def test_nonzero_returncode_returns_not_ok(self, tmp_path):
        script = tmp_path / "backup.sh"
        script.write_text("#!/bin/bash\nexit 1\n")
        proc = _fake_proc(returncode=1, stderr=b"pg_dump: connection failed")

        with patch(
            "services.jobs.db_backup.asyncio.create_subprocess_exec",
            new=AsyncMock(return_value=proc),
        ):
            job = DbBackupJob()
            result = await job.run(
                pool=None,
                config={"script_path": str(script)},
            )

        assert result.ok is False
        assert "exited 1" in result.detail

    @pytest.mark.asyncio
    async def test_timeout_kills_subprocess(self, tmp_path):
        script = tmp_path / "backup.sh"
        script.write_text("#!/bin/bash\nsleep 999\n")

        # wait_for raises TimeoutError; the job should kill() + return ok=False.
        proc = MagicMock()
        proc.kill = MagicMock()
        proc.wait = AsyncMock(return_value=None)
        proc.communicate = AsyncMock(side_effect=RuntimeError("should not be awaited"))

        async def _raise_timeout(coro, timeout):
            raise TimeoutError("simulated")

        with patch(
            "services.jobs.db_backup.asyncio.create_subprocess_exec",
            new=AsyncMock(return_value=proc),
        ), patch("services.jobs.db_backup.asyncio.wait_for", new=_raise_timeout):
            job = DbBackupJob()
            result = await job.run(
                pool=None,
                config={"script_path": str(script), "timeout_seconds": 1},
            )

        assert result.ok is False
        assert "timeout" in result.detail.lower()
        proc.kill.assert_called_once()

    @pytest.mark.asyncio
    async def test_spawn_failure_returns_not_ok(self, tmp_path):
        script = tmp_path / "backup.sh"
        script.write_text("#!/bin/bash\necho ok\n")

        with patch(
            "services.jobs.db_backup.asyncio.create_subprocess_exec",
            new=AsyncMock(side_effect=OSError("fork failed")),
        ):
            job = DbBackupJob()
            result = await job.run(
                pool=None,
                config={"script_path": str(script)},
            )

        assert result.ok is False
        assert "spawn failed" in result.detail
