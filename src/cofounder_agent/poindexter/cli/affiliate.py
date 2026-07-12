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
    return await asyncpg.create_pool(dsn, min_size=1, max_size=2, timeout=8)


async def _republish(pool) -> None:
    """Republish both affiliate JSON exports + bust the referrals ISR tag.

    Best-effort: a republish failure must not fail the CLI command that
    already wrote the DB row (the row is the source of truth; a stale
    export self-heals on the next publish or on-demand rebuild).
    """
    from services.revalidation_service import trigger_nextjs_revalidation
    from services.site_config import SiteConfig
    from services.static_export_service import republish_affiliate_exports

    try:
        site_config = SiteConfig(pool=pool)
        try:
            await site_config.load(pool)
        except Exception:
            # silent-ok: best-effort settings load — the outer try/except
            # already reports a republish failure, and site_config still
            # works pool-only (just without DB-loaded settings) if this fails.
            pass
        await republish_affiliate_exports(pool, site_config=site_config)
        await trigger_nextjs_revalidation(tags=["referrals"], site_config=site_config)
    except Exception as e:  # noqa: BLE001 — never fail the CLI write over a republish hiccup
        click.secho(f"  (warning: republish failed: {e})", fg="yellow")


@click.group(name="affiliate")
def affiliate_group() -> None:
    """Manage affiliate links (add/list/enable/disable/rm)."""


@affiliate_group.command(name="add")
@click.option("--code", required=True, help="Stable slug for /go/<code> (e.g. mercury).")
@click.option("--keyword", required=True, help="Body keyword to match (e.g. Mercury).")
@click.option("--url", required=True, help="Real merchant/referral URL.")
@click.option("--display-text", default="", help="Link text (defaults to keyword).")
@click.option("--program", default="", help="Program label (e.g. 'Mercury Referral').")
@click.option(
    "--category",
    required=True,
    type=click.Choice(["service", "product"]),
    help="Section this link appears in on /referrals.",
)
@click.option(
    "--description",
    required=True,
    help="Reader-facing description shown on /referrals.",
)
def add_cmd(code, keyword, url, display_text, program, category, description):
    """Add or update an affiliate link."""
    from modules.content.affiliate_links import add_link

    async def _go():
        pool = await _connect()
        try:
            await add_link(
                pool, code=code, keyword=keyword, url=url,
                display_text=display_text, program=program,
                description=description, category=category,
            )
            await _republish(pool)
        finally:
            await pool.close()

    asyncio.run(_go())
    click.secho(f"Added/updated affiliate link '{code}'.", fg="green")


@affiliate_group.command(name="list")
def list_cmd():
    """List active affiliate links."""
    from modules.content.affiliate_links import list_active

    async def _go():
        pool = await _connect()
        try:
            return await list_active(pool)
        finally:
            await pool.close()

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
        pool = await _connect()
        try:
            ok = await set_active(pool, code, active)
            if ok:
                await _republish(pool)
            return ok
        finally:
            await pool.close()

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
        pool = await _connect()
        try:
            ok = await remove_link(pool, code)
            if ok:
                await _republish(pool)
            return ok
        finally:
            await pool.close()

    ok = asyncio.run(_go())
    if not ok:
        raise click.ClickException(f"No affiliate link with code '{code}'.")
    click.secho(f"Removed '{code}'.", fg="green")


__all__ = ["affiliate_group"]
