"""``poindexter approve`` / ``reject`` / ``list-pending`` / ``show-pending``
/ ``gates`` — operator interface for HITL approval gates (#145).

Single source of truth lives in :mod:`services.approval_service`. This
module is a thin Click wrapper that:

1. Resolves a DB DSN (same env-var ladder ``poindexter qa-gates`` uses).
2. Constructs an asyncpg pool + a SiteConfig instance.
3. Calls into ``approval_service`` and renders the result.

All output commands accept ``--json`` for machine-readable output
suitable for piping into ``jq`` / ``xargs`` / a shell loop.

Examples
--------

    poindexter approve <task_id>                    # any active gate
    poindexter approve <task_id> --gate topic_decision --feedback "good"
    poindexter reject  <task_id> --reason "off-brand"
    poindexter list-pending                          # human-readable
    poindexter list-pending --json | jq '.[].task_id'
    poindexter show-pending <task_id>
    poindexter gates list
    poindexter gates set topic_decision on
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from typing import Any

import click


def _dsn() -> str:
    """Resolve the PostgreSQL DSN.

    Same env-var ladder the rest of the CLI uses (``qa_gates.py``,
    ``taps.py``, ``stores.py``). The CLI runs outside the worker
    process, so it can't read ``app.state.site_config`` — env vars
    are the standard escape hatch.
    """
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


async def _make_pool():
    """Open a tiny pool for one CLI invocation. Closed by the caller
    in a try/finally so we don't leak connections on errors."""
    import asyncpg
    return await asyncpg.create_pool(_dsn(), min_size=1, max_size=2)


async def _make_site_config(pool):
    """Construct a SiteConfig the CLI commands can hand into the
    service module. Loaded from the DB so gate-enable settings are
    visible to the same process.
    """
    from services.site_config import SiteConfig

    cfg = SiteConfig(pool=pool)
    try:
        await cfg.load(pool)
    except Exception:
        # Defensive — the load path may fail in odd environments
        # (missing app_settings table, partial bootstrap). Fall back to
        # an empty config so gate-list / gate-set still works.
        pass
    return cfg


# ---------------------------------------------------------------------------
# Helpers — formatting
# ---------------------------------------------------------------------------


def _print_pending_row(row: dict[str, Any]) -> None:
    tid = (row.get("task_id") or "")[:8]
    gate = row.get("gate_name") or "?"
    paused = row.get("gate_paused_at") or "-"
    title = row.get("title") or row.get("topic") or "(no title)"
    artifact = row.get("artifact") or {}
    summary_keys = sorted(artifact.keys())[:5]
    summary = ", ".join(summary_keys) if summary_keys else "(empty)"

    click.secho(f"  {tid}  {gate:<24} {title[:50]}", fg="yellow")
    click.secho(f"    paused_at={paused}  artifact_keys=[{summary}]", fg="bright_black")


def _exit_error(msg: str, code: int = 1) -> None:
    click.echo(f"Error: {msg}", err=True)
    sys.exit(code)


# ---------------------------------------------------------------------------
# approve
# ---------------------------------------------------------------------------


@click.command("approve")
@click.argument("task_id")
@click.option(
    "--gate", "gate_name", default=None,
    help="Optional gate name to assert. Default: clear whatever gate is currently active.",
)
@click.option("--feedback", default=None, help="Optional operator note (recorded in audit_log).")
@click.option("--json", "json_output", is_flag=True, help="Print result as JSON.")
def approve_command(
    task_id: str, gate_name: str | None, feedback: str | None, json_output: bool,
) -> None:
    """Approve a task at its current (or named) HITL gate.

    Clears the gate columns and re-queues the pipeline by inserting a
    ``pipeline_events`` row. The runner picks up where it left off.
    """
    from services.approval_service import (
        ApprovalServiceError,
        approve as approve_service,
    )

    async def _impl():
        pool = await _make_pool()
        try:
            site_config = await _make_site_config(pool)
            return await approve_service(
                task_id=task_id,
                gate_name=gate_name,
                feedback=feedback,
                site_config=site_config,
                pool=pool,
            )
        finally:
            await pool.close()

    try:
        result = _run(_impl())
    except ApprovalServiceError as e:
        _exit_error(str(e))
        return
    except Exception as e:
        _exit_error(f"unexpected: {type(e).__name__}: {e}")
        return

    if json_output:
        click.echo(json.dumps(result, indent=2, default=str))
        return

    click.secho(
        f"Approved task {task_id} at gate {result.get('gate_name')!r}.",
        fg="green",
    )
    if feedback:
        click.echo(f"  feedback: {feedback}")


