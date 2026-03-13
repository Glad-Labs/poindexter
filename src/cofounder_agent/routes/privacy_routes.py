"""GDPR Data Subject Rights Routes."""

from services.logger_config import get_logger
import os
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import asyncpg
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from routes.auth_unified import get_current_user
from services.database_service import DatabaseService
from services.gdpr_service import GDPRService
from utils.route_utils import get_database_dependency

logger = get_logger(__name__)
router = APIRouter(prefix="/api/privacy", tags=["privacy"])


class DataSubjectRequest(BaseModel):
    """GDPR Data Subject Request"""

    request_type: str = Field(
        ...,
        description="Type: access, deletion, portability, correction, restriction, objection, other",
    )
    email: str = Field(..., description="User's email for identity verification")
    name: Optional[str] = Field(None, description="User's full name")
    details: Optional[str] = Field(None, description="Additional request details")
    data_categories: Optional[List[str]] = Field(
        None, description="Data categories involved: analytics, advertising, cookies, logs, all"
    )


def validate_email(email: str) -> bool:
    """Simple email validation"""
    pattern = r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$"
    return bool(re.match(pattern, email))


async def _send_verification_email(email: str, request_id: str, verification_link: str) -> None:
    """Send verification email (logs delivery for local/dev and fallback mode)."""
    try:
        # In development and fallback mode we log the verification link to keep flow testable.
        logger.info(
            "[gdpr_send_verification_email] Verification email queued for %s (request_id=%s, link=%s)",
            email,
            request_id,
            verification_link,
        )
    except (OSError, RuntimeError, AttributeError) as e:
        logger.error(
            f"[gdpr_send_verification_email] Failed to send verification email for request {request_id}: {e}",
            exc_info=True,
        )
        raise


@router.post("/data-requests", response_model=Dict[str, Any])
async def submit_data_request(
    request_data: DataSubjectRequest,
    background_tasks: BackgroundTasks,
    db: DatabaseService = Depends(get_database_dependency),
) -> Dict[str, Any]:
    """
    Submit a GDPR data subject request.

    Per GDPR Article 12, we must:
    1. Acknowledge receipt within 2 weeks
    2. Verify user identity (opt-in via email link)
    3. Fulfill request within 30 days (extendable to 90 for complex requests)
    4. Provide proof of compliance

    This endpoint stores the request and triggers verification email.
    """

    # Validate email
    if not validate_email(request_data.email):
        raise HTTPException(status_code=400, detail="Invalid email address")

    # Validate request type
    valid_types = [
        "access",
        "deletion",
        "portability",
        "correction",
        "restriction",
        "objection",
        "other",
    ]
    if request_data.request_type not in valid_types:
        raise HTTPException(
            status_code=400, detail=f"Invalid request type. Must be one of: {valid_types}"
        )

    try:
        logmsg = (
            f"📋 GDPR Data Request Received - "
            f"Type: {request_data.request_type} | Email: {request_data.email} | "
            f"Data Categories: {', '.join(request_data.data_categories or [])}"
        )
        logger.info(logmsg)

        gdpr_service = GDPRService(db)
        created = await gdpr_service.create_request(
            request_type=request_data.request_type,
            email=request_data.email,
            name=request_data.name,
            details=request_data.details,
            data_categories=request_data.data_categories,
        )

        request_id = str(created["id"])
        token = created["verification_token"]
        base_url = os.getenv("PUBLIC_BASE_URL", "http://localhost:8000")
        verification_link = f"{base_url}/api/privacy/data-requests/verify/{token}"

        background_tasks.add_task(
            _send_verification_email, request_data.email, request_id, verification_link
        )
        await gdpr_service.mark_verification_sent(request_id)

        return {
            "status": "success",
            "message": (
                "Your data request has been received. "
                "Check your email for a verification link. "
                "Once verified, we'll process your request within 30 days."
            ),
            "request_id": request_id,
            "verification_required": True,
            "verification_link_preview": verification_link,
            "processing_deadline": (
                created["deadline_at"].isoformat() if created.get("deadline_at") else None
            ),
            "next_steps": [
                "1. Verify your email address (link sent to your inbox)",
                "2. We'll confirm receipt within 2 weeks",
                "3. Process your request within 30 days",
                "4. Provide proof of compliance",
            ],
            "support_email": "privacy@gladlabs.ai",
        }

    except (asyncpg.PostgresError, KeyError, AttributeError, TypeError) as e:
        logger.error(
            f"[submit_data_request] Error processing GDPR data request: {e}", exc_info=True
        )
        raise HTTPException(
            status_code=500, detail="Failed to process request. Please email privacy@gladlabs.ai"
        ) from e


