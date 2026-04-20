"""
Newsletter & Email Campaign Routes

Endpoints for managing email campaign subscriptions and newsletter signups.
"""


from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, EmailStr

from middleware.api_token_auth import verify_api_token
from services.logger_config import get_logger
from utils.rate_limiter import limiter
from utils.route_utils import get_database_dependency

logger = get_logger(__name__)


router = APIRouter(prefix="/api/newsletter", tags=["newsletter"])


class NewsletterSubscribeRequest(BaseModel):
    """Newsletter subscription request"""

    email: EmailStr
    first_name: str | None = None
    last_name: str | None = None
    company: str | None = None
    interest_categories: list[str] | None = None  # ["AI", "Technology", "Automation"]
    marketing_consent: bool = False


class NewsletterSubscribeResponse(BaseModel):
    """Newsletter subscription response"""

    success: bool
    message: str
    subscriber_id: int | None = None

    model_config = {"from_attributes": True}


class NewsletterUnsubscribeRequest(BaseModel):
    """Newsletter unsubscribe request"""

    email: str
    reason: str | None = None


@router.post("/subscribe", response_model=NewsletterSubscribeResponse)
@limiter.limit("5/minute")
async def subscribe_to_newsletter(
    request: Request, payload: NewsletterSubscribeRequest, db=Depends(get_database_dependency)
):
    """Subscribe email to newsletter. Used by the public site 'Get Updates' button."""
    try:
        # Basic email validation
        if not payload.email or "@" not in payload.email:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid email address"
            )

        # Check for existing subscription — return generic message to prevent email enumeration.
        # An attacker must not be able to determine whether an email is already registered.
        existing = await (getattr(db, "cloud_pool", None) or db.pool).fetchrow(
            """
            SELECT id, unsubscribed_at FROM newsletter_subscribers
            WHERE email = $1
            """,
            payload.email,
        )

        if existing and not existing["unsubscribed_at"]:
            # Return generic success — do not reveal whether the email was already subscribed.
            return NewsletterSubscribeResponse(
                success=True,
                message="If this email is not already subscribed, you will receive a confirmation shortly.",
            )

        # Get client IP and user agent
        client_ip = request.client.host if request.client else None
        user_agent = request.headers.get("user-agent", "")

        # Prepare interest categories as JSON string
        interest_str = None
        if payload.interest_categories:
            import json

            interest_str = json.dumps(payload.interest_categories)

        # Insert new subscriber
        subscriber_id = await (getattr(db, "cloud_pool", None) or db.pool).fetchval(
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
            True,  # Mark as verified immediately on public signup
        )

        logger.info("Newsletter subscriber added: %s (ID: %s)", payload.email, subscriber_id)

        return NewsletterSubscribeResponse(
            success=True,
            message="Successfully subscribed to newsletter and campaign updates",
            subscriber_id=subscriber_id,
        )

    except Exception as e:
        logger.error("Newsletter subscription error: %s", e, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to process subscription",
        ) from e


@router.post("/unsubscribe")
@limiter.limit("5/minute")
async def unsubscribe_from_newsletter(
    request: Request, payload: NewsletterUnsubscribeRequest, db=Depends(get_database_dependency)
):
    """Unsubscribe email from newsletter. Returns generic response to prevent email enumeration."""
    try:
        # Update subscriber as unsubscribed
        result = await (getattr(db, "cloud_pool", None) or db.pool).execute(
            """
            UPDATE newsletter_subscribers
            SET unsubscribed_at = CURRENT_TIMESTAMP,
                unsubscribe_reason = $2,
                updated_at = CURRENT_TIMESTAMP
            WHERE email = $1 AND unsubscribed_at IS NULL
            """,
            payload.email,
            payload.reason,
        )

        if result == "UPDATE 0":
            logger.info(
                "[newsletter_unsubscribe] No active subscription found for email (not revealing to client)"
            )
        else:
            logger.info("[newsletter_unsubscribe] Successfully unsubscribed: %s", payload.email)

        # Always return the same response to prevent email enumeration
        return NewsletterSubscribeResponse(
            success=True, message="If this email was subscribed, it has been removed."
        )

    except Exception as e:
        logger.error("Unsubscribe error: %s", e, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to process unsubscribe",
        ) from e


@router.get("/subscribers/count")
async def get_subscriber_count(
    db=Depends(get_database_dependency),
    token: str = Depends(verify_api_token),
):
    """Get total active newsletter subscribers count"""
    try:
        count = await (getattr(db, "cloud_pool", None) or db.pool).fetchval("""
            SELECT COUNT(*) FROM newsletter_subscribers 
            WHERE unsubscribed_at IS NULL AND verified = TRUE
            """)

        return {"success": True, "subscriber_count": count or 0}
    except Exception as e:
        logger.error("Subscriber count error: %s", e, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch subscriber count",
        ) from e
