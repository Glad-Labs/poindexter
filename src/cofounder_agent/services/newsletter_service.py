"""
Newsletter Service — send emails to subscribers on post publish.

Supports two providers (configured via app_settings):
- Resend (free tier: 100/day, 3000/month) — newsletter_provider = "resend"
- SMTP (self-hosted or any SMTP server) — newsletter_provider = "smtp"

All configuration is DB-first via app_settings keys:
- newsletter_enabled: bool (default false)
- newsletter_provider: "resend" or "smtp"
- newsletter_from_email: sender address
- newsletter_from_name: sender display name
- resend_api_key: Resend API key
- smtp_host, smtp_port, smtp_user, smtp_password, smtp_use_tls: SMTP config
- newsletter_batch_size: emails per batch (default 50)
- newsletter_batch_delay_seconds: delay between batches (default 2)
"""

import asyncio
from typing import List, Optional

from services.logger_config import get_logger

from services.site_config import site_config

logger = get_logger(__name__)

SITE_URL = site_config.get("site_url", "")


def _cfg() -> dict:
    """Load newsletter config from DB."""
    from services.site_config import site_config

    return {
        "enabled": site_config.get_bool("newsletter_enabled", False),
        "provider": site_config.get("newsletter_provider", "resend"),
        "from_email": site_config.get("newsletter_from_email", ""),
        "from_name": site_config.get("newsletter_from_name", ""),
        "resend_api_key": site_config.get("resend_api_key", ""),
        "smtp_host": site_config.get("smtp_host", ""),
        "smtp_port": site_config.get_int("smtp_port", 587),
        "smtp_user": site_config.get("smtp_user", ""),
        "smtp_password": site_config.get("smtp_password", ""),
        "smtp_use_tls": site_config.get_bool("smtp_use_tls", True),
        "batch_size": site_config.get_int("newsletter_batch_size", 50),
        "batch_delay": site_config.get_int("newsletter_batch_delay_seconds", 2),
    }


async def _get_active_subscribers(pool) -> List[dict]:
    """Fetch all active, verified subscribers."""
    rows = await pool.fetch(
        "SELECT id, email, first_name FROM newsletter_subscribers "
        "WHERE unsubscribed_at IS NULL AND verified = TRUE "
        "ORDER BY id"
    )
    return [dict(r) for r in rows]


def _build_html(title: str, excerpt: str, slug: str, first_name: Optional[str] = None) -> str:
    """Build a simple, clean newsletter email body."""
    greeting = f"Hi {first_name}," if first_name else "Hi there,"
    post_url = f"{SITE_URL}/posts/{slug}"
    unsubscribe_url = f"{SITE_URL}/newsletter/unsubscribe"

    return f"""\
<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width"></head>
<body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; max-width: 600px; margin: 0 auto; padding: 20px; color: #1a1a1a;">
  <div style="border-bottom: 2px solid #6366f1; padding-bottom: 16px; margin-bottom: 24px;">
    <h2 style="margin: 0; color: #6366f1;">{site_config.get("company_name", "")}</h2>
  </div>

  <p>{greeting}</p>

  <p>We just published a new article:</p>

  <h1 style="font-size: 22px; line-height: 1.3; margin: 16px 0;">
    <a href="{post_url}" style="color: #6366f1; text-decoration: none;">{title}</a>
  </h1>

  <p style="color: #555; line-height: 1.6;">{excerpt}</p>

  <p style="margin: 24px 0;">
    <a href="{post_url}" style="display: inline-block; background: #6366f1; color: white; padding: 12px 24px; border-radius: 6px; text-decoration: none; font-weight: 600;">Read the full article</a>
  </p>

  <hr style="border: none; border-top: 1px solid #e5e5e5; margin: 32px 0;">
  <p style="font-size: 12px; color: #999;">
    You're receiving this because you subscribed to {site_config.get("site_name", "our")} updates.<br>
    <a href="{unsubscribe_url}" style="color: #999;">Unsubscribe</a>
  </p>
</body>
</html>"""