# ---------------------------------------------------------------------------
# reject
# ---------------------------------------------------------------------------


@click.command("reject")
@click.argument("task_id")
@click.option("--gate", "gate_name", default=None, help="Optional gate name to assert.")
@click.option("--reason", default=None, help="Operator-supplied veto reason (recorded).")
@click.option("--json", "json_output", is_flag=True)
def reject_command(
    task_id: str, gate_name: str | None, reason: str | None, json_output: bool,
) -> None:
    """Reject a task at its current (or named) HITL gate.

    Sets the task to the gate's reject status (``rejected`` by default)
    and clears the gate columns. The pipeline halts; no auto-retry.
    """
    from services.approval_service import (
        ApprovalServiceError,
        reject as reject_service,
    )

    async def _impl():
        pool = await _make_pool()
        try:
            site_config = await _make_site_config(pool)
            return await reject_service(
                task_id=task_id,
                gate_name=gate_name,
                reason=reason,
                site_config=site_config,
                pool=pool,
            )
        finally:
            await pool.close()

    try:
        result = _run(_impl())
    except ApprovalServiceError as e:
        _exit_error(str(e))
        return
    except Exception as e:
        _exit_error(f"unexpected: {type(e).__name__}: {e}")
        return

    if json_output:
        click.echo(json.dumps(result, indent=2, default=str))
        return

    click.secho(
        f"Rejected task {task_id} at gate {result.get('gate_name')!r} "
        f"→ status={result.get('new_status')!r}.",
        fg="yellow",
    )
    if reason:
        click.echo(f"  reason: {reason}")


# ---------------------------------------------------------------------------
# list-pending
# ---------------------------------------------------------------------------


@click.command("list-pending")
@click.option("--gate", "gate_name", default=None, help="Filter by gate name.")
@click.option("--limit", type=int, default=100, show_default=True)
@click.option("--json", "json_output", is_flag=True)
def list_pending_command(
    gate_name: str | None, limit: int, json_output: bool,
) -> None:
    """List every task currently paused at any (or one) HITL gate.

    Ordered oldest-first so you work the queue chronologically.
    """
    from services.approval_service import list_pending

    async def _impl():
        pool = await _make_pool()
        try:
            return await list_pending(pool=pool, gate_name=gate_name, limit=limit)
        finally:
            await pool.close()

    try:
        rows = _run(_impl())
    except Exception as e:
        _exit_error(f"{type(e).__name__}: {e}")
        return

    if json_output:
        click.echo(json.dumps(rows, indent=2, default=str))
        return

    if not rows:
        click.echo("(no pending gates)")
        return

    label = f"gate={gate_name!r}" if gate_name else "all gates"
    click.secho(f"Pending gates ({label}): {len(rows)}", fg="cyan")
    click.echo()
    for row in rows:
        _print_pending_row(row)


# ---------------------------------------------------------------------------
# show-pending
# ---------------------------------------------------------------------------


