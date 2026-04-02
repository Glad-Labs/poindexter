"""
Business Report Generator

Generates weekly and daily summary reports by querying the production
PostgreSQL database directly. Output formats:

- Plain text for Telegram delivery
- Discord embed-style structured message

Covers:
  1. Content metrics (posts published, total posts, avg quality score)
  2. Pipeline metrics (tasks created, completed, failed, rejected)
  3. Cost metrics (cloud API spend from cost_logs, electricity estimate)
  4. Site health (uptime checks, alerts)
  5. SEO (sitemap URLs, categories, tags)

Usage:
    from services.business_report import generate_weekly_report, generate_daily_summary

    text = await generate_weekly_report(pool)
    daily = await generate_daily_summary(pool)
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import os

from asyncpg import Pool

from services.logger_config import get_logger
from services.site_config import site_config

logger = get_logger(__name__)


# ============================================================================
# Data structures
# ============================================================================


@dataclass
class ContentMetrics:
    posts_published_this_week: int = 0
    total_posts: int = 0
    total_published: int = 0
    avg_quality_score: Optional[float] = None
    recent_titles: List[str] = field(default_factory=list)


@dataclass
class PipelineMetrics:
    tasks_created: int = 0
    tasks_completed: int = 0
    tasks_failed: int = 0
    tasks_rejected: int = 0
    tasks_in_progress: int = 0
    tasks_pending: int = 0


@dataclass
class CostMetrics:
    cloud_api_spend: float = 0.0
    electricity_rate_kwh: Optional[float] = None
    daily_budget_usd: Optional[float] = None
    top_providers: Dict[str, float] = field(default_factory=dict)
    total_tokens: int = 0


@dataclass
class SiteHealthMetrics:
    uptime_checks: int = 0
    alerts_fired: int = 0


@dataclass
class SEOMetrics:
    total_sitemap_urls: int = 0
    categories_count: int = 0
    tags_count: int = 0


@dataclass
class BusinessReport:
    period_start: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    period_end: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    content: ContentMetrics = field(default_factory=ContentMetrics)
    pipeline: PipelineMetrics = field(default_factory=PipelineMetrics)
    costs: CostMetrics = field(default_factory=CostMetrics)
    health: SiteHealthMetrics = field(default_factory=SiteHealthMetrics)
    seo: SEOMetrics = field(default_factory=SEOMetrics)


# ============================================================================
# Query helpers
# ============================================================================


async def _fetch_content_metrics(
    conn, period_start: datetime, period_end: datetime
) -> ContentMetrics:
    """Query posts and content_tasks for content metrics."""
    metrics = ContentMetrics()

    try:
        # Total posts and published count
        totals = await conn.fetchrow(
            """
            SELECT
                COUNT(*) AS total,
                COUNT(*) FILTER (WHERE status = 'published') AS published
            FROM posts
            """
        )
        metrics.total_posts = totals["total"] if totals else 0
        metrics.total_published = totals["published"] if totals else 0

        # Posts published this week
        week_row = await conn.fetchrow(
            """
            SELECT COUNT(*) AS cnt
            FROM posts
            WHERE status = 'published'
              AND published_at >= $1
              AND published_at < $2
            """,
            period_start,
            period_end,
        )
        metrics.posts_published_this_week = week_row["cnt"] if week_row else 0

        # Recent post titles (up to 5)
        recent = await conn.fetch(
            """
            SELECT title
            FROM posts
            WHERE status = 'published'
              AND published_at >= $1
              AND published_at < $2
            ORDER BY published_at DESC
            LIMIT 5
            """,
            period_start,
            period_end,
        )
        metrics.recent_titles = [r["title"] for r in recent]

        # Average quality score from content_tasks (non-null scores only)
        quality = await conn.fetchrow(
            """
            SELECT AVG(quality_score)::FLOAT AS avg_score
            FROM content_tasks
            WHERE quality_score IS NOT NULL
              AND created_at >= $1
              AND created_at < $2
            """,
            period_start,
            period_end,
        )
        if quality and quality["avg_score"] is not None:
            metrics.avg_quality_score = round(quality["avg_score"], 1)

    except Exception as e:
        logger.error("[fetch_content_metrics] Error querying content metrics", exc_info=True)

    return metrics


async def _fetch_pipeline_metrics(
    conn, period_start: datetime, period_end: datetime
) -> PipelineMetrics:
    """Query content_tasks for pipeline throughput metrics."""
    metrics = PipelineMetrics()

    try:
        row = await conn.fetchrow(
            """
            SELECT
                COUNT(*) AS created,
                COUNT(*) FILTER (WHERE status = 'completed') AS completed,
                COUNT(*) FILTER (WHERE status = 'failed') AS failed,
                COUNT(*) FILTER (WHERE status = 'rejected') AS rejected,
                COUNT(*) FILTER (WHERE status = 'in_progress') AS in_progress,
                COUNT(*) FILTER (WHERE status = 'pending') AS pending
            FROM content_tasks
            WHERE created_at >= $1
              AND created_at < $2
            """,
            period_start,
            period_end,
        )
        if row:
            metrics.tasks_created = row["created"]
            metrics.tasks_completed = row["completed"]
            metrics.tasks_failed = row["failed"]
            metrics.tasks_rejected = row["rejected"]
            metrics.tasks_in_progress = row["in_progress"]
            metrics.tasks_pending = row["pending"]

    except Exception as e:
        logger.error("[fetch_pipeline_metrics] Error querying pipeline metrics", exc_info=True)

    return metrics


async def _fetch_cost_metrics(
    conn, period_start: datetime, period_end: datetime
) -> CostMetrics:
    """Query cost_logs and app_settings for spend metrics."""
    metrics = CostMetrics()

    try:
        # Total cloud API spend and token usage
        spend = await conn.fetchrow(
            """
            SELECT
                COALESCE(SUM(cost_usd), 0)::FLOAT AS total_spend,
                COALESCE(SUM(total_tokens), 0) AS total_tokens
            FROM cost_logs
            WHERE created_at >= $1
              AND created_at < $2
              AND success = true
            """,
            period_start,
            period_end,
        )
        if spend:
            metrics.cloud_api_spend = round(spend["total_spend"], 6)
            metrics.total_tokens = spend["total_tokens"]

        # Spend by provider
        providers = await conn.fetch(
            """
            SELECT provider, COALESCE(SUM(cost_usd), 0)::FLOAT AS spend
            FROM cost_logs
            WHERE created_at >= $1
              AND created_at < $2
              AND success = true
            GROUP BY provider
            ORDER BY spend DESC
            """,
            period_start,
            period_end,
        )
        metrics.top_providers = {r["provider"]: round(r["spend"], 6) for r in providers}

        # Settings: electricity rate and daily budget
        settings = await conn.fetch(
            """
            SELECT key, value
            FROM app_settings
            WHERE key IN ('electricity_rate_kwh', 'daily_budget_usd')
            """
        )
        for s in settings:
            try:
                if s["key"] == "electricity_rate_kwh" and s["value"]:
                    metrics.electricity_rate_kwh = float(s["value"])
                elif s["key"] == "daily_budget_usd" and s["value"]:
                    metrics.daily_budget_usd = float(s["value"])
            except (ValueError, TypeError):
                pass

    except Exception as e:
        logger.error("[fetch_cost_metrics] Error querying cost metrics", exc_info=True)

    return metrics


async def _fetch_health_metrics(
    conn, period_start: datetime, period_end: datetime
) -> SiteHealthMetrics:
    """Check for health-related data in cost_logs (uptime proxy)."""
    metrics = SiteHealthMetrics()

    try:
        # Use successful cost_log entries as a proxy for uptime checks
        # (each successful API call implies the system was operational)
        row = await conn.fetchrow(
            """
            SELECT
                COUNT(*) FILTER (WHERE success = true) AS checks,
                COUNT(*) FILTER (WHERE success = false) AS alerts
            FROM cost_logs
            WHERE created_at >= $1
              AND created_at < $2
            """,
            period_start,
            period_end,
        )
        if row:
            metrics.uptime_checks = row["checks"]
            metrics.alerts_fired = row["alerts"]

    except Exception as e:
        logger.error("[fetch_health_metrics] Error querying health metrics", exc_info=True)

    return metrics


async def _fetch_seo_metrics(conn) -> SEOMetrics:
    """Query posts, categories, and tags for SEO overview."""
    metrics = SEOMetrics()

    try:
        row = await conn.fetchrow(
            """
            SELECT
                (SELECT COUNT(*) FROM posts WHERE status = 'published') AS sitemap_urls,
                (SELECT COUNT(*) FROM categories) AS cat_count,
                (SELECT COUNT(*) FROM tags) AS tag_count
            """
        )
        if row:
            metrics.total_sitemap_urls = row["sitemap_urls"]
            metrics.categories_count = row["cat_count"]
            metrics.tags_count = row["tag_count"]

    except Exception as e:
        logger.error("[fetch_seo_metrics] Error querying SEO metrics", exc_info=True)

    return metrics


# ============================================================================
# Report assembly
# ============================================================================


async def _build_report(pool: Pool, period_start: datetime, period_end: datetime) -> BusinessReport:
    """Assemble a full BusinessReport for the given time window."""
    report = BusinessReport(period_start=period_start, period_end=period_end)

    try:
        async with pool.acquire() as conn:
            report.content = await _fetch_content_metrics(conn, period_start, period_end)
            report.pipeline = await _fetch_pipeline_metrics(conn, period_start, period_end)
            report.costs = await _fetch_cost_metrics(conn, period_start, period_end)
            report.health = await _fetch_health_metrics(conn, period_start, period_end)
            report.seo = await _fetch_seo_metrics(conn)
    except Exception as e:
        logger.error("[build_report] Error assembling report", exc_info=True)

    return report


# ============================================================================
# Formatters
# ============================================================================


def _format_usd(amount: float) -> str:
    """Format a USD amount with appropriate precision."""
    if amount < 0.01:
        return f"${amount:.4f}"
    return f"${amount:.2f}"


def _completion_rate(pipeline: PipelineMetrics) -> str:
    """Calculate completion rate as a percentage string."""
    if pipeline.tasks_created == 0:
        return "N/A"
    rate = (pipeline.tasks_completed / pipeline.tasks_created) * 100
    return f"{rate:.0f}%"


def format_telegram(report: BusinessReport) -> str:
    """Format report as clean text suitable for Telegram."""
    p = report.pipeline
    c = report.content
    co = report.costs
    h = report.health
    s = report.seo

    start_str = report.period_start.strftime("%b %d")
    end_str = report.period_end.strftime("%b %d, %Y")

    lines = [
        f"GLAD LABS WEEKLY REPORT",
        f"{start_str} - {end_str}",
        "",
        "--- CONTENT ---",
        f"Posts published this week: {c.posts_published_this_week}",
        f"Total published posts: {c.total_published}",
        f"Avg quality score: {c.avg_quality_score or 'N/A'}",
    ]

    if c.recent_titles:
        lines.append("Recent posts:")
        for title in c.recent_titles:
            lines.append(f"  - {title}")

    lines.extend([
        "",
        "--- PIPELINE ---",
        f"Tasks created: {p.tasks_created}",
        f"Completed: {p.tasks_completed}",
        f"Failed: {p.tasks_failed}",
        f"Rejected: {p.tasks_rejected}",
        f"In progress: {p.tasks_in_progress}",
        f"Pending: {p.tasks_pending}",
        f"Completion rate: {_completion_rate(p)}",
    ])

    lines.extend([
        "",
        "--- COSTS ---",
        f"Cloud API spend: {_format_usd(co.cloud_api_spend)}",
        f"Total tokens used: {co.total_tokens:,}",
    ])

    if co.top_providers:
        lines.append("By provider:")
        for provider, spend in co.top_providers.items():
            lines.append(f"  {provider}: {_format_usd(spend)}")

    if co.daily_budget_usd:
        lines.append(f"Daily budget: {_format_usd(co.daily_budget_usd)}")

    if co.electricity_rate_kwh is not None:
        lines.append(f"Electricity rate: ${co.electricity_rate_kwh:.3f}/kWh")

    lines.extend([
        "",
        "--- SITE HEALTH ---",
        f"Successful API calls: {h.uptime_checks}",
        f"Failed API calls: {h.alerts_fired}",
    ])

    lines.extend([
        "",
        "--- SEO ---",
        f"Sitemap URLs: {s.total_sitemap_urls}",
        f"Categories: {s.categories_count}",
        f"Tags: {s.tags_count}",
    ])

    return "\n".join(lines)


def format_telegram_daily(report: BusinessReport) -> str:
    """Format a shorter daily summary for Telegram."""
    p = report.pipeline
    c = report.content
    co = report.costs

    date_str = report.period_start.strftime("%b %d, %Y")

    lines = [
        f"GLAD LABS DAILY SUMMARY - {date_str}",
        "",
        f"Posts published: {c.posts_published_this_week}",
        f"Tasks: {p.tasks_completed} done / {p.tasks_failed} failed / {p.tasks_pending} pending",
        f"API spend: {_format_usd(co.cloud_api_spend)} ({co.total_tokens:,} tokens)",
    ]

    if c.recent_titles:
        lines.append("New posts:")
        for title in c.recent_titles:
            lines.append(f"  - {title}")

    if p.tasks_failed > 0:
        lines.append(f"[!] {p.tasks_failed} task(s) failed today")

    return "\n".join(lines)


def format_discord_embed(report: BusinessReport) -> Dict[str, Any]:
    """Format report as a Discord embed-style structured message.

    Returns a dict matching Discord's embed object schema, suitable for
    sending via a Discord webhook or bot API.
    """
    p = report.pipeline
    c = report.content
    co = report.costs
    h = report.health
    s = report.seo

    start_str = report.period_start.strftime("%b %d")
    end_str = report.period_end.strftime("%b %d, %Y")

    # Build provider breakdown string
    provider_lines = []
    for provider, spend in co.top_providers.items():
        provider_lines.append(f"{provider}: {_format_usd(spend)}")
    provider_str = "\n".join(provider_lines) if provider_lines else "No API calls"

    # Recent titles string
    titles_str = ""
    if c.recent_titles:
        titles_str = "\n".join(f"- {t}" for t in c.recent_titles)

    fields = [
        {
            "name": "Content",
            "value": (
                f"Published this week: **{c.posts_published_this_week}**\n"
                f"Total published: **{c.total_published}**\n"
                f"Avg quality: **{c.avg_quality_score or 'N/A'}**"
            ),
            "inline": True,
        },
        {
            "name": "Pipeline",
            "value": (
                f"Created: **{p.tasks_created}**\n"
                f"Completed: **{p.tasks_completed}**\n"
                f"Failed: **{p.tasks_failed}**\n"
                f"Rate: **{_completion_rate(p)}**"
            ),
            "inline": True,
        },
        {
            "name": "Costs",
            "value": (
                f"API spend: **{_format_usd(co.cloud_api_spend)}**\n"
                f"Tokens: **{co.total_tokens:,}**\n"
                f"{provider_str}"
            ),
            "inline": True,
        },
        {
            "name": "Site Health",
            "value": (
                f"Successful calls: **{h.uptime_checks}**\n"
                f"Failures: **{h.alerts_fired}**"
            ),
            "inline": True,
        },
        {
            "name": "SEO",
            "value": (
                f"Sitemap URLs: **{s.total_sitemap_urls}**\n"
                f"Categories: **{s.categories_count}**\n"
                f"Tags: **{s.tags_count}**"
            ),
            "inline": True,
        },
    ]

    if titles_str:
        fields.append({
            "name": "Recent Posts",
            "value": titles_str,
            "inline": False,
        })

    embed = {
        "title": f"{site_config.get('site_name', 'AI Pipeline')} Weekly Report",
        "description": f"{start_str} - {end_str}",
        "color": 0x2ECC71,  # Green
        "fields": fields,
        "footer": {"text": f"{site_config.get('site_name', 'AI Pipeline')} Content Engine"},
        "timestamp": report.period_end.isoformat(),
    }

    return embed


# ============================================================================
# Public API
# ============================================================================


async def generate_weekly_report(pool: Pool) -> str:
    """Generate a weekly business report formatted for Telegram.

    Args:
        pool: asyncpg connection pool

    Returns:
        Plain text report covering the last 7 days
    """
    now = datetime.now(timezone.utc)
    period_end = now
    period_start = now - timedelta(days=7)

    logger.info("[generate_weekly_report] Building report for %s to %s", period_start, period_end)

    report = await _build_report(pool, period_start, period_end)
    text = format_telegram(report)

    logger.info("[generate_weekly_report] Report generated successfully")
    return text


async def generate_weekly_report_discord(pool: Pool) -> Dict[str, Any]:
    """Generate a weekly business report formatted as a Discord embed.

    Args:
        pool: asyncpg connection pool

    Returns:
        Discord embed dict covering the last 7 days
    """
    now = datetime.now(timezone.utc)
    period_end = now
    period_start = now - timedelta(days=7)

    logger.info("[generate_weekly_report_discord] Building report for %s to %s", period_start, period_end)

    report = await _build_report(pool, period_start, period_end)
    embed = format_discord_embed(report)

    logger.info("[generate_weekly_report_discord] Report generated successfully")
    return embed


async def generate_daily_summary(pool: Pool) -> str:
    """Generate a shorter daily summary formatted for Telegram.

    Args:
        pool: asyncpg connection pool

    Returns:
        Plain text daily summary covering today
    """
    now = datetime.now(timezone.utc)
    period_end = now
    period_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    logger.info("[generate_daily_summary] Building summary for %s", period_start)

    report = await _build_report(pool, period_start, period_end)
    text = format_telegram_daily(report)

    logger.info("[generate_daily_summary] Summary generated successfully")
    return text
