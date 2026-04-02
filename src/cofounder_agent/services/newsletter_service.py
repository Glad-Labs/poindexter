"""
Newsletter Service — email capture and digest delivery.

Manages newsletter subscribers and generates weekly digest emails
from the best published content.

Subscribers are stored in the existing newsletter_subscribers table.
Delivery via Resend (free tier: 3,000 emails/month, 100/day).

Usage:
    from services.newsletter_service import generate_weekly_digest, send_digest_emails
    digest = await generate_weekly_digest(pool)
    await send_digest_emails(pool, digest)
"""

import os
from datetime import datetime, timezone
from typing import List, Optional

from services.logger_config import get_logger

logger = get_logger(__name__)

RESEND_API_KEY = os.getenv("RESEND_API_KEY", "")
FROM_EMAIL = os.getenv("NEWSLETTER_FROM_EMAIL", "Newsletter <newsletter@example.com>")
SITE_NAME = os.getenv("SITE_NAME", "My Content Site")
SITE_DOMAIN = os.getenv("SITE_DOMAIN", "localhost:3000")


async def get_subscriber_count(pool) -> int:
    """Get total active newsletter subscribers (not unsubscribed)."""
    try:
        row = await pool.fetchrow(
            "SELECT COUNT(*) as count FROM newsletter_subscribers WHERE unsubscribed_at IS NULL"
        )
        return int(row["count"]) if row else 0
    except Exception as e:
        logger.warning("[NEWSLETTER] Failed to query subscriber count: %s", e)
        return 0


async def get_active_subscribers(pool) -> List[dict]:
    """Get all active subscribers for sending."""
    try:
        rows = await pool.fetch(
            "SELECT email, first_name FROM newsletter_subscribers WHERE unsubscribed_at IS NULL"
        )
        return [dict(r) for r in rows]
    except Exception as e:
        logger.warning("[NEWSLETTER] Failed to fetch subscribers: %s", e)
        return []


async def get_top_posts_this_week(pool, limit: int = 5) -> List[dict]:
    """Get the best posts published this week."""
    try:
        rows = await pool.fetch("""
            SELECT title, slug, excerpt, published_at, seo_keywords
            FROM posts
            WHERE status = 'published'
            AND published_at > NOW() - INTERVAL '7 days'
            ORDER BY published_at DESC
            LIMIT $1
        """, limit)
        return [dict(r) for r in rows]
    except Exception as e:
        logger.warning("[NEWSLETTER] Failed to fetch posts: %s", e)
        return []


