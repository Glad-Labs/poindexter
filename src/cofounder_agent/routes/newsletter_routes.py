"""
Newsletter & Email Campaign Routes

Endpoints for managing email campaign subscriptions and newsletter signups.
"""

import logging
import re
from datetime import datetime
from typing import Optional, List

from fastapi import APIRouter, HTTPException, status, Request, Depends
from pydantic import BaseModel, EmailStr

from utils.route_utils import get_database_dependency

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/newsletter", tags=["newsletter"])


class NewsletterSubscribeRequest(BaseModel):
    """Newsletter subscription request"""
    email: EmailStr
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    company: Optional[str] = None
    interest_categories: Optional[List[str]] = None  # ["AI", "Technology", "Automation"]
    marketing_consent: bool = False


class NewsletterSubscribeResponse(BaseModel):
    """Newsletter subscription response"""
    success: bool
    message: str
    subscriber_id: Optional[int] = None
    
    model_config = {"from_attributes": True}


class NewsletterUnsubscribeRequest(BaseModel):
    """Newsletter unsubscribe request"""
    email: str
    reason: Optional[str] = None


@router.post("/subscribe")
async def subscribe_to_newsletter(
    request: Request,
    payload: NewsletterSubscribeRequest,
    db = Depends(get_database_dependency)
):
    """
    Subscribe email to newsletter campaign list.
    
    Endpoint for "Get Updates" button on public site.
    
    Args:
        request: HTTP request (for IP/user agent logging)
        payload: Subscription data
        db: Database service
    
    Returns:
        Subscription confirmation with ID
    """
    try:
        # Basic email validation
        if not payload.email or '@' not in payload.email:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid email address"
            )
        
        # Check for existing subscription
        existing = await db.pool.fetchrow(
            """
            SELECT id, unsubscribed_at FROM newsletter_subscribers 
            WHERE email = $1
            """,
            payload.email
        )
        
        if existing and not existing['unsubscribed_at']:
            return NewsletterSubscribeResponse(
                success=False,
                message=f"Email {payload.email} is already subscribed",
                subscriber_id=existing['id']
            )
        
        # Get client IP and user agent
        client_ip = request.client.host if request.client else None
        user_agent = request.headers.get('user-agent', '')
        
        # Prepare interest categories as JSON string
        interest_str = None
        if payload.interest_categories:
            import json
            interest_str = json.dumps(payload.interest_categories)
        
        # Insert new subscriber
        subscriber_id = await db.pool.fetchval(
            """
            INSERT INTO newsletter_subscribers 
            (email, first_name, last_name, company, interest_categories, 
             ip_address, user_agent, marketing_consent, verified)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
            ON CONFLICT (email) DO UPDATE 
            SET unsubscribed_at = NULL, 
                verified = TRUE,
                updated_at = CURRENT_TIMESTAMP
            RETURNING id
            """,
            payload.email,
            payload.first_name,
            payload.last_name,
            payload.company,
            interest_str,
            client_ip,
            user_agent,
            payload.marketing_consent,
            True  # Mark as verified immediately on public signup
        )
        
        logger.info(f"✅ New newsletter subscriber: {payload.email} (ID: {subscriber_id})")
        
        return NewsletterSubscribeResponse(
            success=True,
            message=f"Successfully subscribed to newsletter and campaign updates",
            subscriber_id=subscriber_id
        )
        
    except Exception as e:
        logger.error(f"❌ Newsletter subscription error: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to process subscription"
        )


@router.post("/unsubscribe")
async def unsubscribe_from_newsletter(
    payload: NewsletterUnsubscribeRequest,
    db = Depends(get_database_dependency)
):
    """
    Unsubscribe email from newsletter.
    
    Args:
        payload: Email and optional unsubscribe reason
        db: Database service
    
    Returns:
        Unsubscribe confirmation
    """
    try:
        # Update subscriber as unsubscribed
        result = await db.pool.execute(
            """
            UPDATE newsletter_subscribers
            SET unsubscribed_at = CURRENT_TIMESTAMP,
                unsubscribe_reason = $2,
                updated_at = CURRENT_TIMESTAMP
            WHERE email = $1 AND unsubscribed_at IS NULL
            """,
            payload.email,
            payload.reason
        )
        
        if result == "UPDATE 0":
            return NewsletterSubscribeResponse(
                success=False,
                message=f"Email {payload.email} not found or already unsubscribed"
            )
        
        logger.info(f"✅ Unsubscribed from newsletter: {payload.email}")
        
        return NewsletterSubscribeResponse(
            success=True,
            message="Successfully unsubscribed from newsletter"
        )
        
    except Exception as e:
        logger.error(f"❌ Unsubscribe error: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to process unsubscribe"
        )


@router.get("/subscribers/count")
async def get_subscriber_count(db = Depends(get_database_dependency)):
    """Get total active newsletter subscribers count"""
    try:
        count = await db.pool.fetchval(
            """
            SELECT COUNT(*) FROM newsletter_subscribers 
            WHERE unsubscribed_at IS NULL AND verified = TRUE
            """
        )
        
        return {
            "success": True,
            "subscriber_count": count or 0
        }
    except Exception as e:
        logger.error(f"❌ Error fetching subscriber count: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch subscriber count"
        )
