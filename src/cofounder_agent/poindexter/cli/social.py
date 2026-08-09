"""CLI surface for social post draft management.

All commands delegate to SocialDraftsService — no SQL or business logic here.
"""
from __future__ import annotations

import sys
from typing import Any

import click

from poindexter.cli._dataplane import run_service
from services.site_config import SiteConfig
from services.social_drafts import SocialDraftsService

_svc = SocialDraftsService()


async def _with_site_config(pool: Any) -> SiteConfig:
    sc = SiteConfig(pool=pool)
    await sc.load(pool)
    return sc


@click.group(name="social")
def social_group() -> None:
    """Manage social post drafts (Postiz distribution)."""


@social_group.command("list")
@click.option("--post-id", default=None, help="Filter by blog post UUID")
@click.option("--task-id", default=None, help="Filter by pipeline task UUID")
@click.option(
    "--status",
    default=None,
    help="Filter by status (pending/approved/posted/failed/rejected)",
)
@click.option(
    "--limit", default=50, show_default=True, type=int, help="Max drafts to show"
)
@click.option("--offset", default=0, type=int, help="Drafts to skip")
def list_drafts(
    post_id: str | None,
    task_id: str | None,
    status: str | None,
    limit: int,
    offset: int,
) -> None:
    """List social post drafts."""
    try:
        page = run_service(
            lambda p: _svc.list_drafts(
                post_id, task_id, status, p, limit=limit, offset=offset
            )
        )
    except Exception as exc:
        click.echo(f"Error: {exc}", err=True)
        sys.exit(1)
    if not page.rows:
        click.echo("No drafts found.")
        return
    for d in page.rows:
        subreddit = d.platform_config.get("subreddit", "")
        label = f"{d.platform}:{subreddit}" if subreddit else d.platform
        click.echo(
            f"[{d.status.upper():8}] {d.id[:8]}  {label:28}  {d.content[:55]}"
        )
    # Never let the cap read as "that's all of them" — pending/failed sort
    # first, so what's withheld is always posted/rejected history.
    shown = offset + len(page.rows)
    if shown < page.total:
        click.echo(
            f"\nShowing {offset + 1}-{shown} of {page.total} "
            f"(--offset {shown} for more)."
        )
    breakdown = ", ".join(
        f"{s}={n}" for s, n in sorted(page.status_counts.items())
    )
    if breakdown:
        click.echo(f"Totals: {breakdown}")


@social_group.command("approve")
@click.argument("draft_id")
def approve_draft(draft_id: str) -> None:
    """Approve a draft and post it via Postiz immediately."""
    try:
        async def _impl(pool: Any) -> dict:
            sc = await _with_site_config(pool)
            return await _svc.approve_draft(draft_id, pool, sc)

        result = run_service(_impl)
    except Exception as exc:
        click.echo(f"Error: {exc}", err=True)
        sys.exit(1)
    if result.get("success"):
        click.echo(f"Posted — Postiz ID: {result.get('postiz_post_id')}")
    else:
        click.echo(f"Failed: {result.get('error')}", err=True)
        sys.exit(1)


@social_group.command("schedule")
@click.argument("draft_id")
@click.argument("when")
@click.option(
    "--force",
    is_flag=True,
    help="Allow a time in the past (fires on the next sweep)",
)
def schedule_draft(draft_id: str, when: str, force: bool) -> None:
    """Queue a draft to post at WHEN instead of immediately.

    WHEN accepts 'tomorrow 9am', 'next monday 14:00', or ISO 8601
    ('2026-08-09 09:00'), read in your operator_timezone.

    The promoted post does not have to be live yet — the publish gate is
    re-checked when the slot arrives, so a post that hasn't gone out by then
    just holds its place instead of promoting a dead link.
    """
    try:
        async def _impl(pool: Any) -> dict:
            sc = await _with_site_config(pool)
            return await _svc.schedule_draft(
                draft_id, when, pool, sc, force=force
            )

        result = run_service(_impl)
    except Exception as exc:
        click.echo(f"Error: {exc}", err=True)
        sys.exit(1)
    if not result.get("success"):
        click.echo(f"Failed: {result.get('error')}", err=True)
        sys.exit(1)
    click.echo(
        f"Draft {draft_id[:8]} scheduled for {result['scheduled_at']} (UTC)."
    )


