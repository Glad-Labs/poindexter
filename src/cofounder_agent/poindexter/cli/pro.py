"""``poindexter pro`` — operate the Pro pay→deliver chain (#3216).

Thin adapter over ``services.pro_delivery`` per the transport-adapter
contract: every command delegates to a service function and holds no SQL
or business logic of its own.

Subcommands:

- ``status`` — config presence, subscription counts, recent rows.
- ``sync`` — run one reconcile pass NOW (ignores ``pro_delivery_enabled``;
  invoking the command IS the operator intent).
- ``link SUB USERNAME`` — attach a buyer's GitHub username and deliver
  immediately. SUB is a subscription id, id prefix, or customer email.
  This is the manual half of delivery when Lemon Squeezy doesn't hand the
  sync a username (the ``pro_delivery_action_needed`` finding names the
  exact command to run).
- ``unlink SUB`` — revoke access and detach the GitHub account.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import click

logger = logging.getLogger(__name__)


async def _open_ctx() -> tuple[Any, Any]:
    """Pool + a loaded SiteConfig (pool-backed so get_secret works)."""
    import asyncpg

    from ._bootstrap import resolve_dsn

    pool = await asyncpg.create_pool(resolve_dsn(), min_size=1, max_size=2)
    from services.site_config import SiteConfig

    site_config = SiteConfig(pool=pool)
    await site_config.load(pool)
    return pool, site_config


@click.group(
    "pro",
    help=(
        "Operate the Poindexter Pro delivery chain: Lemon Squeezy "
        "subscriptions in, GitHub repo access out. The scheduled sync "
        "handles the steady state; these commands are for status checks "
        "and the manual username-link path."
    ),
)
def pro_group() -> None:
    """pro command group."""


@pro_group.command("status")
@click.option("--limit", type=int, default=20, help="Rows to show (default 20).")
@click.option("--json", "as_json", is_flag=True, help="Emit JSON for LLM/script consumers.")
def cmd_status(limit: int, as_json: bool) -> None:
    """Show delivery config state + subscription inventory."""
    asyncio.run(_run_status(limit, as_json))


async def _run_status(limit: int, as_json: bool) -> None:
    pool, site_config = await _open_ctx()
    try:
        from services.pro_delivery import cli_status

        payload = await cli_status(pool, site_config, limit=limit)
    finally:
        await pool.close()

    if as_json:
        click.echo(json.dumps(payload, indent=2, default=str))
        return

    click.echo("=== pro delivery status ===")
    for key, value in payload["config"].items():
        click.echo(f"  {key} = {value}")
    click.echo()
    counts = payload["status_counts"]
    click.echo(f"Subscriptions by status: {counts or 'none synced yet'}")
    if payload["subscriptions"]:
        click.echo()
        click.echo(
            f"{'subscription':<14} {'status':<10} {'email':<28} "
            f"{'github':<18} {'delivered'}"
        )
        click.echo("-" * 84)
        for row in payload["subscriptions"]:
            delivered = (
                "revoked" if row["github_revoked_at"]
                else ("yes" if row["github_invited_at"] else "no")
            )
            click.echo(
                f"{str(row['subscription_id'])[:13]:<14} "
                f"{(row['status'] or '?')[:10]:<10} "
                f"{(row['customer_email'] or '?')[:27]:<28} "
                f"{(row['github_username'] or '—')[:17]:<18} {delivered}"
            )


@pro_group.command("sync")
@click.option("--json", "as_json", is_flag=True, help="Emit JSON for LLM/script consumers.")
def cmd_sync(as_json: bool) -> None:
    """Run one reconcile pass now (poll LS, converge GitHub access)."""
    asyncio.run(_run_sync_cmd(as_json))


async def _run_sync_cmd(as_json: bool) -> None:
    pool, site_config = await _open_ctx()
    try:
        from services.pro_delivery import ProDeliveryConfigError, run_sync

        try:
            outcome = await run_sync(pool, site_config)
        except ProDeliveryConfigError as exc:
            raise click.ClickException(str(exc)) from exc
    finally:
        await pool.close()

    if as_json:
        click.echo(json.dumps(outcome.as_metrics(), indent=2))
        return
    click.echo(
        f"synced {outcome.subscriptions_seen} subscription(s): "
        f"invited {outcome.invited or 'none'}, revoked {outcome.revoked or 'none'}, "
        f"missing username: {outcome.missing_username or 'none'}, "
        f"revenue rows written: {outcome.revenue_rows}"
    )
    for err in outcome.errors:
        click.echo(f"ERROR: {err}", err=True)


@pro_group.command("link")
@click.argument("subscription")
@click.argument("username")
@click.option("--json", "as_json", is_flag=True, help="Emit JSON for LLM/script consumers.")
def cmd_link(subscription: str, username: str, as_json: bool) -> None:
    """Attach USERNAME to SUBSCRIPTION (id, prefix, or email) and deliver now."""
    asyncio.run(_run_link(subscription, username, as_json))


async def _run_link(subscription: str, username: str, as_json: bool) -> None:
    pool, site_config = await _open_ctx()
    try:
        from services.pro_delivery import ProDeliveryConfigError, cli_link

        try:
            payload = await cli_link(pool, site_config, subscription, username)
        except (ValueError, ProDeliveryConfigError) as exc:
            raise click.ClickException(str(exc)) from exc
    finally:
        await pool.close()

    if as_json:
        click.echo(json.dumps(payload, indent=2, default=str))
        return
    state = "invited" if payload["invited"] else "linked (invite pending next sync)"
    click.echo(
        f"{payload['github_username']} {state} for subscription "
        f"{payload['subscription_id']}"
    )


@pro_group.command("unlink")
@click.argument("subscription")
@click.option("--json", "as_json", is_flag=True, help="Emit JSON for LLM/script consumers.")
def cmd_unlink(subscription: str, as_json: bool) -> None:
    """Revoke access for SUBSCRIPTION and detach its GitHub account."""
    asyncio.run(_run_unlink(subscription, as_json))


async def _run_unlink(subscription: str, as_json: bool) -> None:
    pool, site_config = await _open_ctx()
    try:
        from services.pro_delivery import ProDeliveryConfigError, cli_unlink

        try:
            payload = await cli_unlink(pool, site_config, subscription)
        except (ValueError, ProDeliveryConfigError) as exc:
            raise click.ClickException(str(exc)) from exc
    finally:
        await pool.close()

    if as_json:
        click.echo(json.dumps(payload, indent=2, default=str))
        return
    click.echo(
        f"unlinked {payload['unlinked'] or '(no github account)'} from "
        f"subscription {payload['subscription_id']}"
    )


@pro_group.command("apply")
@click.argument("seed", required=False)
@click.option(
    "--apply", "do_apply", is_flag=True,
    help="Write the adoptable values. Without this flag the command is a "
         "dry-run report.",
)
@click.option(
    "--include-models", is_flag=True,
    help="Also adopt model-pin / GPU / VRAM keys (held for review by default "
         "— they're tuned to the seller's hardware).",
)
@click.option(
    "--overwrite-conflicts", is_flag=True,
    help="Also overwrite keys YOU have customized with the seed's values. "
         "Off by default: your tuning wins.",
)
@click.option("--json", "as_json", is_flag=True, help="Emit JSON for LLM/script consumers.")
def cmd_apply(
    seed: str | None, do_apply: bool, include_models: bool,
    overwrite_conflicts: bool, as_json: bool,
) -> None:
    """Diff the Pro seed against your live settings; adopt it safely.

    SEED is your poindexter-pro checkout (directory or the
    seed-settings.json itself). Omitted, ./ and ~/poindexter-pro are tried.
    Dry-run by default; --apply only overwrites values still at their OSS
    defaults, so your own tuning is never clobbered without
    --overwrite-conflicts.
    """
    if (include_models or overwrite_conflicts) and not do_apply:
        raise click.ClickException(
            "--include-models / --overwrite-conflicts only mean something "
            "with --apply (the default run is a dry-run report)."
        )
    asyncio.run(_run_apply(seed, do_apply, include_models, overwrite_conflicts, as_json))


async def _run_apply(
    seed: str | None, do_apply: bool, include_models: bool,
    overwrite_conflicts: bool, as_json: bool,
) -> None:
    pool, _site_config = await _open_ctx()
    try:
        from services.pro_delivery import cli_apply

        try:
            payload = await cli_apply(
                pool, seed,
                apply=do_apply,
                include_models=include_models,
                overwrite_conflicts=overwrite_conflicts,
            )
        except ValueError as exc:
            raise click.ClickException(str(exc)) from exc
    finally:
        await pool.close()

    if as_json:
        click.echo(json.dumps(payload, indent=2, default=str))
        return

    counts = payload["counts"]
    click.echo(f"seed: {payload['seed_path']}")
    click.echo(
        f"{'DRY RUN — ' if payload['dry_run'] else ''}"
        f"adoptable {counts['adoptable']}, held for review {counts['review_held']}, "
        f"conflicts kept {counts['conflicts_kept']}, identical {counts['identical']}, "
        f"unknown to this engine {counts['unknown_to_this_engine']}"
    )

    def _show(title: str, bucket: dict, hint: str) -> None:
        if not bucket:
            return
        click.echo()
        click.echo(f"{title} ({len(bucket)}):{'' if not hint else '  # ' + hint}")
        for key, (cur, new) in list(bucket.items())[:20]:
            click.echo(f"  {key}: {cur!r} -> {new!r}")
        if len(bucket) > 20:
            click.echo(f"  … and {len(bucket) - 20} more (use --json for all)")

    _show("Adoptable (you're on stock values)", payload["adoptable"],
          "" if not payload["dry_run"] else "apply with --apply")
    _show("Held for review (model/GPU keys)", payload["review_held"],
          "seller-hardware-tuned; --apply --include-models to adopt")
    _show("Conflicts kept (your tuning wins)", payload["conflicts_kept"],
          "--apply --overwrite-conflicts to take the seed's values")

    if payload["applied"]:
        click.echo()
        click.echo(
            f"applied {len(payload['applied'])} value(s) — live within ~1 min "
            "(reload_site_config), no restart needed."
        )
    elif payload["dry_run"] and counts["adoptable"]:
        click.echo()
        click.echo(f"re-run with --apply to adopt the {counts['adoptable']} value(s) above.")


__all__ = ["pro_group"]
