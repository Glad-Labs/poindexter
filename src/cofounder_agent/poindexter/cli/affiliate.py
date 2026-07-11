"""`poindexter affiliate` — manage affiliate-link rows (DB-only real URLs).

Thin adapter over ``modules.content.affiliate_links`` (no inline SQL here, per
the transport-adapter-contract ADR). Real referral URLs never live in source;
operators add them here.
"""
from __future__ import annotations

import asyncio

import click


async def _connect():
    import asyncpg
    from brain import bootstrap

    dsn = bootstrap.resolve_database_url()
    if not dsn:
        raise click.ClickException("No database_url — run `poindexter setup` first.")
    return await asyncpg.connect(dsn, timeout=8)


@click.group(name="affiliate")
def affiliate_group() -> None:
    """Manage affiliate links (add/list/enable/disable/rm)."""


@affiliate_group.command(name="add")
@click.option("--code", required=True, help="Stable slug for /go/<code> (e.g. mercury).")
@click.option("--keyword", required=True, help="Body keyword to match (e.g. Mercury).")
@click.option("--url", required=True, help="Real merchant/referral URL.")
@click.option("--display-text", default="", help="Link text (defaults to keyword).")
@click.option("--program", default="", help="Program label (e.g. 'Mercury Referral').")
def add_cmd(code, keyword, url, display_text, program):
    """Add or update an affiliate link."""
    from modules.content.affiliate_links import add_link

    async def _go():
        conn = await _connect()
        try:
            await add_link(
                conn, code=code, keyword=keyword, url=url,
                display_text=display_text, program=program,
            )
        finally:
            await conn.close()

    asyncio.run(_go())
    click.secho(f"Added/updated affiliate link '{code}'.", fg="green")


@affiliate_group.command(name="list")
def list_cmd():
    """List active affiliate links."""
    from modules.content.affiliate_links import list_active

    async def _go():
        conn = await _connect()
        try:
            return await list_active(conn)
        finally:
            await conn.close()

    links = asyncio.run(_go())
    if not links:
        click.echo("No active affiliate links.")
        return
    for lk in links:
        click.echo(f"  {lk.code:16} {lk.keyword:16} → {lk.url}")


@affiliate_group.command(name="enable")
@click.argument("code")
def enable_cmd(code):
    """Enable (activate) an affiliate link."""
    _set_active(code, True)


@affiliate_group.command(name="disable")
@click.argument("code")
def disable_cmd(code):
    """Disable (deactivate) an affiliate link."""
    _set_active(code, False)


def _set_active(code, active):
    from modules.content.affiliate_links import set_active

    async def _go():
        conn = await _connect()
        try:
            return await set_active(conn, code, active)
        finally:
            await conn.close()

    ok = asyncio.run(_go())
    if not ok:
        raise click.ClickException(f"No affiliate link with code '{code}'.")
    click.secho(f"{'Enabled' if active else 'Disabled'} '{code}'.", fg="green")


@affiliate_group.command(name="rm")
@click.argument("code")
def rm_cmd(code):
    """Remove an affiliate link."""
    from modules.content.affiliate_links import remove_link

    async def _go():
        conn = await _connect()
        try:
            return await remove_link(conn, code)
        finally:
            await conn.close()

    ok = asyncio.run(_go())
    if not ok:
        raise click.ClickException(f"No affiliate link with code '{code}'.")
    click.secho(f"Removed '{code}'.", fg="green")


__all__ = ["affiliate_group"]