async def _send_via_resend(cfg: dict, to_email: str, subject: str, html: str) -> bool:
    """Send a single email via Resend API."""
    try:
        import resend

        resend.api_key = cfg["resend_api_key"]
        # Run in executor since resend SDK is sync
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None,
            lambda: resend.Emails.send({
                "from": f"{cfg['from_name']} <{cfg['from_email']}>",
                "to": [to_email],
                "subject": subject,
                "html": html,
            }),
        )
        return bool(result and result.get("id"))
    except Exception as e:
        logger.warning("[NEWSLETTER] Resend send failed for %s: %s", to_email, e)
        return False


async def _send_via_smtp(cfg: dict, to_email: str, subject: str, html: str) -> bool:
    """Send a single email via SMTP."""
    try:
        from email.mime.multipart import MIMEMultipart
        from email.mime.text import MIMEText

        import aiosmtplib

        msg = MIMEMultipart("alternative")
        msg["From"] = f"{cfg['from_name']} <{cfg['from_email']}>"
        msg["To"] = to_email
        msg["Subject"] = subject
        msg["List-Unsubscribe"] = f"<{SITE_URL}/newsletter/unsubscribe>"
        msg.attach(MIMEText(html, "html"))

        await aiosmtplib.send(
            msg,
            hostname=cfg["smtp_host"],
            port=cfg["smtp_port"],
            username=cfg["smtp_user"] or None,
            password=cfg["smtp_password"] or None,
            use_tls=cfg["smtp_use_tls"],
        )
        return True
    except Exception as e:
        logger.warning("[NEWSLETTER] SMTP send failed for %s: %s", to_email, e)
        return False


async def _log_send(pool, subscriber_id: int, subject: str, status: str, error: str = None) -> None:
    """Log send attempt to campaign_email_logs."""
    try:
        await pool.execute(
            """INSERT INTO campaign_email_logs
               (subscriber_id, campaign_name, email_subject, delivery_status, delivery_error)
               VALUES ($1, $2, $3, $4, $5)""",
            subscriber_id, "post_published", subject, status, error,
        )
    except Exception as e:
        logger.debug("[NEWSLETTER] Failed to log send: %s", e)


async def send_post_newsletter(
    pool,
    title: str,
    excerpt: str,
    slug: str,
) -> dict:
    """Send newsletter to all active subscribers about a new post.

    Args:
        pool: asyncpg connection pool
        title: post title
        excerpt: post excerpt/description
        slug: post URL slug

    Returns:
        dict with sent, failed, skipped counts
    """
    cfg = _cfg()
    result = {"sent": 0, "failed": 0, "skipped": 0, "total_subscribers": 0}

    if not cfg["enabled"]:
        logger.info("[NEWSLETTER] Disabled via app_settings (newsletter_enabled=false)")
        result["skipped_reason"] = "disabled"
        return result

    provider = cfg["provider"]
    if provider == "resend" and not cfg["resend_api_key"]:
        logger.warning("[NEWSLETTER] Resend selected but no API key configured")
        result["skipped_reason"] = "no_api_key"
        return result
    if provider == "smtp" and not cfg["smtp_host"]:
        logger.warning("[NEWSLETTER] SMTP selected but no host configured")
        result["skipped_reason"] = "no_smtp_host"
        return result

    send_fn = _send_via_resend if provider == "resend" else _send_via_smtp

    subscribers = await _get_active_subscribers(pool)
    result["total_subscribers"] = len(subscribers)

    if not subscribers:
        logger.info("[NEWSLETTER] No active subscribers to notify")
        return result

    subject = title
    batch_size = cfg["batch_size"]
    batch_delay = cfg["batch_delay"]

    logger.info(
        "[NEWSLETTER] Sending to %d subscribers via %s (batch=%d, delay=%ds)",
        len(subscribers), provider, batch_size, batch_delay,
    )

    for i in range(0, len(subscribers), batch_size):
        batch = subscribers[i : i + batch_size]

        for sub in batch:
            html = _build_html(title, excerpt, slug, sub.get("first_name"))
            success = await send_fn(cfg, sub["email"], subject, html)

            if success:
                result["sent"] += 1
                await _log_send(pool, sub["id"], subject, "delivered")
            else:
                result["failed"] += 1
                await _log_send(pool, sub["id"], subject, "failed", "send_error")

        # Respect rate limits between batches
        if i + batch_size < len(subscribers):
            await asyncio.sleep(batch_delay)

    logger.info(
        "[NEWSLETTER] Done: %d sent, %d failed, %d total",
        result["sent"], result["failed"], result["total_subscribers"],
    )
    return result
