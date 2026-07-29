"""``poindexter logs`` — read the Loki log stream from the terminal.

A thin adapter over ``GET /api/logs`` (the same Loki proxy the operator
console's Telemetry tab uses), so the CLI, the console, and the MCP server all
read one implementation. No LogQL knowledge required for the common cases:
``--service`` and ``--level`` build the selector server-side.

Read-only by construction — Loki is queried, never written — which is what
makes ``--follow`` safe to run unattended and safe to record as demo footage
(see ``services/demo_clips.py``).

``--follow`` polls rather than holding a streaming connection. Loki's
``query_range`` is a point-in-time query, and the endpoint returns newest-first
with a look-back window; polling with timestamp de-duplication gives a true
tail without needing a websocket the API does not expose.
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from typing import Any

import click

from ._api_client import WorkerClient
from ._status_style import ACTIVE, FAILURE, INACTIVE, NEUTRAL, SUCCESS

# Loki level label -> the shared colourblind-safe roles. Levels are the
# canonical case-insensitive spellings ``normalize_level`` emits.
_LEVEL_COLOR: dict[str, str] = {
    "error": FAILURE,
    "critical": FAILURE,
    "fatal": FAILURE,
    "warn": ACTIVE,
    "warning": ACTIVE,
    "info": SUCCESS,
    "debug": INACTIVE,
    "trace": INACTIVE,
}

# Poll cadence for --follow. Deliberately not configurable per-invocation: the
# endpoint is a range query, so a tighter loop re-fetches the same window
# without surfacing anything new, it just costs Loki more.
_FOLLOW_INTERVAL_S = 2.0


def _run(coro):
    return asyncio.run(coro)


async def _fetch(params: dict[str, Any]) -> dict[str, Any]:
    async with WorkerClient() as c:
        resp = await c.get("/api/logs", params=params)
        return await c.json_or_raise(resp)


def _print_line(row: dict[str, Any], *, show_service: bool) -> None:
    level = (row.get("level") or "").lower()
    ts = (row.get("ts") or "")[11:19] or "--:--:--"
    prefix = f"{ts}"
    if show_service:
        prefix += f"  {(row.get('service') or '-')[:22]:<22}"
    click.secho(f"{prefix}  {level.upper():<5}", fg=_LEVEL_COLOR.get(level, NEUTRAL), nl=False)
    click.secho(f"  {row.get('line', '')}", fg=NEUTRAL)


@click.command(name="logs")
@click.option("--service", default="", help="Filter by the `service` Loki label.")
@click.option("--level", default="", help="Filter by level (error/warn/info/debug).")
@click.option("--since", default="1h", show_default=True, help="Look-back window (e.g. 30m, 6h).")
@click.option("--limit", type=int, default=50, show_default=True, help="Max lines (1-1000).")
@click.option("--query", default="", help="Raw LogQL selector; overrides --service/--level.")
@click.option("--follow", "-f", is_flag=True, help="Poll for new lines until interrupted.")
@click.option("--json", "as_json", is_flag=True, help="Machine-readable output.")
def logs_command(
    service: str, level: str, since: str, limit: int,
    query: str, follow: bool, as_json: bool,
) -> None:
    """Tail or search the log stream.

    Examples:

        poindexter logs --level error --since 6h

        poindexter logs --service poindexter-worker --follow
    """
    params: dict[str, Any] = {"since": since, "limit": limit}
    for key, value in (("service", service), ("level", level), ("query", query)):
        if value:
            params[key] = value

    try:
        payload = _run(_fetch(params))
    except RuntimeError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)

    lines = payload.get("lines") or []

    if as_json:
        click.echo(json.dumps(payload, indent=2, default=str))
        return

    if not lines:
        click.echo(f"(no log lines in the last {since})")
        return

    stats = payload.get("stats") or {}
    click.secho(
        f"Logs: {stats.get('count', len(lines))} lines  {stats.get('query', '')}",
        fg=SUCCESS,
    )
    click.echo()

    # The API returns newest-first; a terminal reads oldest-at-top like a tail.
    show_service = not service
    for row in reversed(lines):
        _print_line(row, show_service=show_service)

    if not follow:
        return

    # Follow: re-query on an interval, printing only timestamps not yet shown.
    # Bounded memory — only the last window's timestamps need remembering,
    # since anything older can no longer reappear in a newer range query.
    seen = {row.get("ts") for row in lines}
    try:
        while True:
            time.sleep(_FOLLOW_INTERVAL_S)
            try:
                payload = _run(_fetch(params))
            except RuntimeError as e:
                click.secho(f"  (poll failed: {e})", fg=ACTIVE, err=True)
                continue
            fresh = [r for r in (payload.get("lines") or []) if r.get("ts") not in seen]
            for row in reversed(fresh):
                _print_line(row, show_service=show_service)
                seen.add(row.get("ts"))
            if len(seen) > 5000:
                seen = {r.get("ts") for r in (payload.get("lines") or [])}
    except KeyboardInterrupt:
        click.echo()
        click.secho("(stopped)", fg=INACTIVE)