@social_group.command("unschedule")
@click.argument("draft_id")
def unschedule_draft(draft_id: str) -> None:
    """Pull a draft back out of the queue (returns it to pending)."""
    try:
        result = run_service(lambda p: _svc.unschedule_draft(draft_id, p))
    except Exception as exc:
        click.echo(f"Error: {exc}", err=True)
        sys.exit(1)
    if not result.get("success"):
        click.echo(f"Failed: {result.get('error')}", err=True)
        sys.exit(1)
    click.echo(f"Draft {draft_id[:8]} unscheduled — back to pending.")


@social_group.command("queue")
@click.option(
    "--limit", default=50, show_default=True, type=int, help="Max drafts to show"
)
def show_queue(limit: int) -> None:
    """Show the upcoming social post queue, soonest first."""
    try:
        async def _impl(pool: Any) -> tuple:
            sc = await _with_site_config(pool)
            page = await _svc.list_drafts(
                None, None, "scheduled", pool,
                limit=limit, order_by_schedule=True,
            )
            return page, sc.timezone

        page, tz = run_service(_impl)
    except Exception as exc:
        click.echo(f"Error: {exc}", err=True)
        sys.exit(1)
    if not page.rows:
        click.echo("Queue is empty — nothing scheduled.")
        return
    for d in page.rows:
        subreddit = d.platform_config.get("subreddit", "")
        label = f"{d.platform}:{subreddit}" if subreddit else d.platform
        when = (
            d.scheduled_at.astimezone(tz).strftime("%Y-%m-%d %H:%M %Z")
            if d.scheduled_at
            else "unscheduled"
        )
        # A queued promo whose post isn't published yet will hold at its slot
        # rather than post, so flag it here instead of at fire time.
        flag = "" if d.post_status == "published" else f"  [post={d.post_status}]"
        click.echo(f"{when}  {d.id[:8]}  {label:28}  {d.title or ''}{flag}")
    if page.total > len(page.rows):
        click.echo(f"\nShowing {len(page.rows)} of {page.total} scheduled.")


@social_group.command("reject")
@click.argument("draft_id")
def reject_draft(draft_id: str) -> None:
    """Reject a draft (terminal — no retry)."""
    try:
        run_service(lambda p: _svc.reject_draft(draft_id, p))
    except Exception as exc:
        click.echo(f"Error: {exc}", err=True)
        sys.exit(1)
    click.echo(f"Draft {draft_id[:8]} rejected.")


@social_group.command("edit")
@click.argument("draft_id")
@click.option("--content", required=True, help="New post copy")
def edit_draft(draft_id: str, content: str) -> None:
    """Edit draft copy before approving."""
    try:
        run_service(lambda p: _svc.edit_draft(draft_id, content, None, p))
    except Exception as exc:
        click.echo(f"Error: {exc}", err=True)
        sys.exit(1)
    click.echo(f"Draft {draft_id[:8]} updated.")


@social_group.command("retry")
@click.argument("draft_id")
def retry_draft(draft_id: str) -> None:
    """Retry a failed draft."""
    try:
        async def _impl(pool: Any) -> dict:
            sc = await _with_site_config(pool)
            return await _svc.retry_draft(draft_id, pool, sc)

        result = run_service(_impl)
    except Exception as exc:
        click.echo(f"Error: {exc}", err=True)
        sys.exit(1)
    if result.get("success"):
        click.echo(f"Retried and posted — Postiz ID: {result.get('postiz_post_id')}")
    else:
        click.echo(f"Retry failed: {result.get('error')}", err=True)
        sys.exit(1)


