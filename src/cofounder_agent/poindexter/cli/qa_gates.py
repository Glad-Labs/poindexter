"""``poindexter qa-gates`` — manage the ``qa_gates`` table (GH-115).

Declarative QA chain. Each row describes one gate instance. The runtime
walks enabled rows in ``execution_order`` and dispatches to the named
reviewer plugin.

Commands:

    poindexter qa-gates list                         # all rows
    poindexter qa-gates list --state enabled         # filter
    poindexter qa-gates show NAME                    # full row dump
    poindexter qa-gates enable NAME
    poindexter qa-gates disable NAME
    poindexter qa-gates reorder NAME NEW_ORDER       # change execution_order

Mutations are persisted directly to PostgreSQL — no app restart
required. The ReloadSiteConfigJob and the runtime ``qa_gates`` lookup
both pick up changes on the next pipeline tick.
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


@click.group(
    name="qa-gates",
    help="Manage the declarative QA gate chain (qa_gates table).",
)
def qa_gates_group() -> None:
    """Root for ``poindexter qa-gates ...`` commands."""


@qa_gates_group.command("list")
@click.option(
    "--state", default="",
    type=click.Choice(["", "enabled", "disabled"]),
    help="Filter by enabled state.",
)
@click.option(
    "--stage", default="qa",
    help="Filter by stage_name (default: qa).",
)
def qa_gates_list(state: str, stage: str) -> None:
    """List every gate row with current status, ordered by execution_order."""
    async def _impl() -> list[dict[str, Any]]:
        conn = await _connect()
        try:
            where = ["stage_name = $1"]
            args: list[Any] = [stage]
            if state == "enabled":
                where.append("enabled = TRUE")
            elif state == "disabled":
                where.append("enabled = FALSE")
            where_sql = " AND ".join(where)
            rows = await conn.fetch(
                f"""
                SELECT name, stage_name, execution_order, reviewer,
                       required_to_pass, enabled, total_runs, total_rejections,
                       last_run_at, last_run_status, last_error
                  FROM qa_gates
                 WHERE {where_sql}
              ORDER BY execution_order ASC, name ASC
                """,
                *args,
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
        click.echo("(no qa_gates rows — run migrations 0093/0094)")
        return

    click.echo(
        f"{'ORDER':<6} {'NAME':<24} {'REVIEWER':<24} {'REQUIRED':<9} "
        f"{'STATE':<9} {'RUNS':<6} {'REJECT':<7}"
    )
    for r in rows:
        state_txt = "enabled" if r["enabled"] else "disabled"
        color = (
            "red" if r["last_error"]
            else ("green" if r["enabled"] else "yellow")
        )
        line = (
            f"{r['execution_order']:<6} {r['name']:<24} {r['reviewer']:<24} "
            f"{('yes' if r['required_to_pass'] else 'no'):<9} "
            f"{state_txt:<9} {r['total_runs']:<6} {r['total_rejections']:<7}"
        )
        click.secho(line, fg=color)


@qa_gates_group.command("show")
@click.argument("name")
def qa_gates_show(name: str) -> None:
    """Show full details of a single gate row."""
    async def _impl():
        conn = await _connect()
        try:
            row = await conn.fetchrow(
                "SELECT * FROM qa_gates WHERE name = $1", name,
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
        click.echo(f"(no qa_gates row named {name!r})", err=True)
        sys.exit(1)

    for key in (
        "name", "stage_name", "execution_order", "reviewer",
        "required_to_pass", "enabled", "config", "metadata",
        "last_run_at", "last_run_duration_ms", "last_run_status",
        "total_runs", "total_rejections", "last_error",
        "created_at", "updated_at",
    ):
        val = row.get(key)
        if isinstance(val, (dict, list)):
            val = json.dumps(val, default=str)
        click.echo(f"  {key:<20} {val!r}")


@qa_gates_group.command("enable")
@click.argument("name")
def qa_gates_enable(name: str) -> None:
    """Mark a gate enabled — runtime will run it on the next pipeline tick."""
    _set_enabled(name, True)


@qa_gates_group.command("disable")
@click.argument("name")
def qa_gates_disable(name: str) -> None:
    """Mark a gate disabled — runtime will skip it. No restart needed."""
    _set_enabled(name, False)


def _set_enabled(name: str, enabled: bool) -> None:
    async def _impl():
        conn = await _connect()
        try:
            return await conn.execute(
                "UPDATE qa_gates SET enabled = $2 WHERE name = $1",
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
        click.echo(f"(no qa_gates row named {name!r})", err=True)
        sys.exit(1)
    state = "enabled" if enabled else "disabled"
    click.secho(f"{name}: {state}", fg="green" if enabled else "yellow")


@qa_gates_group.command("reorder")
@click.argument("name")
@click.argument("new_order", type=int)
def qa_gates_reorder(name: str, new_order: int) -> None:
    """Change a gate's execution_order. Effective on the next pipeline tick."""
    async def _impl():
        conn = await _connect()
        try:
            return await conn.execute(
                "UPDATE qa_gates SET execution_order = $2 WHERE name = $1",
                name, new_order,
            )
        finally:
            await conn.close()

    try:
        result = _run(_impl())
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)

    if result.endswith(" 0"):
        click.echo(f"(no qa_gates row named {name!r})", err=True)
        sys.exit(1)
    click.secho(f"{name}: execution_order = {new_order}", fg="cyan")


__all__ = ["qa_gates_group"]
