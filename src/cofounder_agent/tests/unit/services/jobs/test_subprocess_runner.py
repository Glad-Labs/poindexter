"""Unit tests for ``services/jobs/_subprocess_runner.py``.

Covers every branch of ``run_python_script`` so the two (and future)
jobs that wrap it can stay simple.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.jobs._subprocess_runner import (
    resolve_scripts_dir,
    run_python_script,
)


def _fake_proc(returncode: int = 0, stdout: bytes = b"", stderr: bytes = b"") -> MagicMock:
    proc = MagicMock()
    proc.returncode = returncode
    proc.communicate = AsyncMock(return_value=(stdout, stderr))
    proc.kill = MagicMock()
    proc.wait = AsyncMock(return_value=None)
    return proc


class TestResolveScriptsDir:
    def test_container_path_preferred(self):
        """When /opt/scripts exists, it wins."""
        with patch(
            "services.jobs._subprocess_runner.os.path.isdir",
            return_value=True,
        ):
            assert resolve_scripts_dir() == "/opt/scripts"

    def test_host_path_fallback(self):
        with patch(
            "services.jobs._subprocess_runner.os.path.isdir",
            return_value=False,
        ):
            assert resolve_scripts_dir() == "scripts"


class TestRunPythonScript:
    @pytest.mark.asyncio
    async def test_success_returns_stdout_tail(self):
        proc = _fake_proc(returncode=0, stdout=b"final summary line\n")
        with patch(
            "services.jobs._subprocess_runner.asyncio.create_subprocess_exec",
            new=AsyncMock(return_value=proc),
        ):
            result = await run_python_script("/x/y.py")
        assert result.ok is True
        assert result.returncode == 0
        assert "final summary" in result.detail
        assert result.stdout == "final summary line"

    @pytest.mark.asyncio
    async def test_empty_stdout_detail_is_done(self):
        proc = _fake_proc(returncode=0, stdout=b"")
        with patch(
            "services.jobs._subprocess_runner.asyncio.create_subprocess_exec",
            new=AsyncMock(return_value=proc),
        ):
            result = await run_python_script("/x/y.py")
        assert result.ok is True
        assert result.detail == "done"

    @pytest.mark.asyncio
    async def test_extra_args_threaded_through(self):
        proc = _fake_proc(returncode=0, stdout=b"ok")
        mock_exec = AsyncMock(return_value=proc)
        with patch(
            "services.jobs._subprocess_runner.asyncio.create_subprocess_exec",
            new=mock_exec,
        ):
            await run_python_script("/x/y.py", "--phase", "posts")
        argv = mock_exec.call_args.args
        assert argv == ("python", "/x/y.py", "--phase", "posts")

    @pytest.mark.asyncio
    async def test_cwd_passed_through(self):
        proc = _fake_proc(returncode=0, stdout=b"ok")
        mock_exec = AsyncMock(return_value=proc)
        with patch(
            "services.jobs._subprocess_runner.asyncio.create_subprocess_exec",
            new=mock_exec,
        ):
            await run_python_script("/x/y.py", cwd="/custom/cwd")
        assert mock_exec.call_args.kwargs["cwd"] == "/custom/cwd"

    @pytest.mark.asyncio
    async def test_nonzero_returncode_returns_not_ok(self):
        proc = _fake_proc(returncode=2, stderr=b"ImportError: boom")
        with patch(
            "services.jobs._subprocess_runner.asyncio.create_subprocess_exec",
            new=AsyncMock(return_value=proc),
        ):
            result = await run_python_script("/x/y.py")
        assert result.ok is False
        assert result.returncode == 2
        assert "exited 2" in result.detail
        assert "ImportError" in result.detail
        assert "ImportError" in result.stderr

    @pytest.mark.asyncio
    async def test_timeout_kills_process(self):
        proc = MagicMock()
        proc.kill = MagicMock()
        proc.wait = AsyncMock(return_value=None)
        proc.communicate = AsyncMock(side_effect=RuntimeError("should not run"))

        async def _raise_timeout(*args: Any, **kwargs: Any) -> None:
            raise asyncio.TimeoutError()

        with patch(
            "services.jobs._subprocess_runner.asyncio.create_subprocess_exec",
            new=AsyncMock(return_value=proc),
        ), patch(
            "services.jobs._subprocess_runner.asyncio.wait_for",
            new=_raise_timeout,
        ):
            result = await run_python_script("/x/y.py", timeout_s=5)
        assert result.ok is False
        assert "5s timeout" in result.detail
        proc.kill.assert_called_once()

    @pytest.mark.asyncio
    async def test_file_not_found_surfaces_clear_error(self):
        with patch(
            "services.jobs._subprocess_runner.asyncio.create_subprocess_exec",
            new=AsyncMock(side_effect=FileNotFoundError("python gone")),
        ):
            result = await run_python_script("/x/y.py")
        assert result.ok is False
        assert "not found" in result.detail

    @pytest.mark.asyncio
    async def test_oserror_spawn_fails_cleanly(self):
        with patch(
            "services.jobs._subprocess_runner.asyncio.create_subprocess_exec",
            new=AsyncMock(side_effect=OSError("fork")),
        ):
            result = await run_python_script("/x/y.py")
        assert result.ok is False
        assert "spawn failed" in result.detail

    @pytest.mark.asyncio
    async def test_tail_chars_controls_detail_length(self):
        """Long stdout should be truncated to ``tail_chars`` in ``detail``."""
        long_stdout = b"X" * 500
        proc = _fake_proc(returncode=0, stdout=long_stdout)
        with patch(
            "services.jobs._subprocess_runner.asyncio.create_subprocess_exec",
            new=AsyncMock(return_value=proc),
        ):
            result = await run_python_script("/x/y.py", tail_chars=50)
        assert len(result.detail) == 50
        # Full stdout still preserved separately.
        assert len(result.stdout) == 500
