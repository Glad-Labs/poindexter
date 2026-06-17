"""`poindexter taps` — manage the external_taps table (GH-103).

Thin adapter over :mod:`services.declarative_config_service` (#1522, epic
#1340): list / show / enable / disable read & write config through the
service; `run` delegates to the tap_runner. No raw SQL or asyncpg connection
lives here — the shared ``_dataplane`` helper owns the pool lifecycle.
"""

from __future__ import annotations

import json
import sys

import click

from poindexter.cli._dataplane import dump_row, fmt_age, render_table, run_service
from services import declarative_config_service as dcs

_SURFACE = "taps"

_COLUMNS = [
    ("name", "NAME", 18, None),
    ("handler_name", "HANDLER", 22, None),
    ("tap_type", "TAP TYPE", 12, None),
    ("schedule", "SCHEDULE", 17, None),
    ("enabled", "STATE", 9, lambda v: "enabled" if v else "disabled"),
    ("last_run_at", "LAST", 12, fmt_age),
    ("total_runs", "RUNS", 5, None),
    ("total_records", "RECORDS", 8, None),
]


def _state_filter(state: str) -> dict | None:
    if state == "enabled":
        return {"enabled": True}
    if state == "disabled":
        return {"enabled": False}
    return None


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
    try:
        rows = run_service(lambda p: dcs.list_rows(p, _SURFACE, filters=_state_filter(state)))
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)
    render_table(rows, _COLUMNS, empty="(no external taps)")


@taps_group.command("show")
@click.argument("name")
def taps_show(name: str) -> None:
    """Show full details of one tap row."""
    try:
        row = run_service(lambda p: dcs.get_row(p, _SURFACE, name))
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)
    if not dump_row(row, missing=f"(no tap named {name!r})"):
        sys.exit(1)


@taps_group.command("enable")
@click.argument("name")
def taps_enable(name: str) -> None:
    _set_enabled(name, True)


@taps_group.command("disable")
@click.argument("name")
def taps_disable(name: str) -> None:
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
    async def _impl(pool):
        from services.integrations import tap_runner
        return await tap_runner.run_all(pool, only_names=[name] if name else None)

    try:
        summary = run_service(_impl)
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)

    click.echo(json.dumps(summary.to_dict(), indent=2, default=str))
