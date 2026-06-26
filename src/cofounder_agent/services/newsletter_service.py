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
from typing import Any

from services.logger_config import get_logger
from services.site_config import SiteConfig

logger = get_logger(__name__)


def _site_url(*, site_config: SiteConfig) -> str:
    """Return the canonical site URL. Fails loud (RuntimeError) if the
    setting is missing — silently sending newsletters with broken links
    would be worse.

    DI (#272 Phase-2b): ``site_config`` is keyword-required. The module
    no longer carries a lifespan-bound global; the public entry point
    threads the injected instance down through every helper.
    """
    return site_config.require("site_url")


async def _cfg(*, site_config: SiteConfig) -> dict:
    """Load newsletter config from DB.

    ``smtp_password`` (and ``resend_api_key``) are ``is_secret=true`` rows
    after migration 0121 — they're filtered out of the in-memory cache,
    so they MUST be fetched via the async ``get_secret`` path. See
    Glad-Labs/poindexter#221 for the schema flip.

    DI (#272 Phase-2b): ``site_config`` is keyword-required.
    """
    _sc = site_config
    return {
        "enabled": _sc.get_bool("newsletter_enabled", False),
        "provider": _sc.get("newsletter_provider", "resend"),
        "from_email": _sc.get("newsletter_from_email", ""),
        "from_name": _sc.get("newsletter_from_name", ""),
        "resend_api_key": await _sc.get_secret("resend_api_key", ""),
        "smtp_host": _sc.get("smtp_host", ""),
        "smtp_port": _sc.get_int("smtp_port", 587),
        "smtp_user": _sc.get("smtp_user", ""),
        "smtp_password": await _sc.get_secret("smtp_password", ""),
        "smtp_use_tls": _sc.get_bool("smtp_use_tls", True),
        "batch_size": _sc.get_int("newsletter_batch_size", 50),
        "batch_delay": _sc.get_int("newsletter_batch_delay_seconds", 2),
    }


async def _get_active_subscribers(pool) -> list[dict]:
    """Fetch all active, verified subscribers.

    Includes ``unsubscribe_token`` because every email needs a
    per-subscriber unsubscribe URL post-#252 — the public endpoint
    refuses email-keyed unsubscribes and requires the token. Migration
    20260527_180559 guarantees the column is NOT NULL on every row.
    """
    rows = await pool.fetch(
        "SELECT id, email, first_name, unsubscribe_token FROM newsletter_subscribers "
        "WHERE unsubscribed_at IS NULL AND verified = TRUE "
        "ORDER BY id"
    )
    return [dict(r) for r in rows]


def _unsubscribe_url(token: str, *, site_config: SiteConfig) -> str:
    """Per-subscriber unsubscribe URL.

    Centralised so the email template and the ``List-Unsubscribe``
    header stay in sync — both consume the token, and they have to
    agree on shape or one-click clients (Gmail/Apple Mail) silently
    fall back to the inline link.

    DI (#272 Phase-2b): threads the keyword-required ``site_config``
    through to ``_site_url``.
    """
    return f"{_site_url(site_config=site_config)}/newsletter/unsubscribe?token={token}"


