"""`poindexter community` — Reddit/IndieHackers draft assistant.

Thin adapter over ``services.community_drafts`` + ``services.subreddit_import``
(no inline SQL, per the transport-adapter-contract ADR). On-demand, draft-only:
generates native founder-voice drafts the operator reviews and posts manually.
"""
from __future__ import annotations

import asyncio

import click

from services.community_drafts import (
    SubredditProfile,
    add_profile,
    discard_draft,
    edit_draft,
    edit_profile,
    generate_reddit_draft,
    get_draft,
    get_profile,
    list_drafts,
    list_profiles,
    mark_posted,
    remove_profile,
    set_profile_enabled,
    suggest_subreddits_for_post,
)
from services.subreddit_import import export_csv, import_csv


async def _connect():
    import asyncpg
    from brain import bootstrap

    dsn = bootstrap.resolve_database_url()
    if not dsn:
        raise click.ClickException("No database_url — run `poindexter setup` first.")
    return await asyncpg.create_pool(dsn, min_size=1, max_size=2, timeout=8)


async def _make_site_config(pool):
    from services.site_config import SiteConfig

    site_config = SiteConfig(pool=pool)
    # Fail loud. The only caller is `draft reddit`, which needs the writer model
    # from settings — there is no pool-only path to protect (unlike
    # affiliate.py::_make_site_config, whose callers work without settings). A
    # swallow here would defer a real failure and swap its cause for a misleading
    # "set pipeline_writer_model" at generation time. Surface the true error.
    try:
        await site_config.load(pool)
    except Exception as e:
        raise click.ClickException(
            f"failed to load settings from the database: {e}"
        ) from e
    return site_config


@click.group(name="community")
def community_group() -> None:
    """Reddit/IndieHackers draft assistant (profiles / draft / drafts)."""


# ------------------------------------------------------------------ profiles
@community_group.group(name="profiles")
def profiles_group() -> None:
    """Manage subreddit profiles (add/list/edit/enable/disable/rm/import-csv/export-csv)."""


@profiles_group.command(name="list")
@click.option("--enabled", "enabled_only", is_flag=True, help="Only enabled profiles.")
def profiles_list(enabled_only):
    async def _go():
        pool = await _connect()
        try:
            return await list_profiles(pool, enabled_only=enabled_only)
        finally:
            await pool.close()

    profiles = asyncio.run(_go())
    if not profiles:
        click.echo("No subreddit profiles.")
        return
    for p in profiles:
        state = "on " if p.enabled else "off"
        ct = ",".join(p.content_types)
        click.echo(f"  [{state}] {p.subreddit:20} {p.post_type:6} {p.self_promo:8} {ct}")


@profiles_group.command(name="show")
@click.argument("subreddit")
def profiles_show(subreddit):
    async def _go():
        pool = await _connect()
        try:
            return await get_profile(pool, subreddit)
        finally:
            await pool.close()

    p = asyncio.run(_go())
    if p is None:
        raise click.ClickException(f"No profile for '{subreddit}'.")
    for f in ("subreddit", "enabled", "content_types", "post_type", "self_promo",
              "flair", "min_karma", "min_account_age_days", "rules_summary",
              "tone_notes", "cadence_cap_days"):
        click.echo(f"  {f:20} {getattr(p, f)}")


def _profile_options(f):
    f = click.option("--content-types", help="';'-delimited classifier labels this sub accepts.")(f)
    f = click.option("--post-type", type=click.Choice(["text", "link", "either"]))(f)
    f = click.option("--self-promo", type=click.Choice(["strict", "moderate", "ok"]))(f)
    f = click.option("--flair", help="Default flair to set.")(f)
    f = click.option("--min-karma", type=int)(f)
    f = click.option("--min-account-age-days", type=int)(f)
    f = click.option("--rules", "rules_summary", help="Rules + culture, fed to the LLM.")(f)
    f = click.option("--tone", "tone_notes", help="How to write for this sub.")(f)
    f = click.option("--cadence-cap-days", type=int)(f)
    return f


