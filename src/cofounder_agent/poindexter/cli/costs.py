"""`poindexter costs` subcommands — budget + operational metrics."""

from __future__ import annotations

import asyncio
import json
import sys

import click

from ._api_client import WorkerClient


def _run(coro):
    return asyncio.run(coro)


@click.group(name="costs", help="Pipeline spending and operational metrics.")
def costs_group() -> None:
    pass


@costs_group.command("budget")
@click.option("--json", "json_output", is_flag=True)
def costs_budget(json_output: bool) -> None:
    """Month-to-date spend vs. configured monthly budget."""

    async def _budget():
        async with WorkerClient() as c:
            resp = await c.get("/api/metrics/costs/budget")
            return await c.json_or_raise(resp)

    try:
        d = _run(_budget())
    except RuntimeError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)

    if json_output:
        click.echo(json.dumps(d, indent=2, default=str))
        return

    status = d.get("status", "?")
    status_color = {"healthy": "green", "warning": "yellow", "critical": "red"}.get(
        status, "white"
    )
    click.secho(f"Status: {status}", fg=status_color, bold=True)
    click.echo(f"  monthly_budget     ${d.get('monthly_budget', '?')}")
    click.echo(f"  amount_spent       ${d.get('amount_spent', '?')}")
    click.echo(f"  amount_remaining   ${d.get('amount_remaining', '?')}")
    click.echo(f"  percent_used       {d.get('percent_used', '?')}%")
    click.echo(f"  daily_burn_rate    ${d.get('daily_burn_rate', '?')}")
    click.echo(f"  projected_final    ${d.get('projected_final_cost', '?')}")
    click.echo(f"  days_remaining     {d.get('days_remaining', '?')}")
    alerts = d.get("alerts") or []
    if alerts:
        click.secho(f"  alerts: {', '.join(alerts)}", fg="yellow")


@costs_group.command("operational")
@click.option("--json", "json_output", is_flag=True)
def costs_operational(json_output: bool) -> None:
    """Task counts, worker state, websocket connections."""

    async def _op():
        async with WorkerClient() as c:
            resp = await c.get("/api/metrics/operational")
            return await c.json_or_raise(resp)

    try:
        d = _run(_op())
    except RuntimeError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)

    if json_output:
        click.echo(json.dumps(d, indent=2, default=str))
        return

    click.secho("Operational Metrics", fg="cyan", bold=True)
    click.echo(f"  uptime_seconds     {d.get('uptime_seconds', '?')}")

    q = d.get("task_queue") or {}
    click.echo()
    click.secho("  task_queue", fg="cyan")
    for key in ("pending", "in_progress", "failed", "completed"):
        val = q.get(key)
        if val is not None:
            color = "yellow" if key == "pending" and val else None
            line = f"    {key:<15} {val}"
            if color:
                click.secho(line, fg=color)
            else:
                click.echo(line)

    e = d.get("executor") or {}
    click.echo()
    click.secho("  executor", fg="cyan")
    running = e.get("is_running")
    running_color = "green" if running else "red"
    click.secho(f"    is_running       {running}", fg=running_color)
    click.echo(f"    task_count       {e.get('task_count', '?')}")
    click.echo(f"    success_count    {e.get('success_count', '?')}")
    click.echo(f"    error_count      {e.get('error_count', '?')}")

    ws = d.get("websocket_connections")
    if ws is not None:
        click.echo()
        click.echo(f"  websocket_connections: {ws}")
