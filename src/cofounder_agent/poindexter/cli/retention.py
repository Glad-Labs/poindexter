"""`poindexter retention` — operate on the declarative retention_policies table.

Minimal v1: list / show / enable / disable / run. The ``run`` subcommand
invokes :func:`services.integrations.retention_runner.run_all`
directly (optionally filtered to a single policy), so operators can
kick off a prune on demand without waiting for the scheduled tick.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from typing import Any

import click


def _dsn() -> str:
    dsn = (
        os.getenv("POINDEXTER_MEMORY_DSN")
        or os.getenv("LOCAL_DATABASE_URL")
        or os.getenv("DATABASE_URL")
        or ""
    )
    if not dsn:
        raise RuntimeError(
            "No DSN — set POINDEXTER_MEMORY_DSN, LOCAL_DATABASE_URL, or DATABASE_URL."
        )
    return dsn


def _run(coro):
    return asyncio.run(coro)


async def _connect():
    import asyncpg
    return await asyncpg.connect(_dsn())


def _format_age(ts: Any) -> str:
    if ts is None:
        return "—"
    import datetime as _dt
    now = _dt.datetime.now(_dt.timezone.utc)
    secs = int((now - ts).total_seconds())
    if secs < 0:
        return "future"
    if secs < 60:
        return f"{secs}s ago"
    if secs < 3600:
        return f"{secs // 60}m ago"
    if secs < 86400:
        return f"{secs // 3600}h ago"
    return f"{secs // 86400}d ago"


@click.group(
    name="retention",
    help="Manage retention policies (declarative lifecycle management).",
)
def retention_group() -> None:
    pass


@retention_group.command("list")
@click.option(
    "--state", default="",
    type=click.Choice(["", "enabled", "disabled"]),
    help="Filter by enabled state.",
)
def retention_list(state: str) -> None:
    """List every retention policy with current status."""
    async def _impl():
        conn = await _connect()
        try:
            where = ""
            if state == "enabled":
                where = "WHERE enabled = TRUE"
            elif state == "disabled":
                where = "WHERE enabled = FALSE"
            rows = await conn.fetch(
                f"""
                SELECT name, handler_name, table_name, ttl_days, enabled,
                       last_run_at, last_error, total_runs, total_deleted
                  FROM retention_policies
                  {where}
              ORDER BY name
                """
            )
            return [dict(r) for r in rows]
        finally:
            await conn.close()

    try:
        rows = _run(_impl())
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)

    if not rows:
        click.echo("(no retention policies)")
        return

    click.echo(
        f"{'NAME':<32} {'HANDLER':<13} {'TABLE':<18} {'TTL':<5} "
        f"{'STATE':<9} {'LAST RUN':<12} {'RUNS':<5} {'DELETED':<8}"
    )
    for r in rows:
        ttl = str(r["ttl_days"]) if r["ttl_days"] is not None else "—"
        state_txt = "enabled" if r["enabled"] else "disabled"
        color = "red" if r["last_error"] else ("green" if r["enabled"] else "yellow")
        line = (
            f"{r['name']:<32} {r['handler_name']:<13} {r['table_name']:<18} "
            f"{ttl:<5} {state_txt:<9} {_format_age(r['last_run_at']):<12} "
            f"{r['total_runs']:<5} {r['total_deleted']:<8}"
        )
        click.secho(line, fg=color)


@retention_group.command("show")
@click.argument("name")
def retention_show(name: str) -> None:
    """Show full details of one policy including config + rule JSONB."""
    async def _impl():
        conn = await _connect()
        try:
            row = await conn.fetchrow(
                "SELECT * FROM retention_policies WHERE name = $1", name
            )
            return dict(row) if row else None
        finally:
            await conn.close()

    try:
        row = _run(_impl())
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)

    if not row:
        click.echo(f"(no retention policy named {name!r})", err=True)
        sys.exit(1)

    for key in (
        "name", "handler_name", "table_name", "filter_sql", "age_column",
        "ttl_days", "downsample_rule", "summarize_handler", "enabled",
        "config", "metadata", "last_run_at", "last_run_duration_ms",
        "last_run_deleted", "last_run_summarized", "last_error",
        "total_runs", "total_deleted", "created_at", "updated_at",
    ):
        click.echo(f"  {key:<22} {row[key]!r}")


@retention_group.command("enable")
@click.argument("name")
def retention_enable(name: str) -> None:
    """Flip enabled=TRUE for one policy."""
    _set_enabled(name, True)


@retention_group.command("disable")
@click.argument("name")
def retention_disable(name: str) -> None:
    """Flip enabled=FALSE for one policy."""
    _set_enabled(name, False)


def _set_enabled(name: str, enabled: bool) -> None:
    async def _impl():
        conn = await _connect()
        try:
            return await conn.execute(
                "UPDATE retention_policies SET enabled = $2 WHERE name = $1",
                name, enabled,
            )
        finally:
            await conn.close()

    try:
        result = _run(_impl())
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)

    if result.endswith(" 0"):
        click.echo(f"(no policy named {name!r})", err=True)
        sys.exit(1)
    state = "enabled" if enabled else "disabled"
    click.secho(f"{name}: {state}", fg="green" if enabled else "yellow")


@retention_group.command("run")
@click.argument("name", required=False)
@click.option("--dry-run", is_flag=True, help="Handlers that support it will count without deleting.")
def retention_run(name: str | None, dry_run: bool) -> None:
    """Invoke the retention runner immediately.

    Without NAME: runs every enabled policy.
    With NAME: runs just that policy (requires enabled=TRUE).

    --dry-run temporarily sets config.dry_run=true on the matched
    policies for this invocation only. The flag is NOT persisted.
    """
    import asyncpg

    async def _impl():
        from services.integrations import retention_runner
        pool = await asyncpg.create_pool(_dsn(), min_size=1, max_size=2)
        try:
            if dry_run:
                # Toggle dry_run in config for selected policies, run, revert.
                async with pool.acquire() as conn:
                    if name:
                        await conn.execute(
                            "UPDATE retention_policies "
                            "SET config = config || '{\"dry_run\": true}'::jsonb "
                            "WHERE name = $1",
                            name,
                        )
                    else:
                        await conn.execute(
                            "UPDATE retention_policies "
                            "SET config = config || '{\"dry_run\": true}'::jsonb "
                            "WHERE enabled = TRUE"
                        )
            try:
                summary = await retention_runner.run_all(
                    pool, only_names=[name] if name else None,
                )
            finally:
                if dry_run:
                    async with pool.acquire() as conn:
                        await conn.execute(
                            "UPDATE retention_policies "
                            "SET config = config - 'dry_run'"
                        )
            return summary
        finally:
            await pool.close()

    try:
        summary = _run(_impl())
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)

    click.echo(json.dumps(summary.to_dict(), indent=2, default=str))