@click.command("show-pending")
@click.argument("task_id")
@click.option("--json", "json_output", is_flag=True)
def show_pending_command(task_id: str, json_output: bool) -> None:
    """Show the gate state + full artifact for one task."""
    from services.approval_service import (
        ApprovalServiceError,
        show_pending,
    )

    async def _impl():
        pool = await _make_pool()
        try:
            return await show_pending(pool=pool, task_id=task_id)
        finally:
            await pool.close()

    try:
        row = _run(_impl())
    except ApprovalServiceError as e:
        _exit_error(str(e))
        return
    except Exception as e:
        _exit_error(f"unexpected: {type(e).__name__}: {e}")
        return

    if json_output:
        click.echo(json.dumps(row, indent=2, default=str))
        return

    click.secho(f"Task {row['task_id']}", fg="cyan", bold=True)
    click.echo(f"  gate         {row.get('gate_name')!r}")
    click.echo(f"  paused_at    {row.get('gate_paused_at')}")
    click.echo(f"  status       {row.get('status')}")
    click.echo(f"  topic        {row.get('topic')}")
    click.echo(f"  title        {row.get('title')}")
    click.echo()
    click.secho("  artifact:", fg="bright_black")
    artifact = row.get("artifact") or {}
    if not artifact:
        click.echo("    (empty)")
    else:
        for k, v in artifact.items():
            v_str = json.dumps(v, default=str) if not isinstance(v, str) else v
            if len(v_str) > 200:
                v_str = v_str[:197] + "..."
            click.echo(f"    {k}: {v_str}")


# ---------------------------------------------------------------------------
# gates list / set
# ---------------------------------------------------------------------------


@click.group(name="gates", help="List + toggle HITL approval gates.")
def gates_group() -> None:
    pass


@gates_group.command("list")
@click.option("--json", "json_output", is_flag=True)
def gates_list_command(json_output: bool) -> None:
    """Show every known gate + its enabled state + pending count."""
    from services.approval_service import list_gates

    async def _impl():
        pool = await _make_pool()
        try:
            site_config = await _make_site_config(pool)
            return await list_gates(pool=pool, site_config=site_config)
        finally:
            await pool.close()

    try:
        rows = _run(_impl())
    except Exception as e:
        _exit_error(f"{type(e).__name__}: {e}")
        return

    if json_output:
        click.echo(json.dumps(rows, indent=2, default=str))
        return

    if not rows:
        click.echo(
            "(no gates configured yet — set one with `poindexter gates set "
            "<gate_name> on`)"
        )
        return

    click.echo(f"{'GATE':<28} {'STATE':<10} {'PENDING':<8}")
    for row in rows:
        state = "enabled" if row["enabled"] else "disabled"
        color = "green" if row["enabled"] else "yellow"
        click.secho(
            f"{row['gate_name']:<28} {state:<10} {row['pending_count']:<8}",
            fg=color,
        )


@gates_group.command("set")
@click.argument("gate_name")
@click.argument("state", type=click.Choice(["on", "off"]))
@click.option("--json", "json_output", is_flag=True)
def gates_set_command(gate_name: str, state: str, json_output: bool) -> None:
    """Toggle a HITL gate on or off (writes ``app_settings``).

    Effective on the next pipeline tick — no worker restart needed.
    """
    from services.approval_service import set_gate_enabled

    async def _impl():
        pool = await _make_pool()
        try:
            site_config = await _make_site_config(pool)
            return await set_gate_enabled(
                gate_name=gate_name,
                enabled=(state == "on"),
                pool=pool,
                site_config=site_config,
            )
        finally:
            await pool.close()

    try:
        result = _run(_impl())
    except Exception as e:
        _exit_error(f"{type(e).__name__}: {e}")
        return

    if json_output:
        click.echo(json.dumps(result, indent=2, default=str))
        return

    click.secho(
        f"Gate {gate_name!r}: {state}",
        fg=("green" if state == "on" else "yellow"),
    )


__all__ = [
    "approve_command",
    "reject_command",
    "list_pending_command",
    "show_pending_command",
    "gates_group",
]
