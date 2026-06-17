"""`poindexter webhooks` — operate on the declarative webhook_endpoints table.

Thin adapter over :mod:`services.declarative_config_service` (#1522, epic
#1340): list / show / enable / disable read & write config through the
service. ``set-secret`` resolves the row's ``secret_key_ref`` via the service,
then hands a connection to :mod:`plugins.secrets` (which owns the encrypted
pgcrypto write). No raw config SQL or asyncpg connection lives here.
"""

from __future__ import annotations

import sys

import click

from poindexter.cli._dataplane import dump_row, fmt_age, render_table, run_service
from services import declarative_config_service as dcs

_SURFACE = "webhooks"

_COLUMNS = [
    ("name", "NAME", 22, None),
    ("direction", "DIR", 9, None),
    ("handler_name", "HANDLER", 26, None),
    ("signing_algorithm", "ALGO", 14, None),
    ("enabled", "STATE", 9, lambda v: "enabled" if v else "disabled"),
    ("secret_key_ref", "SEC?", 5, lambda v: "yes" if v else "—"),
    ("last_success_at", "LAST OK", 12, fmt_age),
    ("total_success", "OK", 5, None),
    ("total_failure", "FAIL", 5, None),
]


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
    filters: dict = {}
    if direction:
        filters["direction"] = direction
    if state == "enabled":
        filters["enabled"] = True
    elif state == "disabled":
        filters["enabled"] = False
    try:
        rows = run_service(lambda p: dcs.list_rows(p, _SURFACE, filters=filters or None))
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)
    render_table(rows, _COLUMNS, empty="(no webhook endpoints)")


@webhooks_group.command("show")
@click.argument("name")
def webhooks_show(name: str) -> None:
    """Show full details of one endpoint including config + metadata."""
    try:
        row = run_service(lambda p: dcs.get_row(p, _SURFACE, name))
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)
    if not dump_row(row, missing=f"(no webhook endpoint named {name!r})"):
        sys.exit(1)


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

    Resolves the endpoint's ``secret_key_ref`` via the service, then stores
    the value there via the encrypted-at-rest pgcrypto path. If
    ``secret_key_ref`` is NULL, refuses — set it first
    (``poindexter webhooks ... `` upsert, or the HTTP PUT).
    """
    if value is None:
        value = click.prompt(
            f"Secret for {name}", hide_input=True, confirmation_prompt=True
        )
    assert value is not None

    async def _impl(pool):
        row = await dcs.get_row(pool, _SURFACE, name)
        if row is None:
            raise RuntimeError(f"{name!r}: no such webhook endpoint")
        ref = row.get("secret_key_ref")
        if not ref:
            raise RuntimeError(
                f"{name!r}: no secret_key_ref on the row. "
                "Set it first so the secret has somewhere to go."
            )
        # plugins.secrets owns the encrypted write; we just hand it a conn.
        from plugins.secrets import ensure_pgcrypto, set_secret

        async with pool.acquire() as conn:
            await ensure_pgcrypto(conn)
            await set_secret(
                conn, ref, value,
                description=f"Webhook signing secret for endpoint {name!r}",
            )
        return ref

    try:
        ref = run_service(_impl)
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)
    click.secho(
        f"Secret stored encrypted at app_settings.{ref} for webhook {name!r}",
        fg="green",
    )
