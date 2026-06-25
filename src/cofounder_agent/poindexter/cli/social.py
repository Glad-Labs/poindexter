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
def list_drafts(
    post_id: str | None, task_id: str | None, status: str | None
) -> None:
    """List social post drafts."""
    try:
        drafts = run_service(lambda p: _svc.list_drafts(post_id, task_id, status, p))
    except Exception as exc:
        click.echo(f"Error: {exc}", err=True)
        sys.exit(1)
    if not drafts:
        click.echo("No drafts found.")
        return
    for d in drafts:
        subreddit = d.platform_config.get("subreddit", "")
        label = f"{d.platform}:{subreddit}" if subreddit else d.platform
        click.echo(
            f"[{d.status.upper():8}] {d.id[:8]}  {label:28}  {d.content[:55]}"
        )


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
        "       poindexter settings set social_drafts_enabled true\n"
    )