async def generate_weekly_digest(pool) -> dict:
    """Generate a weekly newsletter digest.

    Returns dict with:
      - subject: email subject line
      - text: plain text version
      - html: HTML version (simple)
      - post_count: number of posts featured
    """
    posts = await get_top_posts_this_week(pool)
    subscriber_count = await get_subscriber_count(pool)
    now = datetime.now(timezone.utc)

    if not posts:
        return {
            "subject": f"{SITE_NAME} Weekly — {now.strftime('%B %d, %Y')}",
            "text": "No new posts this week. Stay tuned!",
            "html": "<p>No new posts this week. Stay tuned!</p>",
            "post_count": 0,
            "subscriber_count": subscriber_count,
        }

    subject = f"{SITE_NAME} Weekly: {posts[0]['title'][:50]} + {len(posts)-1} more"

    # Plain text version
    text_lines = [
        f"{SITE_NAME} Weekly — {now.strftime('%B %d, %Y')}",
        f"{len(posts)} new articles this week\n",
    ]
    for i, post in enumerate(posts, 1):
        title = post["title"]
        slug = post["slug"]
        excerpt = (post.get("excerpt") or "")[:150]
        text_lines.append(f"{i}. {title}")
        text_lines.append(f"   {excerpt}")
        text_lines.append(f"   Read: https://{SITE_DOMAIN}/posts/{slug}\n")

    text_lines.append("---")
    text_lines.append("You received this because you subscribed at {SITE_DOMAIN}")
    text_lines.append("Unsubscribe: https://{SITE_DOMAIN}/newsletter/unsubscribe")
    text = "\n".join(text_lines)

    # Simple HTML version
    html_posts = ""
    for post in posts:
        title = post["title"]
        slug = post["slug"]
        excerpt = (post.get("excerpt") or "")[:150]
        html_posts += f"""
        <div style="margin-bottom: 24px; padding: 16px; border-left: 3px solid #22d3ee;">
            <h3 style="margin: 0 0 8px 0;"><a href="https://{SITE_DOMAIN}/posts/{slug}" style="color: #22d3ee; text-decoration: none;">{title}</a></h3>
            <p style="color: #94a3b8; margin: 0; font-size: 14px;">{excerpt}</p>
        </div>
        """

    html = f"""
    <div style="max-width: 600px; margin: 0 auto; font-family: -apple-system, sans-serif; color: #e2e8f0; background: #0f172a; padding: 32px;">
        <h1 style="color: #22d3ee; font-size: 24px;">{SITE_NAME} Weekly</h1>
        <p style="color: #94a3b8;">{now.strftime('%B %d, %Y')} — {len(posts)} new articles</p>
        {html_posts}
        <hr style="border-color: #334155;">
        <p style="color: #64748b; font-size: 12px;">
            You received this because you subscribed at {SITE_DOMAIN}<br>
            <a href="https://{SITE_DOMAIN}/newsletter/unsubscribe" style="color: #64748b;">Unsubscribe</a>
        </p>
    </div>
    """

    return {
        "subject": subject,
        "text": text,
        "html": html,
        "post_count": len(posts),
        "subscriber_count": subscriber_count,
    }


async def send_digest_to_telegram(pool, bot_token: str, chat_id: str) -> bool:
    """Send the weekly digest as a Telegram message (for testing/preview)."""
    import httpx

    digest = await generate_weekly_digest(pool)
    if digest["post_count"] == 0:
        return False

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(
                f"https://api.telegram.org/bot{bot_token}/sendMessage",
                json={
                    "chat_id": chat_id,
                    "text": digest["text"],
                    "parse_mode": "HTML",
                },
            )
        return True
    except Exception as e:
        logger.warning("[NEWSLETTER] Failed to send digest to Telegram: %s", e)
        return False


async def send_digest_emails(pool, digest: Optional[dict] = None) -> dict:
    """Send the weekly digest to all active subscribers via Resend.

    Returns dict with sent count, failed count, and errors.
    """
    if not RESEND_API_KEY:
        logger.warning("[NEWSLETTER] RESEND_API_KEY not set — skipping email delivery")
        return {"sent": 0, "failed": 0, "error": "RESEND_API_KEY not configured"}

    import resend
    resend.api_key = RESEND_API_KEY

    if digest is None:
        digest = await generate_weekly_digest(pool)

    if digest["post_count"] == 0:
        return {"sent": 0, "failed": 0, "error": "No posts this week"}

    subscribers = await get_active_subscribers(pool)
    if not subscribers:
        return {"sent": 0, "failed": 0, "error": "No active subscribers"}

    sent = 0
    failed = 0
    errors = []

    for sub in subscribers:
        try:
            resend.Emails.send({
                "from": FROM_EMAIL,
                "to": sub["email"],
                "subject": digest["subject"],
                "html": digest["html"],
                "text": digest["text"],
            })
            sent += 1
        except Exception as e:
            failed += 1
            errors.append(f"{sub['email']}: {e}")
            logger.warning("[NEWSLETTER] Failed to send to %s: %s", sub["email"], e)

    logger.info("[NEWSLETTER] Digest sent: %d success, %d failed out of %d subscribers",
                sent, failed, len(subscribers))
    return {"sent": sent, "failed": failed, "total": len(subscribers), "errors": errors[:5]}
