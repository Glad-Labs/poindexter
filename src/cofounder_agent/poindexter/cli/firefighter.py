"""``poindexter firefighter`` — operate the self-healing firefighter.

The firefighter matches an about-to-page alert against the ``remediation_rules``
table and runs an allowlisted action (restart a container, re-run the stuck-task
sweep) before paging. Rules are **operational runtime state** — you add, tune,
and retire them one alert at a time — so they live only in the DB, managed here:

    poindexter firefighter rule list [--state enabled|disabled]
    poindexter firefighter rule show (RULE_ID | --alert NAME)
    poindexter firefighter rule add --action restart_container \\
        --alert PyroscopeDown --param container=poindexter-pyroscope \\
        [--match REGEX] [--description ...] [--max-attempts N] \\
        [--window-minutes N] [--verify-after N] [--disabled]
    poindexter firefighter rule rm (RULE_ID | --alert NAME)
    poindexter firefighter rule enable  (RULE_ID | --alert NAME)
    poindexter firefighter rule disable (RULE_ID | --alert NAME)

Thin adapter over :mod:`services.remediation_rules_service` (epic #1340) — the
service owns every query; no raw SQL or asyncpg connection lives here. The brain
re-reads rules each 30s dispatch cycle, so a change is live within one cycle
(no restart).
"""

from __future__ import annotations

import sys

import click

from poindexter.cli._dataplane import dump_row, render_table, run_service
from services import remediation_rules_service as svc

_COLUMNS = [
    ("id", "ID", 5, None),
    ("alertname", "ALERTNAME", 26, lambda v: v if v else "—"),
    ("match_regex", "MATCH", 22, lambda v: v if v else "—"),
    ("action_name", "ACTION", 20, None),
    ("enabled", "STATE", 9, lambda v: "enabled" if v else "disabled"),
    ("description", "DESCRIPTION", 44, None),
]


@click.group(
    name="firefighter",
    help="Operate the self-healing firefighter (remediation_rules policy).",
)
def firefighter_group() -> None:
    """Root for ``poindexter firefighter ...`` commands."""


@firefighter_group.group(name="rule", help="Manage firefighter remediation rules.")
def rule_group() -> None:
    """Root for ``poindexter firefighter rule ...`` commands."""


def _selector(rule_id: int | None, alert: str) -> dict:
    """Resolve the RULE_ID argument / ``--alert`` option to a service selector.

    Exactly one must be given (mirrors the service's XOR guard); anything else
    exits 1 with a usage hint before any DB call."""
    if (rule_id is None) == (not alert):
        click.echo(
            "Error: specify exactly one of RULE_ID (argument) or --alert NAME",
            err=True,
        )
        sys.exit(1)
    return {"rule_id": rule_id} if rule_id is not None else {"alertname": alert}


def _parse_params(pairs: tuple[str, ...]) -> dict[str, str]:
    """Turn repeated ``KEY=VALUE`` ``--param`` options into a dict."""
    out: dict[str, str] = {}
    for pair in pairs:
        if "=" not in pair:
            raise ValueError(f"--param must be KEY=VALUE, got {pair!r}")
        key, value = pair.split("=", 1)
        if not key:
            raise ValueError(f"--param needs a non-empty key, got {pair!r}")
        out[key] = value
    return out


@rule_group.command("list")
@click.option(
    "--state", default="",
    type=click.Choice(["", "enabled", "disabled"]),
    help="Filter by enabled state.",
)
def rule_list(state: str) -> None:
    """List every remediation rule, ordered by id."""
    enabled = True if state == "enabled" else False if state == "disabled" else None
    try:
        rows = run_service(lambda p: svc.list_rules(p, enabled=enabled))
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)
    render_table(
        rows, _COLUMNS,
        empty="(no remediation_rules — the firefighter is inert; add one with "
        "'poindexter firefighter rule add')",
    )


@rule_group.command("show")
@click.argument("rule_id", type=int, required=False)
@click.option("--alert", default="", help="Identify the rule by alertname instead of id.")
def rule_show(rule_id: int | None, alert: str) -> None:
    """Show full details of a single rule."""
    sel = _selector(rule_id, alert)
    try:
        row = run_service(lambda p: svc.get_rule(p, **sel))
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)
    if not dump_row(row, missing="(no matching remediation_rules row)"):
        sys.exit(1)


