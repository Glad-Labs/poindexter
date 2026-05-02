"""`poindexter taps` — manage the external_taps table (GH-103).

Minimal v1 set: list / show / enable / disable / run. Full CRUD
(add / remove / set-credentials) lands in v1.1 once Singer subprocess
support is live.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from typing import Any

import click


from poindexter.cli._bootstrap import resolve_dsn as _dsn  # noqa: E402


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
    name="taps",
    help="Manage external data taps (Singer + built-in scrapers).",
)
def taps_group() -> None:
    pass


@taps_group.command("list")
@click.option(
    "--state", default="",
    type=click.Choice(["", "enabled", "disabled"]),
    help="Filter by enabled state.",
)
def taps_list(state: str) -> None:
    """List every tap with current status."""
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
                SELECT name, handler_name, tap_type, schedule, enabled,
                       last_run_at, last_run_status, total_runs, total_records,
                       last_error
                  FROM external_taps
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
        click.echo("(no external taps)")
        return

    click.echo(
        f"{'NAME':<18} {'HANDLER':<22} {'TAP TYPE':<12} {'SCHEDULE':<17} "
        f"{'STATE':<9} {'LAST':<12} {'RUNS':<5} {'RECORDS':<8}"
    )
    for r in rows:
        state_txt = "enabled" if r["enabled"] else "disabled"
        color = "red" if r["last_error"] else ("green" if r["enabled"] else "yellow")
        line = (
            f"{r['name']:<18} {r['handler_name']:<22} {r['tap_type']:<12} "
            f"{(r['schedule'] or ''):<17} {state_txt:<9} "
            f"{_format_age(r['last_run_at']):<12} "
            f"{r['total_runs']:<5} {r['total_records']:<8}"
        )
        click.secho(line, fg=color)


@taps_group.command("show")
@click.argument("name")
def taps_show(name: str) -> None:
    """Show full details of one tap row."""
    async def _impl():
        conn = await _connect()
        try:
            row = await conn.fetchrow(
                "SELECT * FROM external_taps WHERE name = $1", name
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
        click.echo(f"(no tap named {name!r})", err=True)
        sys.exit(1)

    for key in (
        "name", "handler_name", "tap_type", "target_table", "record_handler",
        "schedule", "config", "state", "enabled", "metadata",
        "last_run_at", "last_run_duration_ms", "last_run_status",
        "last_run_records", "last_error", "total_runs", "total_records",
        "created_at", "updated_at",
    ):
        click.echo(f"  {key:<22} {row[key]!r}")


@taps_group.command("enable")
@click.argument("name")
def taps_enable(name: str) -> None:
    _set_enabled(name, True)


@taps_group.command("disable")
@click.argument("name")
def taps_disable(name: str) -> None:
    _set_enabled(name, False)


def _set_enabled(name: str, enabled: bool) -> None:
    async def _impl():
        conn = await _connect()
        try:
            return await conn.execute(
                "UPDATE external_taps SET enabled = $2 WHERE name = $1",
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
        click.echo(f"(no tap named {name!r})", err=True)
        sys.exit(1)
    state = "enabled" if enabled else "disabled"
    click.secho(f"{name}: {state}", fg="green" if enabled else "yellow")


@taps_group.command("run")
@click.argument("name", required=False)
def taps_run(name: str | None) -> None:
    """Invoke the tap runner immediately.

    Without NAME: runs every enabled tap.
    With NAME: runs just that tap (requires enabled=TRUE).
    """
    import asyncpg

    async def _impl():
        from services.integrations import tap_runner
        pool = await asyncpg.create_pool(_dsn(), min_size=1, max_size=2)
        try:
            summary = await tap_runner.run_all(
                pool, only_names=[name] if name else None,
            )
        finally:
            await pool.close()
        return summary

    try:
        summary = _run(_impl())
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)

    click.echo(json.dumps(summary.to_dict(), indent=2, default=str))
