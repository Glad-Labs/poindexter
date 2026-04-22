"""Unit tests for ``services/stages/writer_self_review.py``."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from plugins.stage import Stage
from services.stages.writer_self_review import WriterSelfReviewStage

# Phase H step 5 (GH#95): stages read site_config from the context dict.
_FAKE_SITE_CONFIG = SimpleNamespace(
    get=lambda _k, _d=None: "true",
    get_int=lambda _k, _d=0: _d,
    get_float=lambda _k, _d=0.0: _d,
    get_bool=lambda _k, _d=False: _d,
)


class TestProtocol:
    def test_conforms(self):
        assert isinstance(WriterSelfReviewStage(), Stage)

    def test_metadata(self):
        s = WriterSelfReviewStage()
        assert s.name == "writer_self_review"
        assert s.halts_on_failure is False  # Legacy behavior: non-fatal.


@pytest.mark.asyncio
class TestExecute:
    async def test_revised_updates_content_and_length(self):
        ctx: dict[str, Any] = {
            "task_id": "t1",
            "topic": "Topic",
            "title": "Title",
            "content": "Old content.",
            "site_config": _FAKE_SITE_CONFIG,
        }
        stats = {"revised": True, "contradictions_found": 2, "skipped": False}
        with patch(
            "services.self_review.self_review_and_revise",
            AsyncMock(return_value=("Revised content.", stats)),
        ), patch("services.audit_log.audit_log_bg", MagicMock()):
            result = await WriterSelfReviewStage().execute(ctx, {})
        assert result.ok is True
        assert result.context_updates["content"] == "Revised content."
        assert result.context_updates["content_length"] == len("Revised content.")
        assert result.metrics["revised"] is True
        assert result.metrics["contradictions_found"] == 2

    async def test_no_revision_leaves_content_alone(self):
        ctx: dict[str, Any] = {
            "task_id": "t1",
            "topic": "T",
            "title": "Ti",
            "content": "Keep me.",
            "site_config": _FAKE_SITE_CONFIG,
        }
        stats = {"revised": False, "contradictions_found": 0, "skipped": False}
        with patch(
            "services.self_review.self_review_and_revise",
            AsyncMock(return_value=("ignored", stats)),
        ), patch("services.audit_log.audit_log_bg", MagicMock()):
            result = await WriterSelfReviewStage().execute(ctx, {})
        assert "content" not in result.context_updates
        assert result.detail == "no changes"

    async def test_skips_when_empty_content(self):
        ctx: dict[str, Any] = {"task_id": "t1", "topic": "T", "content": ""}
        result = await WriterSelfReviewStage().execute(ctx, {})
        assert result.ok is True
        assert result.metrics["skipped"] is True

    async def test_exception_is_non_fatal(self):
        ctx: dict[str, Any] = {
            "task_id": "t1",
            "topic": "T",
            "title": "Ti",
            "content": "something",
            "site_config": _FAKE_SITE_CONFIG,
        }
        with patch(
            "services.self_review.self_review_and_revise",
            AsyncMock(side_effect=RuntimeError("boom")),
        ):
            result = await WriterSelfReviewStage().execute(ctx, {})
        # ok=False so the runner records it, but halts_on_failure=False
        # on the stage means the pipeline continues.
        assert result.ok is False
        assert "RuntimeError" in result.detail