@rule_group.command("add")
@click.option(
    "--action", "action_name", required=True,
    help="Action executor (restart_container | run_auto_remediate).",
)
@click.option("--alert", "alertname", default="", help="Exact alertname to match.")
@click.option(
    "--match", "match_regex", default="",
    help="Regex over alertname/fingerprint (for rules without an exact alertname).",
)
@click.option(
    "--param", "params", multiple=True, metavar="KEY=VALUE",
    help="Action param (repeatable), e.g. --param container=poindexter-pyroscope.",
)
@click.option("--description", default="", help="Human note shown in list / show.")
@click.option(
    "--max-attempts", type=int, default=None,
    help="Circuit-breaker: max actions per window for this rule (else the global default).",
)
@click.option(
    "--window-minutes", type=int, default=None,
    help="Circuit-breaker window for this rule (else the global default).",
)
@click.option(
    "--verify-after", type=int, default=None,
    help="Grace seconds before the verify scan (else the global default).",
)
@click.option("--disabled", is_flag=True, help="Create the rule disabled (default: enabled).")
def rule_add(
    action_name: str,
    alertname: str,
    match_regex: str,
    params: tuple[str, ...],
    description: str,
    max_attempts: int | None,
    window_minutes: int | None,
    verify_after: int | None,
    disabled: bool,
) -> None:
    """Add a remediation rule. Fails loud on an unknown action, a missing
    alertname/--match, or a restart_container rule without a container."""
    try:
        param_dict = _parse_params(params)
    except ValueError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)
    try:
        row = run_service(lambda p: svc.add_rule(
            p,
            action_name=action_name,
            alertname=alertname or None,
            match_regex=match_regex or None,
            params=param_dict,
            description=description,
            max_attempts_per_window=max_attempts,
            window_minutes=window_minutes,
            verify_after_seconds=verify_after,
            enabled=not disabled,
        ))
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)
    match = row.get("alertname") or row.get("match_regex")
    click.secho(
        f"added rule #{row['id']}: {match} → {row['action_name']}", fg="green"
    )


@rule_group.command("rm")
@click.argument("rule_id", type=int, required=False)
@click.option("--alert", default="", help="Identify the rule by alertname instead of id.")
def rule_rm(rule_id: int | None, alert: str) -> None:
    """Delete a rule by id or alertname. This sticks — nothing re-seeds it."""
    sel = _selector(rule_id, alert)
    try:
        row = run_service(lambda p: svc.remove_rule(p, **sel))
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)
    if row is None:
        click.echo("(no matching remediation_rules row)", err=True)
        sys.exit(1)
    match = row.get("alertname") or row.get("match_regex")
    click.secho(f"removed rule #{row['id']}: {match}", fg="yellow")


@rule_group.command("enable")
@click.argument("rule_id", type=int, required=False)
@click.option("--alert", default="", help="Identify the rule by alertname instead of id.")
def rule_enable(rule_id: int | None, alert: str) -> None:
    """Enable a rule — the firefighter acts on it within one dispatch cycle."""
    _set_enabled(rule_id, alert, True)


@rule_group.command("disable")
@click.argument("rule_id", type=int, required=False)
@click.option("--alert", default="", help="Identify the rule by alertname instead of id.")
def rule_disable(rule_id: int | None, alert: str) -> None:
    """Disable a rule without deleting it — it stops acting, the row stays."""
    _set_enabled(rule_id, alert, False)


def _set_enabled(rule_id: int | None, alert: str, enabled: bool) -> None:
    sel = _selector(rule_id, alert)
    try:
        row = run_service(lambda p: svc.set_rule_enabled(p, enabled=enabled, **sel))
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)
    if row is None:
        click.echo("(no matching remediation_rules row)", err=True)
        sys.exit(1)
    state = "enabled" if enabled else "disabled"
    click.secho(f"rule #{row['id']}: {state}", fg="green" if enabled else "yellow")


__all__ = ["firefighter_group"]