@profiles_group.command(name="add")
@click.argument("subreddit")
@_profile_options
def profiles_add(subreddit, content_types, post_type, self_promo, flair, min_karma,
                 min_account_age_days, rules_summary, tone_notes, cadence_cap_days):
    """Add a new subreddit profile (fails if it exists — use `edit`)."""
    profile = SubredditProfile(
        subreddit=subreddit,
        content_types=[c.strip() for c in (content_types or "").split(";") if c.strip()],
        post_type=post_type or "text", self_promo=self_promo or "strict",
        flair=flair, min_karma=min_karma, min_account_age_days=min_account_age_days,
        rules_summary=rules_summary or "", tone_notes=tone_notes or "",
        cadence_cap_days=cadence_cap_days,
    )

    async def _go():
        pool = await _connect()
        try:
            return await add_profile(pool, profile)
        finally:
            await pool.close()

    if not asyncio.run(_go()):
        raise click.ClickException(f"Profile '{subreddit}' already exists — use `edit`.")
    click.secho(f"Added subreddit profile '{subreddit}'.", fg="green")


@profiles_group.command(name="edit")
@click.argument("subreddit")
@_profile_options
def profiles_edit(subreddit, content_types, post_type, self_promo, flair, min_karma,
                  min_account_age_days, rules_summary, tone_notes, cadence_cap_days):
    """Edit an existing profile (only the flags you pass change)."""
    changes = dict(
        content_types=([c.strip() for c in content_types.split(";") if c.strip()]
                       if content_types is not None else None),
        post_type=post_type, self_promo=self_promo, flair=flair, min_karma=min_karma,
        min_account_age_days=min_account_age_days, rules_summary=rules_summary,
        tone_notes=tone_notes, cadence_cap_days=cadence_cap_days,
    )

    async def _go():
        pool = await _connect()
        try:
            return await edit_profile(pool, subreddit, **changes)
        finally:
            await pool.close()

    try:
        asyncio.run(_go())
    except KeyError:
        raise click.ClickException(f"No profile for '{subreddit}'.") from None
    click.secho(f"Updated '{subreddit}'.", fg="green")


@profiles_group.command(name="enable")
@click.argument("subreddit")
def profiles_enable(subreddit):
    _set_enabled(subreddit, True)


@profiles_group.command(name="disable")
@click.argument("subreddit")
def profiles_disable(subreddit):
    _set_enabled(subreddit, False)


def _set_enabled(subreddit, enabled):
    async def _go():
        pool = await _connect()
        try:
            return await set_profile_enabled(pool, subreddit, enabled)
        finally:
            await pool.close()

    if not asyncio.run(_go()):
        raise click.ClickException(f"No profile for '{subreddit}'.")
    click.secho(f"{'Enabled' if enabled else 'Disabled'} '{subreddit}'.", fg="green")


@profiles_group.command(name="rm")
@click.argument("subreddit")
def profiles_rm(subreddit):
    async def _go():
        pool = await _connect()
        try:
            return await remove_profile(pool, subreddit)
        finally:
            await pool.close()

    if not asyncio.run(_go()):
        raise click.ClickException(f"No profile for '{subreddit}'.")
    click.secho(f"Removed '{subreddit}'.", fg="green")


@profiles_group.command(name="import-csv")
@click.argument("csv_path", type=click.Path(exists=True, dir_okay=False))
@click.option("--force", is_flag=True, help="Update existing rows instead of skipping them.")
def profiles_import_csv(csv_path, force):
    """Bulk import subreddit profiles from a spreadsheet export."""
    async def _go():
        pool = await _connect()
        try:
            return await import_csv(pool, csv_path, force=force)
        finally:
            await pool.close()

    report = asyncio.run(_go())
    for r in report.rows:
        color = {"created": "green", "updated": "green",
                 "skipped_exists": "yellow", "error": "red"}.get(r.status, "white")
        extra = f" ({r.detail})" if r.detail else ""
        click.secho(f"  {r.status:15} {r.subreddit}{extra}", fg=color)
    click.echo(
        f"\n{len(report.created)} created, {len(report.updated)} updated, "
        f"{len(report.skipped)} skipped, {len(report.errors)} errors."
    )


@profiles_group.command(name="export-csv")
@click.argument("csv_path", type=click.Path(dir_okay=False), required=False)
def profiles_export_csv(csv_path):
    """Export every subreddit profile as CSV (to a file, or stdout)."""
    async def _go():
        pool = await _connect()
        try:
            return await export_csv(pool)
        finally:
            await pool.close()

    text = asyncio.run(_go())
    if csv_path:
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            f.write(text)
        click.secho(f"Wrote {csv_path}.", fg="green")
    else:
        click.echo(text)


# --------------------------------------------------------------------- draft
@community_group.group(name="draft")
def draft_group() -> None:
    """Generate a native founder-voice draft."""