def _build_html(
    title: str,
    excerpt: str,
    slug: str,
    first_name: str | None = None,
    *,
    unsubscribe_token: str,
    site_config: SiteConfig,
) -> str:
    """Build a simple, clean newsletter email body.

    ``unsubscribe_token`` is keyword-only + required: the token has no
    sensible default and a typo'd positional call would silently ship
    emails with a broken unsubscribe link.

    DI (#272 Phase-2b): ``site_config`` is keyword-required (threaded to
    ``_site_url`` / ``_unsubscribe_url`` and used for the company /
    site-name reads).
    """
    _sc = site_config
    greeting = f"Hi {first_name}," if first_name else "Hi there,"
    post_url = f"{_site_url(site_config=_sc)}/posts/{slug}"
    unsubscribe_url = _unsubscribe_url(unsubscribe_token, site_config=_sc)

    return f"""\
<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width"></head>
<body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; max-width: 600px; margin: 0 auto; padding: 20px; color: #1a1a1a;">
  <div style="border-bottom: 2px solid #6366f1; padding-bottom: 16px; margin-bottom: 24px;">
    <h2 style="margin: 0; color: #6366f1;">{_sc.get("company_name", "")}</h2>
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
    You're receiving this because you subscribed to {_sc.get("site_name", "our")} updates.<br>
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
        loop = asyncio.get_running_loop()
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


async def _send_via_smtp(
    cfg: dict,
    to_email: str,
    subject: str,
    html: str,
    *,
    unsubscribe_token: str,
    site_config: SiteConfig,
) -> bool:
    """Send a single email via SMTP.

    ``unsubscribe_token`` is required so the ``List-Unsubscribe`` header
    carries a tokenized URL — RFC 8058 one-click clients (Gmail, Apple
    Mail) POST to that URL when the user clicks "unsubscribe" in their
    inbox UI. Without the token the one-click flow hits the public
    endpoint with no credential and fails the #252 gate, which would
    silently break inbox-native unsubscribe even after we fix the
    inline link.

    DI (#272 Phase-2b): ``site_config`` is keyword-required and threaded
    to the ``_unsubscribe_url`` call so the header URL resolves against
    the same config flowing through the rest of the send chain.
    """
    try:
        from email.mime.multipart import MIMEMultipart
        from email.mime.text import MIMEText

        import aiosmtplib

        msg = MIMEMultipart("alternative")
        msg["From"] = f"{cfg['from_name']} <{cfg['from_email']}>"
        msg["To"] = to_email
        msg["Subject"] = subject
        msg["List-Unsubscribe"] = f"<{_unsubscribe_url(unsubscribe_token, site_config=site_config)}>"
        msg["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"
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


async def _log_send(pool, subscriber_id: int, subject: str, status: str, error: str | None = None) -> None:
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
    *,
    site_config: SiteConfig,
) -> dict:
    """Send newsletter to all active subscribers about a new post.

    Args:
        pool: asyncpg connection pool
        title: post title
        excerpt: post excerpt/description
        slug: post URL slug
        site_config: the lifespan-bound SiteConfig (keyword-required as of
            #272 Phase-2b). Flows through the full send chain (_cfg /
            _build_html / _send_via_smtp). The ``publish_service`` caller
            passes its own lifespan-bound module ``site_config``.

    Returns:
        dict with sent, failed, skipped counts
    """
    cfg = await _cfg(site_config=site_config)
    result: dict[str, Any] = {"sent": 0, "failed": 0, "skipped": 0, "total_subscribers": 0}

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
            token = sub["unsubscribe_token"]
            html = _build_html(
                title, excerpt, slug, sub.get("first_name"),
                unsubscribe_token=token,
                site_config=site_config,
            )
            # Per-provider dispatch — SMTP also needs the token for the
            # ``List-Unsubscribe`` header (RFC 8058 one-click). Resend
            # only consumes the inline link inside ``html`` so the
            # token's already baked in there.
            if provider == "resend":
                success = await _send_via_resend(cfg, sub["email"], subject, html)
            else:
                success = await _send_via_smtp(
                    cfg, sub["email"], subject, html,
                    unsubscribe_token=token,
                    site_config=site_config,
                )

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


async def get_newsletter_stats(pool) -> dict:
    """Aggregate newsletter stats for the operator console / Grafana.

    Returns:
        subscriber_count: active verified subscribers
        unsubscribed_count: total who have unsubscribed
        last_30d: { sent, failed, total, delivery_rate, last_send_at }
        recent_campaigns: last 5 campaigns by (subject, date)
    """
    from datetime import timezone

    subscriber_count = await pool.fetchval(
        "SELECT COUNT(*) FROM newsletter_subscribers"
        " WHERE unsubscribed_at IS NULL AND verified = TRUE"
    )
    unsubscribed_count = await pool.fetchval(
        "SELECT COUNT(*) FROM newsletter_subscribers WHERE unsubscribed_at IS NOT NULL"
    )
    stats_30d = await pool.fetchrow(
        """
        SELECT
            COUNT(*) FILTER (WHERE delivery_status = 'delivered') AS sent,
            COUNT(*) FILTER (WHERE delivery_status = 'failed')    AS failed,
            COUNT(*)                                               AS total,
            MAX(sent_at)                                           AS last_send_at
        FROM campaign_email_logs
        WHERE sent_at > NOW() - INTERVAL '30 days'
        """
    )
    recent_rows = await pool.fetch(
        """
        SELECT
            email_subject                                              AS subject,
            DATE(sent_at)                                              AS campaign_date,
            COUNT(*) FILTER (WHERE delivery_status = 'delivered')     AS sent,
            COUNT(*) FILTER (WHERE delivery_status = 'failed')        AS failed,
            COUNT(*)                                                   AS total
        FROM campaign_email_logs
        GROUP BY email_subject, DATE(sent_at)
        ORDER BY campaign_date DESC
        LIMIT 5
        """
    )

    sent = int(stats_30d["sent"] or 0)
    failed = int(stats_30d["failed"] or 0)
    total = int(stats_30d["total"] or 0)
    delivery_rate = round(sent / total * 100, 1) if total > 0 else None
    raw_last = stats_30d["last_send_at"]
    if raw_last is not None and raw_last.tzinfo is None:
        raw_last = raw_last.replace(tzinfo=timezone.utc)
    last_send_at = raw_last.isoformat() if raw_last else None

    campaigns = [
        {
            "subject": r["subject"],
            "date": str(r["campaign_date"]),
            "sent": int(r["sent"] or 0),
            "failed": int(r["failed"] or 0),
            "total": int(r["total"] or 0),
        }
        for r in recent_rows
    ]

    return {
        "subscriber_count": int(subscriber_count or 0),
        "unsubscribed_count": int(unsubscribed_count or 0),
        "last_30d": {
            "sent": sent,
            "failed": failed,
            "total": total,
            "delivery_rate": delivery_rate,
            "last_send_at": last_send_at,
        },
        "recent_campaigns": campaigns,
    }