@router.get("/data-requests/verify/{token}", response_model=Dict[str, Any])
async def verify_data_request(
    token: str,
    db: DatabaseService = Depends(get_database_dependency),
) -> Dict[str, Any]:
    """Verify GDPR request ownership using one-time token."""
    try:
        gdpr_service = GDPRService(db)
        verified = await gdpr_service.verify_request(token)
        if verified is None:
            raise HTTPException(status_code=404, detail="Invalid or expired verification token")

        return {
            "status": "verified",
            "request_id": str(verified["id"]),
            "request_type": verified["request_type"],
            "verified_at": (
                verified["verified_at"].isoformat() if verified.get("verified_at") else None
            ),
            "processing_deadline": (
                verified["deadline_at"].isoformat() if verified.get("deadline_at") else None
            ),
            "message": "Request verified. Processing can now begin.",
        }
    except HTTPException:
        raise
    except (asyncpg.PostgresError, KeyError, AttributeError, TypeError) as e:
        logger.error(
            f"[verify_data_request] Failed to verify GDPR request token: {e}", exc_info=True
        )
        raise HTTPException(status_code=500, detail="Failed to verify GDPR request") from e


@router.get("/data-requests/{request_id}", response_model=Dict[str, Any])
async def get_data_request_status(
    request_id: str,
    db: DatabaseService = Depends(get_database_dependency),
    current_user: dict = Depends(get_current_user),
) -> Dict[str, Any]:
    """Get GDPR request status and 30-day SLA deadline. Requires authentication (admin view)."""
    try:
        gdpr_service = GDPRService(db)
        request_data = await gdpr_service.get_request(request_id)
        if request_data is None:
            raise HTTPException(status_code=404, detail="GDPR request not found")

        deadline = request_data.get("deadline_at")
        now = datetime.now(timezone.utc)
        deadline_status = "on_track"
        if deadline and now > deadline:
            deadline_status = "overdue"

        return {
            "request_id": str(request_data["id"]),
            "request_type": request_data["request_type"],
            "status": request_data["status"],
            "created_at": (
                request_data["created_at"].isoformat() if request_data.get("created_at") else None
            ),
            "verified_at": (
                request_data["verified_at"].isoformat() if request_data.get("verified_at") else None
            ),
            "deadline_at": deadline.isoformat() if deadline else None,
            "deadline_status": deadline_status,
            "completed_at": (
                request_data["completed_at"].isoformat()
                if request_data.get("completed_at")
                else None
            ),
        }
    except HTTPException:
        raise
    except (asyncpg.PostgresError, KeyError, AttributeError, TypeError) as e:
        logger.error(
            f"[get_data_request_status] Failed to load GDPR request {request_id}: {e}",
            exc_info=True,
        )
        raise HTTPException(status_code=500, detail="Failed to retrieve GDPR request status") from e


@router.get("/data-requests/{request_id}/export", response_model=Dict[str, Any])
async def export_data_request(
    request_id: str,
    format: str = Query(default="json", pattern="^(json|csv)$"),
    db: DatabaseService = Depends(get_database_dependency),
    current_user: dict = Depends(get_current_user),
) -> Dict[str, Any]:
    """Export data for verified access/portability GDPR requests. Requires authentication (admin)."""
    try:
        gdpr_service = GDPRService(db)
        payload = await gdpr_service.export_user_data(request_id=request_id, fmt=format)
        return payload
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except (asyncpg.PostgresError, KeyError, AttributeError, TypeError) as e:
        logger.error(
            f"[export_data_request] Failed to export GDPR request {request_id}: {e}", exc_info=True
        )
        raise HTTPException(status_code=500, detail="Failed to export GDPR data") from e


