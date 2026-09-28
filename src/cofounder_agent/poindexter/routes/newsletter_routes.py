"""
Newsletter & Email Campaign Routes

Endpoints for managing email campaign subscriptions and newsletter signups.

The public site does NOT call ``POST /subscribe`` — Vercel cannot reach this
worker, which has no public ingress. Public signups land in the Resend segment
and reach ``newsletter_subscribers`` through ``SyncNewsletterAudienceJob``
(``services/newsletter_audience.py``). This route serves direct API callers.

A signup stores the address and first/last name, the same fields the segment
sync writes. ``NewsletterSubscribeRequest`` lists the retired fields that older
callers may still send.
"""


from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, EmailStr, Field

from middleware.api_token_auth import verify_api_token
from poindexter.services.logger_config import get_logger
from poindexter.services.newsletter_audience import (
    mint_unsubscribe_token,
    mirror_signup_to_segment,
)
from poindexter.services.site_config import SiteConfig
from poindexter.utils.deprecation import deprecation_headers
from poindexter.utils.rate_limiter import limiter
from poindexter.utils.route_utils import get_database_dependency, get_site_config_dependency

logger = get_logger(__name__)


router = APIRouter(prefix="/api/newsletter", tags=["newsletter"])


#: Request fields ``POST /subscribe`` stored until 2026-09-28. Nothing ever read
#: them, so they are no longer stored and migration 20260928_184647 dropped their
#: columns (Glad-Labs/poindexter#1109). They stay on the request model so an
#: older caller that still sends them gets its signup, not a 422, plus a
#: ``Deprecation`` header that names what was ignored.
_RETIRED_SUBSCRIBE_FIELDS: tuple[str, ...] = (
    "company",
    "interest_categories",
    "marketing_consent",
)

_RETIRED_FIELD_NOTE = (
    "Retired 2026-09-28: accepted and ignored, never stored. "
    "Send only email, first_name and last_name."
)


class NewsletterSubscribeRequest(BaseModel):
    """Newsletter subscription request.

    Only ``email``, ``first_name`` and ``last_name`` are stored. The three
    deprecated fields keep their old types, so the payloads that validated
    before still validate and none that failed before now passes. Their values
    are never read.
    """

    email: EmailStr
    first_name: str | None = None
    last_name: str | None = None
    company: str | None = Field(default=None, deprecated=_RETIRED_FIELD_NOTE)
    interest_categories: list[str] | None = Field(default=None, deprecated=_RETIRED_FIELD_NOTE)
    marketing_consent: bool = Field(default=False, deprecated=_RETIRED_FIELD_NOTE)

    def retired_fields_sent(self) -> list[str]:
        """The retired fields this request set, in declaration order.

        Reads ``model_fields_set``, not the values: an explicit ``null`` or
        ``false`` is still the old shape, and reading a deprecated field would
        emit pydantic's ``DeprecationWarning`` for no benefit.
        """
        return [name for name in _RETIRED_SUBSCRIBE_FIELDS if name in self.model_fields_set]


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
    request: Request,
    response: Response,
    payload: NewsletterSubscribeRequest,
    db=Depends(get_database_dependency),
    site_config: SiteConfig = Depends(get_site_config_dependency),
):
    """Subscribe an email directly through the worker API.

    Not the public site's path: its form captures into the Resend segment,
    which ``SyncNewsletterAudienceJob`` pulls into the same table.
    """
    retired = payload.retired_fields_sent()
    if retired:
        # Set before any branch, so the already-subscribed reply carries it too.
        # It depends only on the request, so it reveals nothing about the address.
        names = ", ".join(retired)
        logger.warning(
            "[newsletter] POST /api/newsletter/subscribe ignored retired field(s) %s: "
            "they are no longer stored. Update the caller to send only email, "
            "first_name and last_name.",
            names,
        )
        response.headers.update(
            deprecation_headers(
                message=(
                    f"Ignored retired field(s): {names}. Send only email, first_name and last_name."
                )
            )
        )

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

        # Mint a per-subscriber unsubscribe credential. On re-subscribe
        # (the ON CONFLICT branch below) we deliberately rotate the
        # token — re-subscribing semantically begins a new relationship
        # and a fresh credential is the safer default (old link from a
        # prior subscription becomes dead).
        unsubscribe_token = mint_unsubscribe_token()

        # Insert new subscriber: the same fields the segment sync writes. The
        # caller's IP address and user-agent are deliberately not recorded. They
        # describe whatever called this route (a proxy hop or a server-side
        # fetch), never the subscriber, and nothing read them
        # (Glad-Labs/poindexter#1109).
        subscriber_id = await (getattr(db, "cloud_pool", None) or db.pool).fetchval(
            """
            INSERT INTO newsletter_subscribers
            (email, first_name, last_name, verified, unsubscribe_token)
            VALUES ($1, $2, $3, $4, $5)
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
            True,  # Verified on signup — no double opt-in (same as the segment sync)
            unsubscribe_token,
        )

        logger.info("Newsletter subscriber added: %s (ID: %s)", payload.email, subscriber_id)

        # Best-effort: mirror into the Resend segment so Resend-side tooling
        # sees every subscriber. Never fails the signup — the DB row above is
        # the system of record.
        await mirror_signup_to_segment(
            site_config,
            email=payload.email,
            first_name=payload.first_name,
            last_name=payload.last_name,
        )

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


@router.get("/stats")
async def get_newsletter_stats(
    db=Depends(get_database_dependency),
    token: str = Depends(verify_api_token),
):
    """Operator newsletter stats: subscriber count + campaign delivery summary.

    Delegates to services.newsletter_service.get_newsletter_stats so the
    route stays a thin adapter (transport-adapter contract, ADR 2026-06-10).
    """
    try:
        from poindexter.services.newsletter_service import get_newsletter_stats as _stats

        pool = getattr(db, "cloud_pool", None) or db.pool
        return await _stats(pool)
    except Exception as e:
        logger.error("Newsletter stats error: %s", e, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch newsletter stats",
        ) from e
