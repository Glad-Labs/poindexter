"""Tests for the silent-skip surfacing in modules.content.multi_model_qa.

Background: until 2026-05-27, the deepeval/guardrails/ragas reviewer
wrappers in multi_model_qa.py silently returned None when a prerequisite
was missing (master rail flag off, judge model unresolvable, research
sources empty, competitor list empty). Combined with a missing entry
in qa_gates_db_writer._REVIEWER_TO_GATE, this meant the operator
dashboard showed last_run_at=NEVER for those rails.

These tests pin the new `_surface_reviewer_skip` helper and its wire-up
across the five rails that had silent-skip paths.

Per `feedback_no_silent_defaults` — every non-exception silent return
now emits an audit_log row with event_type='qa_reviewer_skipped',
severity='info'.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from modules.content.multi_model_qa import (
    MultiModelQA,
    _surface_reviewer_skip,
)
from services.site_config import SiteConfig


@pytest.mark.unit
class TestSurfaceReviewerSkip:
    """`_surface_reviewer_skip` is the non-exception loud-skip helper for
    structural skips."""

    def test_emits_audit_log_row_with_info_severity(self):
        with patch("services.audit_log.audit_log_bg") as audit_mock:
            _surface_reviewer_skip(
                "deepeval_faithfulness",
                "research_sources empty",
                {"extra": "value"},
            )
        audit_mock.assert_called_once()
        args, kwargs = audit_mock.call_args
        assert args[0] == "qa_reviewer_skipped"
        assert args[1] == "multi_model_qa"
        assert args[2]["reviewer"] == "deepeval_faithfulness"
        assert "research_sources empty" in args[2]["reason"]
        assert args[2]["extra"] == "value"
        assert kwargs.get("severity") == "info"

    def test_works_without_details(self):
        with patch("services.audit_log.audit_log_bg") as audit_mock:
            _surface_reviewer_skip("ragas_eval", "master rail off")
        audit_mock.assert_called_once()
        args, _ = audit_mock.call_args
        assert args[2]["reviewer"] == "ragas_eval"

    def test_audit_logger_uninitialised_swallowed(self):
        with patch(
            "services.audit_log.audit_log_bg",
            side_effect=RuntimeError("audit not initialised"),
        ):
            _surface_reviewer_skip("guardrails_brand", "master rail off")


@pytest.mark.unit
class TestReviewerSkipWiredIntoRails:
    """End-to-end: when each reviewer is skipped for a structural reason,
    the wrapper returns None AND `_surface_reviewer_skip` fires."""

    @pytest.mark.asyncio
    async def test_deepeval_brand_master_rail_off_surfaces_skip(self):
        qa = MultiModelQA(pool=None, settings_service=None, site_config=SiteConfig())
        with patch(
            "services.deepeval_rails.is_enabled", return_value=False,
        ), patch(
            "modules.content.multi_model_qa._surface_reviewer_skip",
        ) as surface_mock:
            result = qa._check_deepeval_brand("body", "topic")
        assert result is None
        surface_mock.assert_called_once()
        assert surface_mock.call_args.args[0] == "deepeval_brand_fabrication"
        assert "deepeval_enabled=false" in surface_mock.call_args.args[1]

    @pytest.mark.asyncio
    async def test_deepeval_faithfulness_empty_research_surfaces_skip(self):
        qa = MultiModelQA(pool=None, settings_service=None, site_config=SiteConfig())
        with patch(
            "services.deepeval_rails.is_enabled", return_value=True,
        ), patch(
            "modules.content.multi_model_qa._surface_reviewer_skip",
        ) as surface_mock:
            result = await qa._check_deepeval_faithfulness("body", None)
        assert result is None
        surface_mock.assert_called_once()
        assert surface_mock.call_args.args[0] == "deepeval_faithfulness"
        assert "research_sources empty" in surface_mock.call_args.args[1]

    @pytest.mark.asyncio
    async def test_guardrails_brand_master_rail_off_surfaces_skip(self):
        qa = MultiModelQA(pool=None, settings_service=None, site_config=SiteConfig())
        with patch(
            "services.guardrails_rails.is_enabled", return_value=False,
        ), patch(
            "modules.content.multi_model_qa._surface_reviewer_skip",
        ) as surface_mock:
            result = await qa._check_guardrails_brand("body")
        assert result is None
        surface_mock.assert_called_once()
        assert surface_mock.call_args.args[0] == "guardrails_brand"
        assert "guardrails_enabled=false" in surface_mock.call_args.args[1]

    @pytest.mark.asyncio
    async def test_guardrails_competitor_empty_list_surfaces_skip(self):
        qa = MultiModelQA(pool=None, settings_service=None, site_config=SiteConfig())
        with patch(
            "services.guardrails_rails.is_enabled", return_value=True,
        ), patch(
            "services.guardrails_rails._resolve_competitors", return_value=[],
        ), patch(
            "modules.content.multi_model_qa._surface_reviewer_skip",
        ) as surface_mock:
            result = await qa._check_guardrails_competitor("body")
        assert result is None
        surface_mock.assert_called_once()
        assert surface_mock.call_args.args[0] == "guardrails_competitor"
        assert "guardrails_competitor_list empty" in surface_mock.call_args.args[1]

    @pytest.mark.asyncio
    async def test_ragas_master_rail_off_surfaces_skip(self):
        qa = MultiModelQA(pool=None, settings_service=None, site_config=SiteConfig())
        with patch(
            "services.ragas_eval.is_enabled", return_value=False,
        ), patch(
            "modules.content.multi_model_qa._surface_reviewer_skip",
        ) as surface_mock:
            result = await qa._check_ragas_eval("body", "topic", "ctx")
        assert result is None
        surface_mock.assert_called_once()
        assert surface_mock.call_args.args[0] == "ragas_eval"
        assert "ragas_enabled=false" in surface_mock.call_args.args[1]

    @pytest.mark.asyncio
    async def test_ragas_empty_research_surfaces_skip(self):
        qa = MultiModelQA(pool=None, settings_service=None, site_config=SiteConfig())
        with patch(
            "services.ragas_eval.is_enabled", return_value=True,
        ), patch(
            "modules.content.multi_model_qa._surface_reviewer_skip",
        ) as surface_mock:
            result = await qa._check_ragas_eval("body", "topic", None)
        assert result is None
        surface_mock.assert_called_once()
        assert surface_mock.call_args.args[0] == "ragas_eval"
        assert "research_sources empty" in surface_mock.call_args.args[1]