@router.post("/data-requests/{request_id}/process-deletion", response_model=Dict[str, Any])
async def process_deletion_request(
    request_id: str,
    db: DatabaseService = Depends(get_database_dependency),
    current_user: dict = Depends(get_current_user),
) -> Dict[str, Any]:
    """Start deletion workflow and enforce 30-day deadline tracking. Requires authentication (admin)."""
    try:
        gdpr_service = GDPRService(db)
        updated = await gdpr_service.record_deletion_processing(request_id)

        return {
            "request_id": str(updated["id"]),
            "status": updated["status"],
            "request_type": updated["request_type"],
            "deadline_at": (
                updated["deadline_at"].isoformat() if updated.get("deadline_at") else None
            ),
            "message": "Deletion workflow started and is tracked against GDPR 30-day SLA.",
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except (asyncpg.PostgresError, KeyError, AttributeError, TypeError) as e:
        logger.error(
            f"[process_deletion_request] Failed to start deletion processing for request {request_id}: {e}",
            exc_info=True,
        )
        raise HTTPException(status_code=500, detail="Failed to process deletion workflow") from e


@router.get("/gdpr-rights", response_model=Dict[str, Any])
async def get_gdpr_rights() -> Dict[str, Any]:
    """
    Returns information about GDPR rights available to users.
    """
    return {
        "jurisdiction": "EU/GDPR (applies if user is in EU)",
        "applicable_articles": [15, 16, 17, 18, 20, 21, 22],
        "rights": {
            "access": {
                "article": 15,
                "description": "Right to obtain a copy of your personal data",
                "deadline_days": 30,
                "extension_days": 60,
            },
            "rectification": {
                "article": 16,
                "description": "Right to correct inaccurate data",
                "deadline_days": 30,
                "extension_days": 60,
            },
            "erasure": {
                "article": 17,
                "description": "Right to deletion ('right to be forgotten')",
                "deadline_days": 30,
                "extension_days": 60,
                "exceptions": [
                    "Ongoing legal proceedings",
                    "Compliance with legal obligation",
                    "Public interest",
                    "Legitimate interest",
                ],
            },
            "restrict_processing": {
                "article": 18,
                "description": "Right to restrict processing",
                "deadline_days": 30,
                "extension_days": 60,
            },
            "data_portability": {
                "article": 20,
                "description": "Right to receive data in portable format",
                "formats": ["JSON", "CSV"],
                "deadline_days": 30,
                "extension_days": 60,
            },
            "objection": {
                "article": 21,
                "description": "Right to object to processing",
                "deadline_days": 30,
            },
            "automated_decision_making": {
                "article": 22,
                "description": "Right not to be subject to automated decision-making",
                "applies": False,
                "note": "We do not use automated decision-making for legal effects",
            },
            "withdraw_consent": {
                "article": "7(3)",
                "description": "Right to withdraw consent at any time",
                "method": "Change cookie preferences or email privacy@gladlabs.ai",
            },
            "lodge_complaint": {
                "article": 77,
                "description": "Right to lodge complaint with supervisory authority",
                "authority": "Your national Data Protection Authority (DPA)",
                "note": "You do not need to file a complaint with us first",
            },
        },
        "contact": "privacy@gladlabs.ai",
        "response_deadline_days": 30,
        "response_deadline_extension_days": 60,
        "verification": {
            "required": True,
            "method": "Email verification link",
            "purpose": "Verify identity and prevent unauthorized access",
            "timeline_days": 7,
        },
    }


@router.get("/data-processing", response_model=Dict[str, Any])
async def get_data_processing_info() -> Dict[str, Any]:
    """
    Returns information about how data is processed.
    """
    return {
        "legal_bases": {
            "consent": {
                "article": "6(1)(a)",
                "description": "Explicit user consent",
                "examples": ["Analytics cookies", "Advertising cookies"],
                "how_to_withdraw": "Change cookie preferences or contact privacy@gladlabs.ai",
            },
            "contract": {
                "article": "6(1)(b)",
                "description": "Necessary for contract performance",
                "examples": ["Session cookies", "Website functionality"],
            },
            "legal_obligation": {
                "article": "6(1)(c)",
                "description": "Required by law",
                "examples": ["Security logs", "Fraud prevention"],
            },
            "legitimate_interest": {
                "article": "6(1)(f)",
                "description": "Legitimate interest of controller",
                "examples": ["Website optimization", "User experience improvement"],
            },
        },
        "data_categories": {
            "usage_data": {
                "description": "Pages visited, time spent, interactions",
                "source": "Cookies, server logs",
                "retention": "14-30 months",
                "recipients": ["Google Analytics"],
            },
            "ip_address": {
                "description": "Internet Protocol address",
                "source": "Server logs",
                "retention": "90 days",
                "recipients": ["Vercel (hosting provider)"],
                "anonymized": True,
            },
            "cookie_preferences": {
                "description": "Your consent choices",
                "source": "Browser localStorage",
                "retention": "365 days",
                "recipients": ["No third parties"],
            },
            "advertising_data": {
                "description": "Interests, ad interactions",
                "source": "Google AdSense",
                "retention": "30 months",
                "recipients": ["Google AdSense"],
            },
        },
        "processors": [
            {
                "name": "Google LLC",
                "services": ["Analytics 4", "AdSense"],
                "location": "United States",
                "scc": True,
                "privacy_policy": "https://policies.google.com/privacy",
            },
            {
                "name": "Vercel Inc",
                "services": ["Hosting", "CDN"],
                "location": "United States",
                "scc": True,
                "privacy_policy": "https://vercel.com/legal/privacy",
            },
        ],
    }
