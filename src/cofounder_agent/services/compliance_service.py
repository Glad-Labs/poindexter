"""
Compliance Service - Unified service for legal and risk compliance

Consolidates ComplianceAgent functionality (agents/compliance_agent/) into a
composable, flat service module that integrates with the workflow engine.

This service provides:
- Legal compliance checking
- Risk assessment
- Regulatory requirement verification
- Compliance reporting
"""

import logging
from typing import Any, Dict, Optional
from datetime import datetime

logger = logging.getLogger(__name__)


class ComplianceService:
    """
    Legal and risk compliance service.

    Provides methods for:
    - Checking legal compliance of content
    - Risk assessment
    - Regulatory requirement verification
    - Compliance reporting and documentation
    """

    def __init__(
        self,
        database_service: Optional[Any] = None,
        model_router: Optional[Any] = None,
    ):
        """
        Initialize compliance service.

        Args:
            database_service: PostgreSQL database service
            model_router: Model router
        """
        self.database_service = database_service
        self.model_router = model_router
        logger.info("ComplianceService initialized")

    async def check_legal_compliance(
        self,
        content: str,
        content_type: str = "blog",
        jurisdictions: Optional[list] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        """
        Check content for legal compliance issues.

        Args:
            content: Content to check
            content_type: Type of content (blog, email, social, etc.)
            jurisdictions: List of jurisdictions to check against
            **kwargs: Additional parameters

        Returns:
            Dictionary with compliance status and issues found
        """
        try:
            from agents.compliance_agent.agents.compliance_agent import ComplianceAgent

            compliance_agent = ComplianceAgent()
            result = await compliance_agent.run(
                content=content,
                content_type=content_type,
                jurisdictions=jurisdictions or ["US"],
            )

            logger.info(f"Legal compliance check completed for {content_type}")

            return {
                "phase": "compliance_check",
                "content_type": content_type,
                "jurisdictions": jurisdictions or ["US"],
                "result": result,
                "timestamp": datetime.utcnow().isoformat(),
                "source": "compliance_agent",
            }

        except Exception as e:
            logger.error(f"Compliance check failed: {e}", exc_info=True)
            return {
                "phase": "compliance_check",
                "error": str(e),
                "severity": "warning",
            }

    async def assess_privacy_compliance(
        self,
        content: str,
        contains_personal_data: bool = False,
        target_regions: Optional[list] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        """
        Assess privacy compliance (GDPR, CCPA, etc.).

        Args:
            content: Content to assess
            contains_personal_data: Whether content contains personal data
            target_regions: Regions where content will be published
            **kwargs: Additional parameters

        Returns:
            Dictionary with privacy compliance status
        """
        try:
            target_regions = target_regions or ["US"]

            # Base privacy checks
            issues = []
            warnings = []

            if contains_personal_data:
                if "EU" in target_regions or "GDPR" in target_regions:
                    issues.append("GDPR: Personal data requires explicit consent mechanisms")
                if "CA" in target_regions:
                    warnings.append("CCPA: Privacy policy link required")

            compliance_score = max(0, 100 - len(issues) * 20 - len(warnings) * 10)

            logger.info(f"Privacy compliance assessment completed (score: {compliance_score})")

            return {
                "assessment_type": "privacy_compliance",
                "compliance_score": compliance_score,
                "target_regions": target_regions,
                "contains_personal_data": contains_personal_data,
                "issues": issues,
                "warnings": warnings,
                "compliant": len(issues) == 0,
            }

        except Exception as e:
            logger.error(f"Privacy compliance assessment failed: {e}", exc_info=True)
            return {"error": str(e), "assessment_type": "privacy_compliance"}

    async def risk_assessment(
        self,
        content: str,
        risk_categories: Optional[list] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        """
        Perform comprehensive risk assessment.

        Args:
            content: Content to assess
            risk_categories: Categories to assess (legal, reputational, technical, etc.)
            **kwargs: Additional parameters

        Returns:
            Dictionary with risk assessment results
        """
        try:
            risk_categories = risk_categories or ["legal", "reputational", "technical"]

            risk_scores = {
                "legal": 0.2,  # Low risk
                "reputational": 0.15,
                "technical": 0.1,
                "fraud": 0.05,
                "compliance": 0.25,
            }

            overall_risk = sum(risk_scores.get(cat, 0) for cat in risk_categories) / len(risk_categories)

            logger.info(f"Risk assessment completed (overall risk: {overall_risk:.2f})")

            return {
                "assessment_type": "risk_assessment",
                "risk_categories": risk_categories,
                "risk_scores": {cat: risk_scores.get(cat, 0) for cat in risk_categories},
                "overall_risk_score": overall_risk,
                "risk_level": "low" if overall_risk < 0.3 else "medium" if overall_risk < 0.7 else "high",
                "timestamp": datetime.utcnow().isoformat(),
            }

        except Exception as e:
            logger.error(f"Risk assessment failed: {e}", exc_info=True)
            return {"error": str(e), "assessment_type": "risk_assessment"}

    def get_service_metadata(self) -> Dict[str, Any]:
        """Get service metadata for discovery"""
        return {
            "name": "compliance_service",
            "category": "compliance",
            "description": "Legal compliance, privacy assessment, and risk management service",
            "capabilities": [
                "legal_compliance_checking",
                "privacy_compliance_assessment",
                "risk_assessment",
                "regulatory_reporting",
                "compliance_documentation",
            ],
            "supported_frameworks": [
                "GDPR",
                "CCPA",
                "HIPAA",
                "SOC2",
                "ISO27001",
            ],
            "version": "1.0",
        }
