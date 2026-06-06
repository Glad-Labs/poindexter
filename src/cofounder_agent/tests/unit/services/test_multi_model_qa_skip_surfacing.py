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
    SKIP_TYPE_CONDITIONAL,
    SKIP_TYPE_MASTER_FLAG_OFF,
    SKIP_TYPE_MISCONFIG,
    MultiModelQA,
)
from plugins.fake_platform import FakePlatform
from services.site_config import SiteConfig


@pytest.mark.unit
class TestSurfaceReviewerSkip:
    """`_surface_reviewer_skip` is the non-exception loud-skip helper for
    structural skips. Wave 3c-ii (#667): it is now a MultiModelQA method that
    audits through the capability handle (``platform.audit.write_bg``)."""

    @staticmethod
    def _qa_with_fake() -> tuple[MultiModelQA, FakePlatform]:
        fake = FakePlatform()
        qa = MultiModelQA(pool=None, settings_service=None,
                          site_config=SiteConfig(), platform=fake)
        return qa, fake

    def test_emits_audit_log_row_with_info_severity(self):
        qa, fake = self._qa_with_fake()
        qa._surface_reviewer_skip(
            "deepeval_faithfulness",
            "research_sources empty",
            {"extra": "value"},
        )
        assert len(fake.audit.writes_bg) == 1
        w = fake.audit.writes_bg[0]
        assert w["event_type"] == "qa_reviewer_skipped"
        assert w["source"] == "multi_model_qa"
        assert w["details"]["reviewer"] == "deepeval_faithfulness"
        assert "research_sources empty" in w["details"]["reason"]
        assert w["details"]["extra"] == "value"
        assert w["severity"] == "info"

    def test_works_without_details(self):
        qa, fake = self._qa_with_fake()
        qa._surface_reviewer_skip("ragas_eval", "master rail off")
        assert fake.audit.writes_bg[0]["details"]["reviewer"] == "ragas_eval"

    def test_default_skip_type_is_misconfig(self):
        # #1181: an unclassified skip defaults to 'misconfig' (counts toward
        # the QaRailFullySkipped ratio) — the conservative fail-loud default.
        qa, fake = self._qa_with_fake()
        qa._surface_reviewer_skip("deepeval_g_eval", "judge unresolvable")
        assert fake.audit.writes_bg[0]["details"]["skip_type"] == SKIP_TYPE_MISCONFIG

    def test_skip_type_recorded_in_audit_details(self):
        # #1181: intentional skips are classified so the skip-ratio SQL can
        # exclude them structurally instead of substring-matching prose.
        qa, fake = self._qa_with_fake()
        qa._surface_reviewer_skip(
            "deepeval_faithfulness", "research_sources empty",
            skip_type=SKIP_TYPE_CONDITIONAL,
        )
        assert fake.audit.writes_bg[0]["details"]["skip_type"] == SKIP_TYPE_CONDITIONAL

    def test_no_platform_is_quiet_noop(self):
        # No handle (substrate preview-QA / tests): the skip surfacing drops
        # quietly, mirroring audit_log_bg's no-global-logger drop. Must not raise.
        qa = MultiModelQA(pool=None, settings_service=None,
                          site_config=SiteConfig(), platform=None)
        qa._surface_reviewer_skip("guardrails_brand", "master rail off")

    def test_audit_failure_swallowed(self):
        # Observability must never break the QA chain: if the handle's write_bg
        # raises, _surface_reviewer_skip swallows it.
        class _BoomAudit:
            def write_bg(self, *a, **k):
                raise RuntimeError("audit boom")

        class _BoomPlatform:
            audit = _BoomAudit()

        qa = MultiModelQA(pool=None, settings_service=None,
                          site_config=SiteConfig(), platform=_BoomPlatform())
        qa._surface_reviewer_skip("guardrails_brand", "master rail off")


@pytest.mark.unit
class TestReviewerSkipWiredIntoRails:
    """End-to-end: when each reviewer is skipped for a structural reason,
    the wrapper returns None AND `_surface_reviewer_skip` fires."""

    @pytest.mark.asyncio
    async def test_deepeval_brand_master_rail_off_surfaces_skip(self):
        qa = MultiModelQA(pool=None, settings_service=None, site_config=SiteConfig())
        with patch(
            "services.deepeval_rails.is_enabled", return_value=False,
        ), patch.object(
            MultiModelQA, "_surface_reviewer_skip",
        ) as surface_mock:
            result = qa._check_deepeval_brand("body", "topic")
        assert result is None
        surface_mock.assert_called_once()
        assert surface_mock.call_args.args[0] == "deepeval_brand_fabrication"
        assert "deepeval_enabled=false" in surface_mock.call_args.args[1]
        # #1181: master-flag-off is an intentional skip → excluded from ratio.
        assert surface_mock.call_args.kwargs["skip_type"] == SKIP_TYPE_MASTER_FLAG_OFF

    @pytest.mark.asyncio
    async def test_deepeval_faithfulness_empty_research_surfaces_skip(self):
        qa = MultiModelQA(pool=None, settings_service=None, site_config=SiteConfig())
        with patch(
            "services.deepeval_rails.is_enabled", return_value=True,
        ), patch.object(
            MultiModelQA, "_surface_reviewer_skip",
        ) as surface_mock:
            result = await qa._check_deepeval_faithfulness("body", None)
        assert result is None
        surface_mock.assert_called_once()
        assert surface_mock.call_args.args[0] == "deepeval_faithfulness"
        assert "research_sources empty" in surface_mock.call_args.args[1]
        # #1181: this is the false-positive the issue is about — an empty
        # research corpus is a structural conditional skip, not breakage, so
        # it must be excluded from the QaRailFullySkipped ratio.
        assert surface_mock.call_args.kwargs["skip_type"] == SKIP_TYPE_CONDITIONAL

    @pytest.mark.asyncio
    async def test_guardrails_brand_master_rail_off_surfaces_skip(self):
        qa = MultiModelQA(pool=None, settings_service=None, site_config=SiteConfig())
        with patch(
            "services.guardrails_rails.is_enabled", return_value=False,
        ), patch.object(
            MultiModelQA, "_surface_reviewer_skip",
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
        ), patch.object(
            MultiModelQA, "_surface_reviewer_skip",
        ) as surface_mock:
            result = await qa._check_guardrails_competitor("body")
        assert result is None
        surface_mock.assert_called_once()
        assert surface_mock.call_args.args[0] == "guardrails_competitor"
        assert "guardrails_competitor_list empty" in surface_mock.call_args.args[1]
        # #1181: no competitor list configured is a conditional skip.
        assert surface_mock.call_args.kwargs["skip_type"] == SKIP_TYPE_CONDITIONAL

    @pytest.mark.asyncio
    async def test_ragas_master_rail_off_surfaces_skip(self):
        qa = MultiModelQA(pool=None, settings_service=None, site_config=SiteConfig())
        with patch(
            "services.ragas_eval.is_enabled", return_value=False,
        ), patch.object(
            MultiModelQA, "_surface_reviewer_skip",
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
        ), patch.object(
            MultiModelQA, "_surface_reviewer_skip",
        ) as surface_mock:
            result = await qa._check_ragas_eval("body", "topic", None)
        assert result is None
        surface_mock.assert_called_once()
        assert surface_mock.call_args.args[0] == "ragas_eval"
        assert "research_sources empty" in surface_mock.call_args.args[1]
