"""`poindexter affiliate` — manage affiliate-link rows (DB-only real URLs).

Thin adapter over ``modules.content.affiliate_links`` / ``affiliate_import``
(no inline SQL here, per the transport-adapter-contract ADR). Real referral
URLs never live in source; operators add them here.
"""
from __future__ import annotations

import asyncio

import click

from poindexter.cli._bootstrap import close_cli_pool, open_cli_pool


async def _connect():
    from brain import bootstrap

    dsn = bootstrap.resolve_database_url()
    if not dsn:
        raise click.ClickException("No database_url — run `poindexter setup` first.")
    return await open_cli_pool(dsn, timeout=8)


async def _make_site_config(pool):
    from services.site_config import SiteConfig

    site_config = SiteConfig(pool=pool)
    try:
        await site_config.load(pool)
    except Exception:
        # silent-ok: best-effort settings load — callers still work pool-only
        # (just without DB-loaded settings) if this fails.
        pass
    return site_config


async def _republish(pool) -> None:
    """Republish both affiliate JSON exports + bust the referrals ISR tag.

    Best-effort: a republish failure must not fail the CLI command that
    already wrote the DB row (the row is the source of truth; a stale
    export self-heals on the next publish or on-demand rebuild).
    """
    from services.revalidation_service import trigger_nextjs_revalidation
    from services.static_export_service import republish_affiliate_exports

    try:
        site_config = await _make_site_config(pool)
        await republish_affiliate_exports(pool, site_config=site_config)
        await trigger_nextjs_revalidation(tags=["referrals"], site_config=site_config)
    except Exception as e:  # noqa: BLE001 — never fail the CLI write over a republish hiccup
        click.secho(f"  (warning: republish failed: {e})", fg="yellow")


@click.group(name="affiliate")
def affiliate_group() -> None:
    """Manage affiliate links (add/list/enable/disable/rm/import-csv)."""


@affiliate_group.command(name="add")
@click.option("--code", required=True, help="Stable slug for /go/<code> (e.g. mercury).")
@click.option(
    "--keyword", "keywords", required=True, multiple=True,
    help="Body phrase to match (repeatable — pass multiple times for aliases).",
)
@click.option("--url", required=True, help="Real merchant/referral URL.")
@click.option("--display-text", default="", help="Link text (defaults to the matched keyword).")
@click.option("--program", default="", help="Program label (e.g. 'Mercury Referral').")
@click.option(
    "--category", required=True, type=click.Choice(["service", "product"]),
    help="Section this link appears in on /referrals.",
)
@click.option("--description", required=True, help="Reader-facing description shown on /referrals.")
@click.option("--platform", default="", help="Free-text tracking label (e.g. Amazon, direct).")
def add_cmd(code, keywords, url, display_text, program, category, description, platform):
    """Add or update an affiliate link."""
    from modules.content.affiliate_links import add_link

    async def _go():
        pool = await _connect()
        try:
            await add_link(
                pool, code=code, keywords=list(keywords), url=url,
                display_text=display_text, program=program,
                description=description, category=category, platform=platform,
            )
            await _republish(pool)
        finally:
            await close_cli_pool(pool)

    asyncio.run(_go())
    click.secho(f"Added/updated affiliate link '{code}'.", fg="green")


@affiliate_group.group(name="keyword")
def keyword_group() -> None:
    """Add or remove match phrases on an existing link.

    `affiliate add` replaces a link's whole keyword set and needs the real
    merchant URL, so it is the wrong tool for introducing one alias. These
    commands are additive and never touch the URL.
    """


