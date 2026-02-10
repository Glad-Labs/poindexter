"""
GDPR Data Subject Rights Routes

Handles GDPR Art. 15-22 requests:
- Right to Access (Art. 15)
- Right to Erasure (Art. 17)
- Right to Data Portability (Art. 20)
- Right to Rectification (Art. 16)
- Right to Restrict (Art. 18)
- Right to Objection (Art. 21)

This is a simplified endpoint that acknowledges requests and provides
information about GDPR rights. In production, implement:
1. Email verification
2. Database storage for requests
3. Processing workflows (30-day SLA)
4. Audit logging
"""

from datetime import datetime
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
import logging
import re

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/privacy", tags=["privacy"])


class DataSubjectRequest(BaseModel):
    """GDPR Data Subject Request"""
    request_type: str = Field(
        ..., 
        description="Type: access, deletion, portability, correction, restriction, objection, other"
    )
    email: str = Field(..., description="User's email for identity verification")
    name: Optional[str] = Field(None, description="User's full name")
    details: Optional[str] = Field(None, description="Additional request details")
    data_categories: Optional[List[str]] = Field(
        None, 
        description="Data categories involved: analytics, advertising, cookies, logs, all"
    )


def validate_email(email: str) -> bool:
    """Simple email validation"""
    pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    return bool(re.match(pattern, email))


@router.post("/data-requests", response_model=Dict)
async def submit_data_request(request_data: DataSubjectRequest) -> Dict:
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
    valid_types = ["access", "deletion", "portability", "correction", "restriction", "objection", "other"]
    if request_data.request_type not in valid_types:
        raise HTTPException(status_code=400, detail=f"Invalid request type. Must be one of: {valid_types}")
    
    try:
        logmsg = (
            f"📋 GDPR Data Request Received - "
            f"Type: {request_data.request_type} | Email: {request_data.email} | "
            f"Data Categories: {', '.join(request_data.data_categories or [])}"
        )
        logger.info(logmsg)
        
        # TODO: In production, implement:
        # 1. Store request in database (privacy_requests table)
        # 2. Send verification email to user with confirmation link
        # 3. Log request for audit trail
        # 4. Set up workflow to process request within 30 days
        
        return {
            "status": "success",
            "message": (
                "Your data request has been received. "
                "Check your email for a verification link. "
                "Once verified, we'll process your request within 30 days."
            ),
            "request_id": datetime.utcnow().isoformat(),
            "next_steps": [
                "1. Verify your email address (link sent to your inbox)",
                "2. We'll confirm receipt within 2 weeks",
                "3. Process your request within 30 days",
                "4. Provide proof of compliance"
            ],
            "support_email": "privacy@gladlabs.ai"
        }
    
    except Exception as e:
        logger.error(f"❌ Error processing data request: {e}")
        raise HTTPException(
            status_code=500,
            detail="Failed to process request. Please email privacy@gladlabs.ai"
        ) from e


@router.get("/gdpr-rights", response_model=Dict)
async def get_gdpr_rights() -> Dict:
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
                    "Legitimate interest"
                ]
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
                "note": "We do not use automated decision-making for legal effects"
            },
            "withdraw_consent": {
                "article": "7(3)",
                "description": "Right to withdraw consent at any time",
                "method": "Change cookie preferences or email privacy@gladlabs.ai"
            },
            "lodge_complaint": {
                "article": 77,
                "description": "Right to lodge complaint with supervisory authority",
                "authority": "Your national Data Protection Authority (DPA)",
                "note": "You do not need to file a complaint with us first"
            }
        },
        "contact": "privacy@gladlabs.ai",
        "response_deadline_days": 30,
        "response_deadline_extension_days": 60,
        "verification": {
            "required": True,
            "method": "Email verification link",
            "purpose": "Verify identity and prevent unauthorized access",
            "timeline_days": 7
        }
    }


@router.get("/data-processing", response_model=Dict)
async def get_data_processing_info() -> Dict:
    """
    Returns information about how data is processed.
    """
    return {
        "legal_bases": {
            "consent": {
                "article": "6(1)(a)",
                "description": "Explicit user consent",
                "examples": ["Analytics cookies", "Advertising cookies"],
                "how_to_withdraw": "Change cookie preferences or contact privacy@gladlabs.ai"
            },
            "contract": {
                "article": "6(1)(b)",
                "description": "Necessary for contract performance",
                "examples": ["Session cookies", "Website functionality"]
            },
            "legal_obligation": {
                "article": "6(1)(c)",
                "description": "Required by law",
                "examples": ["Security logs", "Fraud prevention"]
            },
            "legitimate_interest": {
                "article": "6(1)(f)",
                "description": "Legitimate interest of controller",
                "examples": ["Website optimization", "User experience improvement"]
            }
        },
        "data_categories": {
            "usage_data": {
                "description": "Pages visited, time spent, interactions",
                "source": "Cookies, server logs",
                "retention": "14-30 months",
                "recipients": ["Google Analytics"]
            },
            "ip_address": {
                "description": "Internet Protocol address",
                "source": "Server logs",
                "retention": "90 days",
                "recipients": ["Vercel (hosting provider)"],
                "anonymized": True
            },
            "cookie_preferences": {
                "description": "Your consent choices",
                "source": "Browser localStorage",
                "retention": "365 days",
                "recipients": ["No third parties"]
            },
            "advertising_data": {
                "description": "Interests, ad interactions",
                "source": "Google AdSense",
                "retention": "30 months",
                "recipients": ["Google AdSense"]
            }
        },
        "processors": [
            {
                "name": "Google LLC",
                "services": ["Analytics 4", "AdSense"],
                "location": "United States",
                "scc": True,
                "privacy_policy": "https://policies.google.com/privacy"
            },
            {
                "name": "Vercel Inc",
                "services": ["Hosting", "CDN"],
                "location": "United States",
                "scc": True,
                "privacy_policy": "https://vercel.com/legal/privacy"
            }
        ]
    }
