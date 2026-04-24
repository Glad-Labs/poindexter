"""`poindexter webhooks` — operate on the declarative webhook_endpoints table.

Minimal v1 per the RFC open-question decision (list / enable / disable /
set-secret / show). Full CRUD (add / remove / reassign handler) lands
in v1.1 once the seed flow has been exercised enough to know the
ergonomics.

All commands go directly to the DB via the same DSN resolution pattern
as `poindexter settings`. No HTTP layer — the table is small, operator-
only, and doesn't need the worker roundtrip.
"""

from __future__ import annotations

import asyncio
import os
import sys
from typing import Any

import click


def _dsn() -> str:
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


async def _connect():
    import asyncpg
    return await asyncpg.connect(_dsn())


def _run(coro):
    return asyncio.run(coro)


def _format_age(ts: Any) -> str:
    if ts is None:
        return "—"
    import datetime as _dt
    now = _dt.datetime.now(_dt.timezone.utc)
    delta = now - ts
    secs = int(delta.total_seconds())
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
    name="webhooks",
    help="Manage inbound and outbound webhook endpoints (declarative framework).",
)
def webhooks_group() -> None:
    pass


@webhooks_group.command("list")
@click.option(
    "--direction", default="",
    type=click.Choice(["", "inbound", "outbound"]),
    help="Filter by direction.",
)
@click.option(
    "--state", default="",
    type=click.Choice(["", "enabled", "disabled"]),
    help="Filter by enabled state.",
)
def webhooks_list(direction: str, state: str) -> None:
    """List all webhook endpoints with current status."""
    async def _run_list():
        conn = await _connect()
        try:
            where_clauses: list[str] = []
            args: list[Any] = []
            if direction:
                where_clauses.append(f"direction = ${len(args) + 1}")
                args.append(direction)
            if state == "enabled":
                where_clauses.append("enabled = TRUE")
            elif state == "disabled":
                where_clauses.append("enabled = FALSE")
            where = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""
            rows = await conn.fetch(
                f"""
                SELECT name, direction, handler_name, signing_algorithm,
                       enabled, secret_key_ref,
                       last_success_at, last_failure_at,
                       total_success, total_failure, last_error
                  FROM webhook_endpoints
                  {where}
              ORDER BY direction, name
                """,
                *args,
            )
            return [dict(r) for r in rows]
        finally:
            await conn.close()

    try:
        rows = _run(_run_list())
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)

    if not rows:
        click.echo("(no webhook endpoints)")
        return

    click.echo(
        f"{'NAME':<22} {'DIR':<9} {'HANDLER':<26} {'ALGO':<14} "
        f"{'STATE':<9} {'SEC?':<5} {'LAST OK':<12} {'OK':<5} {'FAIL':<5}"
    )
    for r in rows:
        sec = "yes" if r["secret_key_ref"] else "—"
        state_txt = "enabled" if r["enabled"] else "disabled"
        color = "green" if r["enabled"] else "yellow"
        line = (
            f"{r['name']:<22} {r['direction']:<9} {r['handler_name']:<26} "
            f"{r['signing_algorithm']:<14} {state_txt:<9} {sec:<5} "
            f"{_format_age(r['last_success_at']):<12} "
            f"{r['total_success']:<5} {r['total_failure']:<5}"
        )
        click.secho(line, fg=color)


@webhooks_group.command("show")
@click.argument("name")
def webhooks_show(name: str) -> None:
    """Show full details of one endpoint including config + metadata."""
    async def _run_show():
        conn = await _connect()
        try:
            row = await conn.fetchrow(
                "SELECT * FROM webhook_endpoints WHERE name = $1", name
            )
            return dict(row) if row else None
        finally:
            await conn.close()

    try:
        row = _run(_run_show())
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)

    if not row:
        click.echo(f"(no webhook endpoint named {name!r})", err=True)
        sys.exit(1)

    for key in (
        "name", "direction", "handler_name", "path", "url",
        "signing_algorithm", "secret_key_ref", "enabled",
        "last_success_at", "last_failure_at",
        "total_success", "total_failure", "last_error",
        "event_filter", "config", "metadata",
        "created_at", "updated_at",
    ):
        click.echo(f"  {key:<20} {row[key]!r}")


@webhooks_group.command("enable")
@click.argument("name")
def webhooks_enable(name: str) -> None:
    """Flip enabled=TRUE for one endpoint."""
    _set_enabled(name, True)


@webhooks_group.command("disable")
@click.argument("name")
def webhooks_disable(name: str) -> None:
    """Flip enabled=FALSE for one endpoint."""
    _set_enabled(name, False)


def _set_enabled(name: str, enabled: bool) -> None:
    async def _run_update():
        conn = await _connect()
        try:
            result = await conn.execute(
                "UPDATE webhook_endpoints SET enabled = $2 WHERE name = $1",
                name, enabled,
            )
            return result
        finally:
            await conn.close()

    try:
        result = _run(_run_update())
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)

    # asyncpg execute returns a status string like "UPDATE 1"
    if result.endswith(" 0"):
        click.echo(f"(no row named {name!r})", err=True)
        sys.exit(1)
    state = "enabled" if enabled else "disabled"
    click.secho(f"{name}: {state}", fg="green" if enabled else "yellow")


@webhooks_group.command("set-secret")
@click.argument("name")
@click.option(
    "--value", default=None,
    help="Provide the secret inline. Omit to be prompted (recommended).",
)
def webhooks_set_secret(name: str, value: str | None) -> None:
    """Store an encrypted signing secret for the endpoint.

    Looks up the endpoint's ``secret_key_ref`` column, then stores the
    provided value there via the encrypted-at-rest pgcrypto path. If
    ``secret_key_ref`` is NULL, this command refuses — set it first
    via direct SQL (``UPDATE webhook_endpoints SET secret_key_ref=...``).
    """
    if value is None:
        value = click.prompt(
            f"Secret for {name}", hide_input=True, confirmation_prompt=True
        )
    assert value is not None

    async def _run_set():
        conn = await _connect()
        try:
            ref = await conn.fetchval(
                "SELECT secret_key_ref FROM webhook_endpoints WHERE name = $1",
                name,
            )
            if not ref:
                raise RuntimeError(
                    f"{name!r}: no secret_key_ref on the row. "
                    "Set it first so the secret has somewhere to go."
                )
            # Route through plugins.secrets.set_secret for pgcrypto encryption.
            # Import is deferred to avoid loading the module for commands
            # that don't need it.
            from plugins.secrets import ensure_pgcrypto, set_secret
            await ensure_pgcrypto(conn)
            await set_secret(
                conn, ref, value,
                description=f"Webhook signing secret for endpoint {name!r}",
            )
            return ref
        finally:
            await conn.close()

    try:
        ref = _run(_run_set())
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)
    click.secho(
        f"Secret stored encrypted at app_settings.{ref} for webhook {name!r}",
        fg="green",
    )
