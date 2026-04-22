"""Unit tests for ``services/jobs/auto_embed_posts.py``.

The subprocess runner is covered comprehensively in
``test_subprocess_runner.py``. Here we only verify that this job
threads the right ``--phase`` arg and prefixes the detail string
sensibly.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.jobs._subprocess_runner import ScriptResult
from services.jobs.auto_embed_posts import AutoEmbedPostsJob


def _mock_sc() -> MagicMock:
    """SiteConfig mock for post-Phase-H job.run() kwarg."""
    sc = MagicMock()
    sc.get.side_effect = lambda k, d="": d
    sc.get_bool.side_effect = lambda k, d=False: d
    sc.get_int.side_effect = lambda k, d=0: d
    return sc


class TestContract:
    def test_has_required_attrs(self):
        job = AutoEmbedPostsJob()
        assert job.name == "auto_embed_posts"
        assert job.schedule == "every 1 hour"
        assert job.idempotent is True


class TestRun:
    @pytest.mark.asyncio
    async def test_successful_embed_detail_prefixed_with_phase(self):
        runner = AsyncMock(return_value=ScriptResult(
            ok=True, detail="embedded 8 posts, 0 skipped",
        ))
        with patch(
            "services.jobs.auto_embed_posts.run_python_script", new=runner,
        ):
            job = AutoEmbedPostsJob()
            result = await job.run(None, {}, site_config=_mock_sc())
        assert result.ok is True
        assert result.changes_made == 1
        assert "phase=posts" in result.detail
        assert "embedded 8" in result.detail

    @pytest.mark.asyncio
    async def test_default_phase_is_posts(self):
        runner = AsyncMock(return_value=ScriptResult(ok=True, detail="ok"))
        with patch(
            "services.jobs.auto_embed_posts.run_python_script", new=runner,
        ):
            job = AutoEmbedPostsJob()
            await job.run(None, {}, site_config=_mock_sc())
        argv = runner.call_args.args
        # argv = (script_path, "--phase", "posts") positionally
        assert argv[1] == "--phase"
        assert argv[2] == "posts"

    @pytest.mark.asyncio
    async def test_custom_phase_threaded_through(self):
        runner = AsyncMock(return_value=ScriptResult(ok=True, detail="ok"))
        with patch(
            "services.jobs.auto_embed_posts.run_python_script", new=runner,
        ):
            job = AutoEmbedPostsJob()
            result = await job.run(None, {"phase": "decisions"}, site_config=_mock_sc())
        argv = runner.call_args.args
        assert argv[2] == "decisions"
        assert "phase=decisions" in result.detail

    @pytest.mark.asyncio
    async def test_failed_run_changes_made_zero(self):
        runner = AsyncMock(return_value=ScriptResult(
            ok=False, detail="script exited 1: UniqueViolation",
        ))
        with patch(
            "services.jobs.auto_embed_posts.run_python_script", new=runner,
        ):
            job = AutoEmbedPostsJob()
            result = await job.run(None, {}, site_config=_mock_sc())
        assert result.ok is False
        assert result.changes_made == 0
        assert "exited 1" in result.detail

    @pytest.mark.asyncio
    async def test_custom_script_path_override(self):
        runner = AsyncMock(return_value=ScriptResult(ok=True, detail="ok"))
        with patch(
            "services.jobs.auto_embed_posts.run_python_script", new=runner,
        ):
            job = AutoEmbedPostsJob()
            await job.run(None, {"script_path": "/custom/ae.py"}, site_config=_mock_sc())
        argv = runner.call_args.args
        assert argv[0] == "/custom/ae.py"

    @pytest.mark.asyncio
    async def test_default_timeout_is_120s(self):
        runner = AsyncMock(return_value=ScriptResult(ok=True, detail="ok"))
        with patch(
            "services.jobs.auto_embed_posts.run_python_script", new=runner,
        ):
            job = AutoEmbedPostsJob()
            await job.run(None, {}, site_config=_mock_sc())
        assert runner.call_args.kwargs["timeout_s"] == 120
