"""`poindexter alerts` — operate on the declarative alert_rules table.

Thin adapter over :mod:`services.declarative_config_service` (surface
``"alerts"``, poindexter#848) — mirrors ``poindexter retention``'s shape.
Rows here are read by ``brain/alert_sync.py`` (GH-28) and pushed to
Grafana's native alert-rule provisioning API every
``grafana_alert_sync_interval_cycles`` brain cycles (default 15 min).

``promql_query`` may be PromQL (routed to the ``local-prometheus``
datasource) or a raw SQL ``SELECT`` (routed to ``local-brain-db``) — the
latter is what this surface can do that the static Prometheus rule files
under ``infrastructure/prometheus/alerts/`` can't: alert on arbitrary
Postgres conditions (business tables, not just exported metrics).

New rules default to ``enabled=false`` — review with ``poindexter alerts
show`` and ``poindexter alerts sync`` (a one-off local dry run against
your own eyes, not Grafana) before flipping them on, since ``enable``
starts paging on the next sync cycle.
"""

from __future__ import annotations

import json
import sys

import click

from poindexter.cli._dataplane import dump_row, render_table, run_service
from services import declarative_config_service as dcs

_SURFACE = "alerts"

_COLUMNS = [
    ("name", "NAME", 28, None),
    ("severity", "SEVERITY", 9, None),
    ("threshold", "THRESHOLD", 10, None),
    ("duration", "FOR", 6, None),
    ("enabled", "STATE", 9, lambda v: "enabled" if v else "disabled"),
]


def _state_filter(state: str) -> dict | None:
    if state == "enabled":
        return {"enabled": True}
    if state == "disabled":
        return {"enabled": False}
    return None


@click.group(
    name="alerts",
    help="Manage DB-driven Grafana alert rules (declarative lifecycle management).",
)
def alerts_group() -> None:
    pass


@alerts_group.command("list")
@click.option(
    "--state", default="",
    type=click.Choice(["", "enabled", "disabled"]),
    help="Filter by enabled state.",
)
def alerts_list(state: str) -> None:
    """List every alert rule with current status."""
    try:
        rows = run_service(lambda p: dcs.list_rows(p, _SURFACE, filters=_state_filter(state)))
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)
    render_table(rows, _COLUMNS, empty="(no alert rules — see `poindexter alerts create --help`)")


@alerts_group.command("show")
@click.argument("name")
def alerts_show(name: str) -> None:
    """Show full details of one rule including the query + labels/annotations."""
    try:
        row = run_service(lambda p: dcs.get_row(p, _SURFACE, name))
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)
    if not dump_row(row, missing=f"(no alert rule named {name!r})"):
        sys.exit(1)


def _parse_kv_pairs(pairs: tuple[str, ...], *, opt_name: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for pair in pairs:
        if "=" not in pair:
            raise click.BadParameter(
                f"expected KEY=VALUE, got {pair!r}", param_hint=opt_name
            )
        k, _, v = pair.partition("=")
        out[k.strip()] = v.strip()
    return out


@alerts_group.command("create")
@click.argument("name")
@click.option("--query", "-q", required=True, help="PromQL expression, or a raw SQL SELECT.")
@click.option("--threshold", required=True, type=float, help="Alert fires when the query result is greater than this.")
@click.option("--duration", default="5m", show_default=True, help="Grafana 'for' duration (e.g. 5m, 1h) the condition must hold before firing.")
@click.option("--severity", default="warning", show_default=True, help="Severity label (e.g. warning, critical) — routed by Alertmanager.")
@click.option("--label", "labels", multiple=True, metavar="KEY=VALUE", help="Extra label (repeatable).")
@click.option("--annotation", "annotations", multiple=True, metavar="KEY=VALUE", help="Extra annotation, e.g. summary=... (repeatable).")
@click.option("--enabled/--disabled", default=False, show_default=True, help="New rules start disabled — review before it can page.")
def alerts_create(
    name: str,
    query: str,
    threshold: float,
    duration: str,
    severity: str,
    labels: tuple[str, ...],
    annotations: tuple[str, ...],
    enabled: bool,
) -> None:
    """Create or update an alert rule (upsert, keyed on NAME).

    \b
    Examples:
        poindexter alerts create stuck-approvals \\
            --query "SELECT count(*) FROM pipeline_tasks WHERE status='awaiting_approval' AND updated_at < now() - interval '48 hours'" \\
            --threshold 0 --duration 30m --severity warning \\
            --annotation summary="Posts have sat awaiting approval for 48h+"
    """
    payload = {
        "name": name,
        "promql_query": query,
        "threshold": threshold,
        "duration": duration,
        "severity": severity,
        "enabled": enabled,
        "labels_json": _parse_kv_pairs(labels, opt_name="--label"),
        "annotations_json": _parse_kv_pairs(annotations, opt_name="--annotation"),
    }
    try:
        row = run_service(lambda p: dcs.upsert_row(p, _SURFACE, payload))
    except dcs.SurfaceValidationError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)
    state = "enabled" if row["enabled"] else "disabled (review, then `poindexter alerts enable`)"
    click.secho(f"{name}: saved — {state}", fg="green" if row["enabled"] else "yellow")


@alerts_group.command("enable")
@click.argument("name")
def alerts_enable(name: str) -> None:
    """Flip enabled=TRUE for one rule — takes effect on the next brain sync cycle."""
    _set_enabled(name, True)


@alerts_group.command("disable")
@click.argument("name")
def alerts_disable(name: str) -> None:
    """Flip enabled=FALSE for one rule."""
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
        click.echo(f"(no alert rule named {name!r})", err=True)
        sys.exit(1)
    state = "enabled" if enabled else "disabled"
    click.secho(f"{name}: {state}", fg="green" if enabled else "yellow")


@alerts_group.command("delete")
@click.argument("name")
def alerts_delete(name: str) -> None:
    """Delete one alert rule. Does not retract it from Grafana — it's orphaned
    there until the next full resync overwrites the folder, or delete it by
    hand in the Grafana UI."""
    try:
        deleted = run_service(lambda p: dcs.delete_row(p, _SURFACE, name))
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)
    if not deleted:
        click.echo(f"(no alert rule named {name!r})", err=True)
        sys.exit(1)
    click.secho(f"{name}: deleted", fg="yellow")


@alerts_group.command("sync")
def alerts_sync() -> None:
    """Push every enabled rule to Grafana immediately (what the brain daemon
    does every grafana_alert_sync_interval_cycles cycles)."""
    async def _impl(pool):
        from brain.alert_sync import sync_alert_rules

        return await sync_alert_rules(pool)

    try:
        summary = run_service(_impl)
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)
    click.echo(json.dumps(summary, indent=2, default=str))
