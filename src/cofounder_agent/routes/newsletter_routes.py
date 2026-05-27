"""
Newsletter & Email Campaign Routes

Endpoints for managing email campaign subscriptions and newsletter signups.
"""


import secrets

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, EmailStr

from middleware.api_token_auth import verify_api_token
from services.logger_config import get_logger
from utils.rate_limiter import limiter
from utils.route_utils import get_database_dependency

logger = get_logger(__name__)


router = APIRouter(prefix="/api/newsletter", tags=["newsletter"])


def _mint_unsubscribe_token() -> str:
    """Per-subscriber unsubscribe credential.

    ``secrets.token_urlsafe(32)`` is 43 base64url chars ≈ 256 bits of
    entropy — generous against guess-and-replay. The endpoint looks up
    by token, so an attacker has to brute-force a real token-space
    collision to unsubscribe anyone they don't already have the URL
    for. Rate-limit + UNIQUE index on the column make that
    operationally infeasible.
    """
    return secrets.token_urlsafe(32)


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
    """Newsletter unsubscribe request.

    Cycle-5 audit (#252) hardened this: token is required and is the
    sole lookup key. Email is no longer accepted — accepting it let
    anyone who guessed an address unsubscribe arbitrary subscribers.
    The token ships per-subscriber in the email template's
    unsubscribe link (and the List-Unsubscribe header).
    """

    unsubscribe_token: str
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

        # Mint a per-subscriber unsubscribe credential. On re-subscribe
        # (the ON CONFLICT branch below) we deliberately rotate the
        # token — re-subscribing semantically begins a new relationship
        # and a fresh credential is the safer default (old link from a
        # prior subscription becomes dead).
        unsubscribe_token = _mint_unsubscribe_token()

        # Insert new subscriber
        subscriber_id = await (getattr(db, "cloud_pool", None) or db.pool).fetchval(
            """
            INSERT INTO newsletter_subscribers
            (email, first_name, last_name, company, interest_categories,
             ip_address, user_agent, marketing_consent, verified,
             unsubscribe_token)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
            ON CONFLICT (email) DO UPDATE
            SET unsubscribed_at = NULL,
                verified = TRUE,
                unsubscribe_token = EXCLUDED.unsubscribe_token,
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
            unsubscribe_token,
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
    """Unsubscribe via per-subscriber token. Cycle-5 audit (#252) made
    the token mandatory — the previous email-keyed lookup let anyone
    who knew/guessed an address unsubscribe arbitrary subscribers
    (rate-limit-only protection is trivially bypassable from
    distributed sources). The token ships in the email template's
    unsubscribe URL and the ``List-Unsubscribe`` header.

    Returns a generic response in both the hit and miss cases so that
    an attacker grinding through random tokens cannot tell which ones
    were valid.
    """
    try:
        # Token lookup. Reject unknown tokens by returning the same
        # generic response — leaks zero information about valid tokens.
        result = await (getattr(db, "cloud_pool", None) or db.pool).execute(
            """
            UPDATE newsletter_subscribers
            SET unsubscribed_at = CURRENT_TIMESTAMP,
                unsubscribe_reason = $2,
                updated_at = CURRENT_TIMESTAMP
            WHERE unsubscribe_token = $1 AND unsubscribed_at IS NULL
            """,
            payload.unsubscribe_token,
            payload.reason,
        )

        if result == "UPDATE 0":
            # Either an invalid token OR already unsubscribed. Log the
            # token PREFIX only (8 chars is enough to grep audit logs
            # for repeated probe attempts but doesn't itself leak a
            # working credential into log files).
            token_prefix = payload.unsubscribe_token[:8] if payload.unsubscribe_token else ""
            logger.info(
                "[newsletter_unsubscribe] No active subscription for token prefix %r "
                "(invalid token, or already unsubscribed)",
                token_prefix,
            )
        else:
            logger.info("[newsletter_unsubscribe] Successfully unsubscribed (token consumed)")

        # Always return the same response — refusing to confirm whether
        # the token was valid stops attackers from using the endpoint
        # as a token-validity oracle.
        return NewsletterSubscribeResponse(
            success=True, message="If this link was valid, the subscription has been removed."
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
