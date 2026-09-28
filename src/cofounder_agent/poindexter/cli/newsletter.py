"""``poindexter newsletter`` — operate the newsletter signup path.

Thin adapter over ``services.newsletter_audience`` and
``services.newsletter_signup_canary`` per the transport-adapter contract:
every command delegates to a service function and holds no SQL or business
logic of its own. The scheduled jobs run the same functions; these commands
exist for an immediate run and a dry-run preview.

Subcommands:

- ``sync`` — pull the Resend segment into ``newsletter_subscribers`` now
  (``SyncNewsletterAudienceJob`` does this every 15 minutes). ``--dry-run``
  reports what would change and writes nothing; use it before a backfill.
- ``canary`` — sign a Resend test inbox up through the public signup
  endpoint and confirm it reaches the segment the worker syncs. Sends one
  welcome email to the canary address. ``--url`` runs it once against an
  endpoint without switching the daily canary on.
"""

from __future__ import annotations

import asyncio
import json
import sys
from typing import Any

import click

from poindexter.cli._bootstrap import cli_site_config, close_cli_pool, open_cli_pool


async def _open_ctx() -> tuple[Any, Any]:
    """Pool + a loaded SiteConfig (pool-backed so get_secret works)."""
    pool = await open_cli_pool()
    site_config = cli_site_config(pool)
    await site_config.load(pool)
    return pool, site_config


@click.group(
    "newsletter",
    help=(
        "Operate the newsletter signup path: the public form captures into a "
        "Resend segment, and the worker pulls it into newsletter_subscribers. "
        "The scheduled jobs handle the steady state; these commands run them "
        "now."
    ),
)
def newsletter_group() -> None:
    """newsletter command group."""


@newsletter_group.command("sync")
@click.option(
    "--dry-run", is_flag=True,
    help="Report what the pull would change without writing anything.",
)
@click.option("--json", "as_json", is_flag=True, help="Emit JSON for LLM/script consumers.")
def cmd_sync(dry_run: bool, as_json: bool) -> None:
    """Pull the Resend signup segment into newsletter_subscribers now."""
    asyncio.run(_run_sync(dry_run, as_json))


async def _run_sync(dry_run: bool, as_json: bool) -> None:
    pool, site_config = await _open_ctx()
    try:
        from poindexter.services.newsletter_audience import (
            AudienceConfigError,
            sync_segment_to_subscribers,
        )

        try:
            outcome = await sync_segment_to_subscribers(
                pool, site_config, dry_run=dry_run
            )
        except AudienceConfigError as exc:
            raise click.ClickException(str(exc)) from exc
    finally:
        await close_cli_pool(pool)

    if outcome.segment_id is None:
        raise click.ClickException(
            "resend_audience_id is not set, so there is no segment to pull. Set "
            "it to the Resend segment the site's RESEND_AUDIENCE_ID writes into."
        )
    if as_json:
        click.echo(json.dumps(
            {"segment_id": outcome.segment_id, **outcome.as_metrics(),
             "error_detail": outcome.errors},
            indent=2,
        ))
    else:
        prefix = "[dry run] " if dry_run else ""
        click.echo(f"{prefix}segment {outcome.segment_id}: {outcome.summary()}")
        skipped = {
            "unsubscribed in Resend, not on the list": outcome.skipped_unsubscribed,
            "already unsubscribed": outcome.already_unsubscribed,
            "invalid address": outcome.skipped_invalid,
            "signup canary": outcome.skipped_canary,
        }
        for label, count in skipped.items():
            if count:
                click.echo(f"  skipped {count}: {label}")
        for err in outcome.errors:
            click.echo(f"ERROR: {err}", err=True)
    if outcome.errors:
        sys.exit(1)


@newsletter_group.command("canary")
@click.option(
    "--url", default="",
    help=(
        "Signup endpoint to exercise once, instead of "
        "newsletter_signup_canary_url."
    ),
)
@click.option("--json", "as_json", is_flag=True, help="Emit JSON for LLM/script consumers.")
def cmd_canary(url: str, as_json: bool) -> None:
    """Sign a Resend test inbox up through the public endpoint and verify it."""
    asyncio.run(_run_canary(url, as_json))


async def _run_canary(url: str, as_json: bool) -> None:
    pool, site_config = await _open_ctx()
    try:
        from poindexter.services.newsletter_signup_canary import (
            CanaryConfigError,
            run_signup_canary,
        )

        try:
            outcome = await run_signup_canary(site_config, url=url or None)
        except CanaryConfigError as exc:
            raise click.ClickException(str(exc)) from exc
    finally:
        await close_cli_pool(pool)

    if outcome is None:
        raise click.ClickException(
            "newsletter_signup_canary_url is not set. Pass --url to run once, "
            "or set it to switch the daily canary on."
        )
    if as_json:
        click.echo(json.dumps(
            {"url": outcome.url, "segment_id": outcome.segment_id,
             **outcome.as_metrics(), "problem": outcome.problem},
            indent=2,
        ))
    elif outcome.healthy:
        click.echo(
            f"captured: {outcome.url} answered HTTP {outcome.http_status} in "
            f"{outcome.latency_ms} ms and the canary reached segment "
            f"{outcome.segment_id}"
            + ("" if outcome.cleaned_up else " (cleanup failed; next run retries)")
        )
    else:
        click.echo(f"NOT CAPTURED: {outcome.problem}", err=True)
    if not outcome.healthy:
        sys.exit(1)