@keyword_group.command(name="add")
@click.argument("code")
@click.option(
    "--keyword", "keywords", required=True, multiple=True,
    help="Phrase to match in body prose (repeatable).",
)
def keyword_add_cmd(code, keywords):
    """Append match phrases to an existing link (idempotent)."""
    from modules.content.affiliate_links import add_keywords

    async def _go():
        pool = await _connect()
        try:
            return await add_keywords(pool, code=code, keywords=list(keywords))
        finally:
            await close_cli_pool(pool)

    try:
        added = asyncio.run(_go())
    except (LookupError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    skipped = len(set(keywords)) - added
    msg = f"Added {added} keyword(s) to '{code}'."
    if skipped:
        msg += f" {skipped} already present."
    click.secho(msg, fg="green")


@keyword_group.command(name="rm")
@click.argument("code")
@click.option(
    "--keyword", "keywords", required=True, multiple=True,
    help="Phrase to stop matching (repeatable).",
)
def keyword_rm_cmd(code, keywords):
    """Remove match phrases from a link (never the last one)."""
    from modules.content.affiliate_links import remove_keywords

    async def _go():
        pool = await _connect()
        try:
            return await remove_keywords(pool, code=code, keywords=list(keywords))
        finally:
            await close_cli_pool(pool)

    try:
        removed = asyncio.run(_go())
    except (LookupError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.secho(f"Removed {removed} keyword(s) from '{code}'.", fg="green")


@affiliate_group.command(name="list")
@click.option("--all", "show_all", is_flag=True, help="Include inactive links.")
def list_cmd(show_all):
    """List affiliate links (active only, unless --all)."""
    from modules.content.affiliate_links import list_active, list_all

    async def _go():
        pool = await _connect()
        try:
            return await (list_all(pool) if show_all else list_active(pool))
        finally:
            await close_cli_pool(pool)

    links = asyncio.run(_go())
    if not links:
        click.echo("No affiliate links." if show_all else "No active affiliate links.")
        return
    for lk in links:
        kw = ", ".join(lk.keywords)
        platform = f" [{lk.platform}]" if lk.platform else ""
        click.echo(f"  {lk.code:16} {kw:40}{platform} → {lk.url}")


def _validate_enable_args(code, enable_all) -> None:
    if enable_all == bool(code):
        raise click.UsageError("Provide exactly one of CODE or --all.")


@affiliate_group.command(name="enable")
@click.argument("code", required=False)
@click.option("--all", "enable_all", is_flag=True, help="Enable every currently-inactive link.")
def enable_cmd(code, enable_all):
    """Enable (activate) one link, or every inactive link with --all."""
    _validate_enable_args(code, enable_all)
    if enable_all:
        _set_active_all(True)
    else:
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
            await close_cli_pool(pool)

    ok = asyncio.run(_go())
    if not ok:
        raise click.ClickException(f"No affiliate link with code '{code}'.")
    click.secho(f"{'Enabled' if active else 'Disabled'} '{code}'.", fg="green")


def _set_active_all(active: bool):
    from modules.content.affiliate_links import list_all, set_active

    async def _go():
        pool = await _connect()
        try:
            targets = await list_all(pool)
            changed = []
            for lk in targets:
                if await set_active(pool, lk.code, active):
                    changed.append(lk.code)
            if changed:
                await _republish(pool)
            return changed
        finally:
            await close_cli_pool(pool)

    changed = asyncio.run(_go())
    click.secho(
        f"{'Enabled' if active else 'Disabled'} {len(changed)} link(s): "
        f"{', '.join(changed) or '(none)'}",
        fg="green",
    )


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
            await close_cli_pool(pool)

    ok = asyncio.run(_go())
    if not ok:
        raise click.ClickException(f"No affiliate link with code '{code}'.")
    click.secho(f"Removed '{code}'.", fg="green")


@affiliate_group.command(name="import-csv")
@click.argument("csv_path", type=click.Path(exists=True, dir_okay=False))
@click.option("--force", is_flag=True, help="Overwrite existing rows instead of skipping them.")
def import_csv_cmd(csv_path, force):
    """Bulk-import affiliate links from a spreadsheet export."""
    from modules.content.affiliate_import import import_csv

    async def _go():
        pool = await _connect()
        try:
            site_config = await _make_site_config(pool)
            report = await import_csv(pool, csv_path, site_config=site_config, force=force)
            if report.created:
                await _republish(pool)
            return report
        finally:
            await close_cli_pool(pool)

    report = asyncio.run(_go())
    for r in report.rows:
        if r.status == "created":
            kw = ", ".join(r.keywords)
            click.secho(f"  created  {r.code:24} {r.display_text} [{kw}]", fg="green")
        elif r.status == "skipped_exists":
            click.secho(f"  skipped  {r.code:24} (already exists — use --force to overwrite)", fg="yellow")
        else:
            click.secho(f"  error    {r.code:24} {r.detail}", fg="red")
    click.echo(
        f"\n{len(report.created)} created, {len(report.skipped)} skipped, "
        f"{len(report.errors)} errors."
    )


__all__ = ["affiliate_group"]
