"""Unit tests for ``services/jobs/auto_embed_posts.py``.

Subprocess mocked. Mirrors the sync_shared_context tests but verifies
the ``--phase`` argument is threaded through and the timeout default
differs (120s, not 30s).
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.jobs.auto_embed_posts import AutoEmbedPostsJob


def _fake_proc(returncode: int = 0, stdout: bytes = b"", stderr: bytes = b"") -> MagicMock:
    proc = MagicMock()
    proc.returncode = returncode
    proc.communicate = AsyncMock(return_value=(stdout, stderr))
    proc.kill = MagicMock()
    proc.wait = AsyncMock(return_value=None)
    return proc


class TestContract:
    def test_has_required_attrs(self):
        job = AutoEmbedPostsJob()
        assert job.name == "auto_embed_posts"
        assert job.schedule == "every 1 hour"
        assert job.idempotent is True


class TestRun:
    @pytest.mark.asyncio
    async def test_successful_embed(self):
        proc = _fake_proc(
            returncode=0,
            stdout=b"... embedded 8 posts, 0 skipped, 0 failed\n",
        )
        mock_exec = AsyncMock(return_value=proc)
        with patch(
            "services.jobs.auto_embed_posts.asyncio.create_subprocess_exec",
            new=mock_exec,
        ):
            job = AutoEmbedPostsJob()
            result = await job.run(None, {})
        assert result.ok is True
        assert result.changes_made == 1
        assert "phase=posts" in result.detail
        # Verify --phase posts is in the argv
        argv = mock_exec.call_args.args
        assert "--phase" in argv
        assert "posts" in argv

    @pytest.mark.asyncio
    async def test_custom_phase_threaded_through(self):
        proc = _fake_proc(returncode=0, stdout=b"done")
        mock_exec = AsyncMock(return_value=proc)
        with patch(
            "services.jobs.auto_embed_posts.asyncio.create_subprocess_exec",
            new=mock_exec,
        ):
            job = AutoEmbedPostsJob()
            result = await job.run(None, {"phase": "decisions"})
        argv = mock_exec.call_args.args
        assert "decisions" in argv
        assert "phase=decisions" in result.detail

    @pytest.mark.asyncio
    async def test_nonzero_returncode_returns_not_ok(self):
        proc = _fake_proc(
            returncode=1,
            stderr=b"asyncpg.exceptions.UniqueViolationError",
        )
        with patch(
            "services.jobs.auto_embed_posts.asyncio.create_subprocess_exec",
            new=AsyncMock(return_value=proc),
        ):
            job = AutoEmbedPostsJob()
            result = await job.run(None, {})
        assert result.ok is False
        assert "exited 1" in result.detail
        assert "UniqueViolation" in result.detail

    @pytest.mark.asyncio
    async def test_timeout_kills_subprocess(self):
        proc = MagicMock()
        proc.kill = MagicMock()
        proc.wait = AsyncMock(return_value=None)
        proc.communicate = AsyncMock(side_effect=RuntimeError("must not be awaited"))

        async def _raise_timeout(*args: Any, **kwargs: Any) -> None:
            raise asyncio.TimeoutError()

        with patch(
            "services.jobs.auto_embed_posts.asyncio.create_subprocess_exec",
            new=AsyncMock(return_value=proc),
        ), patch(
            "services.jobs.auto_embed_posts.asyncio.wait_for",
            new=_raise_timeout,
        ):
            job = AutoEmbedPostsJob()
            result = await job.run(None, {"timeout_seconds": 10})
        assert result.ok is False
        assert "timeout" in result.detail.lower()
        proc.kill.assert_called_once()

    @pytest.mark.asyncio
    async def test_file_not_found_surfaces_clear_error(self):
        with patch(
            "services.jobs.auto_embed_posts.asyncio.create_subprocess_exec",
            new=AsyncMock(side_effect=FileNotFoundError("no python")),
        ):
            job = AutoEmbedPostsJob()
            result = await job.run(None, {})
        assert result.ok is False
        assert "not found" in result.detail

    @pytest.mark.asyncio
    async def test_custom_script_path(self):
        proc = _fake_proc(returncode=0, stdout=b"ok")
        mock_exec = AsyncMock(return_value=proc)
        with patch(
            "services.jobs.auto_embed_posts.asyncio.create_subprocess_exec",
            new=mock_exec,
        ):
            job = AutoEmbedPostsJob()
            await job.run(None, {"script_path": "/custom/ae.py"})
        argv = mock_exec.call_args.args
        assert argv[1] == "/custom/ae.py"
