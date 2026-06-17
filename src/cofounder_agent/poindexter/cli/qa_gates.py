"""``poindexter qa-gates`` — manage the ``qa_gates`` table (GH-115).

Declarative QA chain. Each row describes one gate instance. The runtime walks
enabled rows in ``execution_order`` and dispatches to the named reviewer.

    poindexter qa-gates list [--state ...] [--stage qa]
    poindexter qa-gates show NAME
    poindexter qa-gates enable NAME
    poindexter qa-gates disable NAME
    poindexter qa-gates reorder NAME NEW_ORDER

Thin adapter over :mod:`services.declarative_config_service` (#1522, epic
#1340) — config mutations persist through the service (no app restart needed;
the runtime ``qa_gates`` lookup picks them up next tick). The runtime *read*
path stays in :mod:`services.qa_gates_db`. No raw SQL or asyncpg connection
lives here.
"""

from __future__ import annotations

import sys

import click

from poindexter.cli._dataplane import dump_row, render_table, run_service
from services import declarative_config_service as dcs

_SURFACE = "qa-gates"

_COLUMNS = [
    ("execution_order", "ORDER", 6, None),
    ("name", "NAME", 24, None),
    ("reviewer", "REVIEWER", 24, None),
    ("required_to_pass", "REQUIRED", 9, lambda v: "yes" if v else "no"),
    ("enabled", "STATE", 9, lambda v: "enabled" if v else "disabled"),
    ("total_runs", "RUNS", 6, None),
    ("total_rejections", "REJECT", 7, None),
]


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
@click.option("--stage", default="qa", help="Filter by stage_name (default: qa).")
def qa_gates_list(state: str, stage: str) -> None:
    """List every gate row with current status, ordered by execution_order."""
    filters: dict = {"stage_name": stage}
    if state == "enabled":
        filters["enabled"] = True
    elif state == "disabled":
        filters["enabled"] = False
    try:
        rows = run_service(lambda p: dcs.list_rows(p, _SURFACE, filters=filters))
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)
    # The generic service orders by the key column; QA gates read in
    # execution_order, so sort here for display.
    rows.sort(key=lambda r: (r.get("execution_order", 0), r.get("name", "")))
    render_table(rows, _COLUMNS, empty="(no qa_gates rows — run migrations 0093/0094)")


@qa_gates_group.command("show")
@click.argument("name")
def qa_gates_show(name: str) -> None:
    """Show full details of a single gate row."""
    try:
        row = run_service(lambda p: dcs.get_row(p, _SURFACE, name))
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)
    if not dump_row(row, missing=f"(no qa_gates row named {name!r})"):
        sys.exit(1)


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
    async def _impl(pool):
        row = await dcs.get_row(pool, _SURFACE, name)
        if row is None:
            return False
        await dcs.upsert_row(pool, _SURFACE, {**row, "enabled": enabled})
        return True

    try:
        ok = run_service(_impl)
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)

    if not ok:
        click.echo(f"(no qa_gates row named {name!r})", err=True)
        sys.exit(1)
    state = "enabled" if enabled else "disabled"
    click.secho(f"{name}: {state}", fg="green" if enabled else "yellow")


@qa_gates_group.command("reorder")
@click.argument("name")
@click.argument("new_order", type=int)
def qa_gates_reorder(name: str, new_order: int) -> None:
    """Change a gate's execution_order. Effective on the next pipeline tick."""
    async def _impl(pool):
        row = await dcs.get_row(pool, _SURFACE, name)
        if row is None:
            return False
        await dcs.upsert_row(pool, _SURFACE, {**row, "execution_order": new_order})
        return True

    try:
        ok = run_service(_impl)
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)

    if not ok:
        click.echo(f"(no qa_gates row named {name!r})", err=True)
        sys.exit(1)
    click.secho(f"{name}: execution_order = {new_order}", fg="cyan")


__all__ = ["qa_gates_group"]