@social_group.command("backfill")
@click.option(
    "--lookback-days",
    default=14,
    type=int,
    help="Only consider posts published within this many days",
)
def backfill_drafts(lookback_days: int) -> None:
    """Regenerate social drafts for published posts with incomplete coverage.

    Safe to run any time: delegates to the same reconciliation the
    BackfillMissingSocialDraftsJob runs on a schedule, so a post with full
    draft coverage already is a cheap no-op (no LLM calls).
    """
    try:
        async def _impl(pool: Any) -> dict:
            sc = await _with_site_config(pool)
            return await _svc.reconcile_missing_drafts(pool, sc, lookback_days)

        result = run_service(_impl)
    except Exception as exc:
        click.echo(f"Error: {exc}", err=True)
        sys.exit(1)
    click.echo(
        f"Checked {result['candidates_checked']} post(s), "
        f"created {result['drafts_created']} draft(s)."
    )
    for err in result["errors"]:
        click.echo(f"  ERROR: {err}", err=True)


@social_group.command("setup")
def setup() -> None:
    """Guide through Postiz integration UUID setup."""
    click.echo(
        "Postiz Social Setup\n"
        "===================\n"
        "Prerequisites:\n"
        "  1. Ensure Postiz is running:\n"
        "       docker compose up -d postiz postiz-redis\n"
        "  2. Create the Postiz database (first time only):\n"
        "       docker exec -it postgres psql -U postgres -c 'CREATE DATABASE postiz;'\n\n"
        "Account setup:\n"
        "  3. Open http://localhost:3003 in your browser\n"
        "  4. Connect each social account under Settings → Integrations\n"
        "  5. Copy the UUID for each connected account\n\n"
        "Configuration:\n"
        "  6. Set each integration UUID:\n"
        "       poindexter settings set postiz_integration_id_twitter  <uuid>\n"
        "       poindexter settings set postiz_integration_id_linkedin <uuid>\n"
        "       poindexter settings set postiz_integration_id_mastodon <uuid>\n"
        "       poindexter settings set postiz_integration_id_reddit   <uuid>\n"
        "       poindexter settings set postiz_integration_id_tiktok   <uuid>\n"
        "       poindexter settings set postiz_integration_id_instagram <uuid>\n\n"
        "  7. Set the platforms to generate drafts for:\n"
        "       poindexter settings set social_draft_platforms"
        " twitter,linkedin,mastodon,reddit\n\n"
        "  8. Set Reddit subreddits (one draft per subreddit):\n"
        "       poindexter settings set social_reddit_subreddits"
        " r/LocalLLaMA,r/ArtificialIntelligence,r/selfhosted,r/homelab,r/Python,"
        "r/opensource\n\n"
        "  9. Enable social drafts:\n"
        "       poindexter settings set social_drafts_enabled true\n\n"
        "Scheduling (optional — drafts post immediately on approve otherwise):\n"
        "  Time a single promo without touching the Postiz UI:\n"
        "       poindexter social schedule <draft-id> 'tomorrow 9am'\n"
        "       poindexter social queue\n\n"
        "  Or drip every promo automatically once its post goes live. Both\n"
        "  gates must be set — a platform named in neither map is never\n"
        "  auto-slotted:\n"
        "       poindexter settings set social_schedule_enabled true\n"
        "       poindexter settings set social_schedule_prime_times"
        " 'twitter=09:00,12:30,17:00; linkedin=08:00,12:00; reddit=09:00'\n\n"
        "  Prime times are the hours each channel is worth posting to, so a\n"
        "  post published at 11pm still promotes at 9am. Optionally add an\n"
        "  offset as a minimum delay before the first eligible hour:\n"
        "       poindexter settings set social_schedule_offsets linkedin=3h\n\n"
        "  Auto-drip posts LLM-written copy without per-draft review (the\n"
        "  post's own approval is the gate). Leave it off to read each one.\n"
    )
