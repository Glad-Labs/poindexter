"""``poindexter auto-publish`` — surface auto_publish_gate dry-run state.

Per ``feedback_cli_first``: the CLI is the default operator interface;
MCP + REST are secondary surfaces. Per
``feedback_auto_publish_requires_edit_distance_track_record``: the
operator flips auto-publish from observe-only to live ONLY after the
edit-distance data shows trust is earned. This command surfaces that
data.

Subcommands:

- ``status`` — current setting values (threshold, dry_run, min_clean_runs,
  max_edit_distance) + recent gate decisions. JSON output mode for
  LLM consumers.
- ``trend`` — per-niche edit-distance trend over the last N publishes.
  Shows when the trailing-N clean-run criterion is satisfied.
- ``decisions`` — recent gate decisions from audit_log with would_fire
  + reason text.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import click

logger = logging.getLogger(__name__)


async def _open_pool() -> Any:
    import asyncpg
    from ._bootstrap import resolve_dsn
    dsn = resolve_dsn()
    return await asyncpg.create_pool(dsn, min_size=1, max_size=2)


@click.group(
    "auto-publish",
    help=(
        "Inspect the auto_publish_gate dry-run state. The gate decides "
        "whether the system would have auto-published a post; until you "
        "flip dry_run=false in app_settings, no post is auto-published. "
        "Use these commands to see whether edit-distance is trending toward "
        "trust before flipping the gate live."
    ),
)
def auto_publish_group() -> None:
    """auto-publish command group."""


@auto_publish_group.command("status")
@click.option(
    "--json", "as_json", is_flag=True,
    help="Emit JSON for LLM/script consumers.",
)
def cmd_status(as_json: bool) -> None:
    """Show current gate settings + last 24h decision counts."""
    asyncio.run(_run_status(as_json))


async def _run_status(as_json: bool) -> None:
    pool = await _open_pool()
    async with pool.acquire() as conn:
        settings_rows = await conn.fetch(
            "SELECT key, value FROM app_settings "
            "WHERE key LIKE 'dev_diary_auto_publish%' ORDER BY key"
        )
        settings = {r["key"]: r["value"] for r in settings_rows}

        last_24h = await conn.fetchrow(
            """
            SELECT
              COUNT(*) AS total,
              SUM(CASE WHEN (details->>'would_fire')::boolean THEN 1 ELSE 0 END) AS would_fire_count,
              SUM(CASE WHEN details->>'gate_state' = 'pass' THEN 1 ELSE 0 END) AS pass_count,
              SUM(CASE WHEN details->>'gate_state' = 'block_threshold' THEN 1 ELSE 0 END) AS block_threshold_count,
              SUM(CASE WHEN details->>'gate_state' = 'block_unclean' THEN 1 ELSE 0 END) AS block_unclean_count,
              SUM(CASE WHEN details->>'gate_state' = 'no_history' THEN 1 ELSE 0 END) AS no_history_count,
              SUM(CASE WHEN details->>'gate_state' = 'disabled' THEN 1 ELSE 0 END) AS disabled_count
              FROM audit_log
             WHERE event_type = 'auto_publish_gate'
               AND timestamp > NOW() - INTERVAL '24 hours'
            """
        )
        recent_publishes = await conn.fetchval(
            "SELECT COUNT(*) FROM published_post_edit_metrics "
            "WHERE approved_at > NOW() - INTERVAL '7 days'"
        )

    await pool.close()

    payload = {
        "settings": settings,
        "last_24h_decisions": dict(last_24h) if last_24h else {},
        "publishes_last_7d": recent_publishes,
        "live_or_dry_run": (
            "LIVE — auto-publish active"
            if settings.get("dev_diary_auto_publish_dry_run", "true").lower() == "false"
            and float(settings.get("dev_diary_auto_publish_threshold", "-1")) >= 0
            else "DRY-RUN (observe only — flip dry_run=false in app_settings to enable)"
        ),
    }

    if as_json:
        click.echo(json.dumps(payload, indent=2, default=str))
        return

    click.echo("=== auto_publish_gate status ===")
    click.echo(f"State: {payload['live_or_dry_run']}")
    click.echo()
    click.echo("Settings:")
    for k, v in settings.items():
        click.echo(f"  {k} = {v}")
    click.echo()
    click.echo(f"Last 24h decisions: {dict(last_24h) if last_24h else 'none'}")
    click.echo(f"Publishes (last 7d): {recent_publishes}")


@auto_publish_group.command("trend")
@click.option(
    "--niche", "niche", default=None,
    help="Filter to a specific niche slug (e.g. dev_diary). Default: all.",
)
@click.option(
    "--last", "last_n", type=int, default=14,
    help="Show edit-distance for the last N publishes (default 14).",
)
@click.option(
    "--json", "as_json", is_flag=True,
    help="Emit JSON for LLM/script consumers.",
)
def cmd_trend(niche: str | None, last_n: int, as_json: bool) -> None:
    """Show edit-distance trend per publish — see when trust is earning."""
    asyncio.run(_run_trend(niche, last_n, as_json))


async def _run_trend(niche: str | None, last_n: int, as_json: bool) -> None:
    pool = await _open_pool()
    async with pool.acquire() as conn:
        if niche:
            rows = await conn.fetch(
                """
                SELECT approved_at, niche_slug, category, char_diff_count,
                       line_diff_count, pre_approve_len, post_approve_len, approver
                  FROM published_post_edit_metrics
                 WHERE COALESCE(NULLIF(niche_slug, ''), category) = $1
                 ORDER BY approved_at DESC
                 LIMIT $2
                """,
                niche, last_n,
            )
        else:
            rows = await conn.fetch(
                """
                SELECT approved_at, niche_slug, category, char_diff_count,
                       line_diff_count, pre_approve_len, post_approve_len, approver
                  FROM published_post_edit_metrics
                 ORDER BY approved_at DESC
                 LIMIT $1
                """,
                last_n,
            )
    await pool.close()

    if as_json:
        click.echo(json.dumps([dict(r) for r in rows], indent=2, default=str))
        return

    if not rows:
        click.echo("(no edit-distance metrics recorded yet)")
        click.echo(
            "Edit-distance rows accumulate as publishes happen. "
            "Tomorrow's dev_diary cron is the first opportunity."
        )
        return

    click.echo(f"Last {len(rows)} publishes (niche filter: {niche or 'all'}):")
    click.echo()
    click.echo(
        f"{'when':<26} {'niche':<14} {'char_diff':>10} {'line_diff':>10} "
        f"{'pre/post len':>14} {'approver':>12}"
    )
    click.echo("-" * 90)
    for r in rows:
        slug = r["niche_slug"] or r["category"] or "unknown"
        click.echo(
            f"{str(r['approved_at'])[:25]:<26} {slug[:14]:<14} "
            f"{r['char_diff_count']:>10} {r['line_diff_count']:>10} "
            f"{r['pre_approve_len']}/{r['post_approve_len']:<8} "
            f"{(r['approver'] or '?')[:12]:>12}"
        )
    click.echo()
    clean = sum(1 for r in rows if r["char_diff_count"] < 50)
    click.echo(
        f"Clean runs (char_diff < 50): {clean}/{len(rows)} "
        f"({100*clean//max(1, len(rows))}%)"
    )


@auto_publish_group.command("decisions")
@click.option(
    "--last", "last_n", type=int, default=20,
    help="Show last N gate decisions (default 20).",
)
@click.option(
    "--would-fire-only", is_flag=True,
    help="Filter to decisions where would_fire=true.",
)
@click.option(
    "--json", "as_json", is_flag=True,
    help="Emit JSON for LLM/script consumers.",
)
def cmd_decisions(last_n: int, would_fire_only: bool, as_json: bool) -> None:
    """Show recent gate decisions — what the gate WOULD HAVE done."""
    asyncio.run(_run_decisions(last_n, would_fire_only, as_json))


async def _run_decisions(last_n: int, would_fire_only: bool, as_json: bool) -> None:
    pool = await _open_pool()
    where = "WHERE event_type = 'auto_publish_gate'"
    if would_fire_only:
        where += " AND (details->>'would_fire')::boolean = true"
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"""
            SELECT timestamp, task_id,
                   details->>'gate_state' AS gate_state,
                   (details->>'would_fire')::boolean AS would_fire,
                   (details->>'dry_run')::boolean AS dry_run,
                   ROUND((details->>'quality_score')::numeric, 1) AS quality_score,
                   ROUND((details->>'threshold')::numeric, 1) AS threshold,
                   details->>'reason' AS reason
              FROM audit_log
              {where}
             ORDER BY timestamp DESC
             LIMIT $1
            """,
            last_n,
        )
    await pool.close()

    if as_json:
        click.echo(json.dumps([dict(r) for r in rows], indent=2, default=str))
        return

    if not rows:
        click.echo("(no gate decisions logged yet)")
        click.echo(
            "Decisions are logged each time finalize_task runs. "
            "Tomorrow's dev_diary cron will produce the first row."
        )
        return

    click.echo(f"Last {len(rows)} gate decisions:")
    click.echo()
    for r in rows:
        marker = "FIRE" if r["would_fire"] else "BLOCK"
        click.echo(
            f"[{str(r['timestamp'])[:19]}] {marker:<5} {r['gate_state']:<16} "
            f"task={(r['task_id'] or '?')[:8]} "
            f"q={r['quality_score']}/threshold={r['threshold']}"
        )
        click.echo(f"  {r['reason']}")
        click.echo()


__all__ = ["auto_publish_group"]