@draft_group.command(name="reddit")
@click.argument("post")
@click.option("--subreddit", help="Target subreddit profile. Omit to see suggestions.")
def draft_reddit(post, subreddit):
    """Generate a Reddit value-post for a published POST (slug or id)."""
    if not subreddit:
        async def _suggest():
            pool = await _connect()
            try:
                return await suggest_subreddits_for_post(pool, post)
            finally:
                await pool.close()

        subs = asyncio.run(_suggest())
        if subs:
            click.echo("Matching subreddits (pass one via --subreddit):")
            for s in subs:
                click.echo(f"  {s}")
        else:
            click.echo("No matching subreddit profiles (post unclassified?). "
                       "Pass --subreddit explicitly.")
        raise click.ClickException("Choose a subreddit with --subreddit.")

    async def _go():
        pool = await _connect()
        try:
            site_config = await _make_site_config(pool)
            return await generate_reddit_draft(
                pool, post_id=post, subreddit=subreddit, site_config=site_config
            )
        finally:
            await pool.close()

    try:
        draft = asyncio.run(_go())
    except (KeyError, ValueError) as e:
        raise click.ClickException(str(e)) from e
    click.secho(f"Draft #{draft.id} for {draft.target}:", fg="green")
    if draft.warnings:
        click.secho("  warnings:", fg="yellow")
        for w in draft.warnings:
            click.secho(f"    - {w}", fg="yellow")
    click.secho(f"\nTitle: {draft.title}", bold=True)
    click.echo("\n" + draft.body)


# -------------------------------------------------------------------- drafts
@community_group.group(name="drafts")
def drafts_group() -> None:
    """List / review / mark-posted / discard community drafts."""


@drafts_group.command(name="list")
@click.option("--status", type=click.Choice(["draft", "posted", "discarded"]))
def drafts_list(status):
    async def _go():
        pool = await _connect()
        try:
            return await list_drafts(pool, status=status)
        finally:
            await pool.close()

    drafts = asyncio.run(_go())
    if not drafts:
        click.echo("No drafts.")
        return
    for d in drafts:
        click.echo(f"  #{d.id:<4} {d.status:9} {d.target:24} {(d.title or '')[:50]}")


@drafts_group.command(name="show")
@click.argument("draft_id", type=int)
def drafts_show(draft_id):
    async def _go():
        pool = await _connect()
        try:
            return await get_draft(pool, draft_id)
        finally:
            await pool.close()

    d = asyncio.run(_go())
    if d is None:
        raise click.ClickException(f"No draft #{draft_id}.")
    click.echo(f"#{d.id} {d.status} {d.target}")
    if d.warnings:
        click.secho("warnings: " + "; ".join(d.warnings), fg="yellow")
    if d.posted_url:
        click.echo(f"posted: {d.posted_url}")
    click.echo("\n" + d.body)


@drafts_group.command(name="edit")
@click.argument("draft_id", type=int)
@click.option("--title")
@click.option("--body-file", type=click.Path(exists=True, dir_okay=False),
              help="Read the new body from this file.")
def drafts_edit(draft_id, title, body_file):
    body = None
    if body_file:
        with open(body_file, encoding="utf-8") as f:
            body = f.read()

    async def _go():
        pool = await _connect()
        try:
            return await edit_draft(pool, draft_id, title=title, body=body)
        finally:
            await pool.close()

    if not asyncio.run(_go()):
        raise click.ClickException("Nothing changed (pass --title and/or --body-file).")
    click.secho(f"Updated draft #{draft_id}.", fg="green")


@drafts_group.command(name="mark-posted")
@click.argument("draft_id", type=int)
@click.option("--url", required=True, help="Permalink of the post you published.")
def drafts_mark_posted(draft_id, url):
    async def _go():
        pool = await _connect()
        try:
            return await mark_posted(pool, draft_id, url=url)
        finally:
            await pool.close()

    if not asyncio.run(_go()):
        raise click.ClickException(f"No draft #{draft_id}.")
    click.secho(f"Marked draft #{draft_id} posted → {url}", fg="green")


@drafts_group.command(name="discard")
@click.argument("draft_id", type=int)
def drafts_discard(draft_id):
    async def _go():
        pool = await _connect()
        try:
            return await discard_draft(pool, draft_id)
        finally:
            await pool.close()

    if not asyncio.run(_go()):
        raise click.ClickException(f"No draft #{draft_id}.")
    click.secho(f"Discarded draft #{draft_id}.", fg="green")


__all__ = ["community_group"]
