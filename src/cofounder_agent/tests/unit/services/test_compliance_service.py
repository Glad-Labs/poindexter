"""
Unit tests for services/compliance_service.py.

Tests cover:
- ComplianceService initialization
- check_legal_compliance — agent available (mocked), ImportError path
- assess_privacy_compliance — no personal data, EU personal data, CA personal data
- risk_assessment — default categories, single category, unknown category
- get_service_metadata — structure validation

No real agents, no LLM calls, no DB.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from services.compliance_service import ComplianceService


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _service(**kwargs) -> ComplianceService:
    return ComplianceService(**kwargs)


# ---------------------------------------------------------------------------
# Initialization
# ---------------------------------------------------------------------------


class TestComplianceServiceInit:
    def test_default_init(self):
        svc = ComplianceService()
        assert svc.database_service is None
        assert svc.model_router is None

    def test_init_with_deps(self):
        db = MagicMock()
        router = MagicMock()
        svc = ComplianceService(database_service=db, model_router=router)
        assert svc.database_service is db
        assert svc.model_router is router


# ---------------------------------------------------------------------------
# check_legal_compliance
# ---------------------------------------------------------------------------


class TestCheckLegalCompliance:
    @pytest.mark.asyncio
    async def test_success_via_agent(self):
        mock_agent = MagicMock()
        mock_agent.run = AsyncMock(return_value={"status": "compliant"})
        mock_agent_class = MagicMock(return_value=mock_agent)

        with patch.dict(
            "sys.modules",
            {
                "agents.compliance_agent.agents.compliance_agent": MagicMock(
                    ComplianceAgent=mock_agent_class
                )
            },
        ):
            svc = _service()
            result = await svc.check_legal_compliance("some content", content_type="blog")

        assert result["phase"] == "compliance_check"
        assert result["content_type"] == "blog"
        assert result["source"] == "compliance_agent"
        assert "result" in result
        assert "timestamp" in result

    @pytest.mark.asyncio
    async def test_import_error_returns_warning(self):
        """When the compliance_agent package is missing, returns error dict without raising."""
        svc = _service()

        with patch(
            "builtins.__import__",
            side_effect=ImportError("no module"),
        ):
            # The service catches the exception internally
            result = await svc.check_legal_compliance("text", jurisdictions=["EU"])

        # Should contain error key, not raise
        assert "error" in result
        assert result.get("severity") == "warning"

    @pytest.mark.asyncio
    async def test_custom_jurisdictions_passed_through(self):
        mock_agent = MagicMock()
        mock_agent.run = AsyncMock(return_value={})
        mock_agent_class = MagicMock(return_value=mock_agent)

        with patch.dict(
            "sys.modules",
            {
                "agents.compliance_agent.agents.compliance_agent": MagicMock(
                    ComplianceAgent=mock_agent_class
                )
            },
        ):
            svc = _service()
            result = await svc.check_legal_compliance(
                "content", jurisdictions=["EU", "US"]
            )

        assert result["jurisdictions"] == ["EU", "US"]


# ---------------------------------------------------------------------------
# assess_privacy_compliance
# ---------------------------------------------------------------------------


class TestAssessPrivacyCompliance:
    @pytest.mark.asyncio
    async def test_no_personal_data_fully_compliant(self):
        svc = _service()
        result = await svc.assess_privacy_compliance(
            "public blog post",
            contains_personal_data=False,
            target_regions=["US"],
        )
        assert result["compliant"] is True
        assert result["issues"] == []
        assert result["compliance_score"] == 100

    @pytest.mark.asyncio
    async def test_eu_personal_data_adds_gdpr_issue(self):
        svc = _service()
        result = await svc.assess_privacy_compliance(
            "data with PII",
            contains_personal_data=True,
            target_regions=["EU"],
        )
        assert result["compliant"] is False
        assert any("GDPR" in issue for issue in result["issues"])
        assert result["compliance_score"] < 100

    @pytest.mark.asyncio
    async def test_ca_personal_data_adds_ccpa_warning(self):
        svc = _service()
        result = await svc.assess_privacy_compliance(
            "data",
            contains_personal_data=True,
            target_regions=["CA"],
        )
        assert any("CCPA" in w for w in result["warnings"])

    @pytest.mark.asyncio
    async def test_default_region_is_us(self):
        svc = _service()
        result = await svc.assess_privacy_compliance("text")
        assert "US" in result["target_regions"]

    @pytest.mark.asyncio
    async def test_result_structure(self):
        svc = _service()
        result = await svc.assess_privacy_compliance("text", contains_personal_data=False)
        for key in ("assessment_type", "compliance_score", "target_regions",
                    "contains_personal_data", "issues", "warnings", "compliant"):
            assert key in result, f"Missing key: {key}"


# ---------------------------------------------------------------------------
# risk_assessment
# ---------------------------------------------------------------------------


class TestRiskAssessment:
    @pytest.mark.asyncio
    async def test_default_categories_present(self):
        svc = _service()
        result = await svc.risk_assessment("content")
        assert result["assessment_type"] == "risk_assessment"
        assert "overall_risk_score" in result
        assert result["risk_level"] in ("low", "medium", "high")
        assert "risk_scores" in result

    @pytest.mark.asyncio
    async def test_single_known_category(self):
        svc = _service()
        result = await svc.risk_assessment("content", risk_categories=["legal"])
        assert "legal" in result["risk_scores"]
        # legal risk score is 0.2 → "low"
        assert result["risk_level"] == "low"

    @pytest.mark.asyncio
    async def test_unknown_category_defaults_to_zero(self):
        svc = _service()
        result = await svc.risk_assessment("content", risk_categories=["unknown_category"])
        assert result["risk_scores"]["unknown_category"] == 0
        assert result["overall_risk_score"] == 0.0

    @pytest.mark.asyncio
    async def test_timestamp_included(self):
        svc = _service()
        result = await svc.risk_assessment("content")
        assert "timestamp" in result


# ---------------------------------------------------------------------------
# get_service_metadata
# ---------------------------------------------------------------------------


class TestGetServiceMetadata:
    def test_metadata_structure(self):
        svc = _service()
        meta = svc.get_service_metadata()
        assert meta["name"] == "compliance_service"
        assert "capabilities" in meta
        assert isinstance(meta["capabilities"], list)
        assert len(meta["capabilities"]) > 0
        assert "supported_frameworks" in meta
        assert "GDPR" in meta["supported_frameworks"]
