"""`poindexter retention` — operate on the declarative retention_policies table.

Thin adapter over :mod:`services.declarative_config_service` (#1522, epic
#1340): list / show / enable / disable read & write config through the
service; ``run`` delegates to :func:`services.integrations.retention_runner.run_all`.
The ``--dry-run`` toggle flips ``config.dry_run`` for this invocation via the
service (set → run → revert), so no raw SQL or asyncpg connection lives here.
"""

from __future__ import annotations

import json
import sys

import click

from poindexter.cli._dataplane import dump_row, fmt_age, render_table, run_service
from services import declarative_config_service as dcs

_SURFACE = "retention"

_COLUMNS = [
    ("name", "NAME", 32, None),
    ("handler_name", "HANDLER", 13, None),
    ("table_name", "TABLE", 18, None),
    ("ttl_days", "TTL", 5, lambda v: str(v) if v is not None else "—"),
    ("enabled", "STATE", 9, lambda v: "enabled" if v else "disabled"),
    ("last_run_at", "LAST RUN", 12, fmt_age),
    ("total_runs", "RUNS", 5, None),
    ("total_deleted", "DELETED", 8, None),
]


def _state_filter(state: str) -> dict | None:
    if state == "enabled":
        return {"enabled": True}
    if state == "disabled":
        return {"enabled": False}
    return None


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
    try:
        rows = run_service(lambda p: dcs.list_rows(p, _SURFACE, filters=_state_filter(state)))
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)
    render_table(rows, _COLUMNS, empty="(no retention policies)")


@retention_group.command("show")
@click.argument("name")
def retention_show(name: str) -> None:
    """Show full details of one policy including config + rule JSONB."""
    try:
        row = run_service(lambda p: dcs.get_row(p, _SURFACE, name))
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)
    if not dump_row(row, missing=f"(no retention policy named {name!r})"):
        sys.exit(1)


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
        click.echo(f"(no policy named {name!r})", err=True)
        sys.exit(1)
    state = "enabled" if enabled else "disabled"
    click.secho(f"{name}: {state}", fg="green" if enabled else "yellow")


async def _set_dry_run(pool, targets: list[str], on: bool) -> None:
    """Toggle ``config.dry_run`` on each named policy via the service."""
    for name in targets:
        row = await dcs.get_row(pool, _SURFACE, name)
        if row is None:
            continue
        config = dict(row.get("config") or {})
        if on:
            config["dry_run"] = True
        else:
            config.pop("dry_run", None)
        await dcs.upsert_row(pool, _SURFACE, {**row, "config": config})


@retention_group.command("run")
@click.argument("name", required=False)
@click.option("--dry-run", is_flag=True, help="Handlers that support it will count without deleting.")
def retention_run(name: str | None, dry_run: bool) -> None:
    """Invoke the retention runner immediately.

    Without NAME: runs every enabled policy.
    With NAME: runs just that policy (requires enabled=TRUE).

    --dry-run temporarily sets config.dry_run=true on the matched policies for
    this invocation only (set → run → revert). The flag is NOT persisted.
    """
    async def _impl(pool):
        from services.integrations import retention_runner

        targets: list[str] = []
        if dry_run:
            if name:
                targets = [name]
            else:
                rows = await dcs.list_rows(pool, _SURFACE, filters={"enabled": True})
                targets = [r["name"] for r in rows]
            await _set_dry_run(pool, targets, on=True)
        try:
            return await retention_runner.run_all(
                pool, only_names=[name] if name else None,
            )
        finally:
            if dry_run:
                await _set_dry_run(pool, targets, on=False)

    try:
        summary = run_service(_impl)
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)

    click.echo(json.dumps(summary.to_dict(), indent=2, default=str))
