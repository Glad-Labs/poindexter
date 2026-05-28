"""``poindexter media`` — operator interface for the per-medium approval gate.

Generated podcasts, videos, and shorts sit in ``media_approvals`` with
``status='pending'`` until the operator decides. This CLI group is the
day-to-day surface for those decisions.

Examples
--------

    poindexter media pending                 # show all pending media
    poindexter media pending --medium podcast
    poindexter media approve <post_id> podcast
    poindexter media approve <post_id> video --note "great pacing"
    poindexter media reject  <post_id> podcast --note "tts glitches at 0:42"

The single source of truth is
``services/media_approval_service.py`` — this module is a thin Click
wrapper that opens a pool, calls into the service, and renders the
result. ``--json`` flips listing output to a machine-readable form
suitable for piping into ``jq`` / ``xargs`` / a shell loop.
"""

from __future__ import annotations

import asyncio
import json
import sys

import click

from poindexter.cli._bootstrap import resolve_dsn as _dsn


def _run(coro):
    return asyncio.run(coro)


async def _make_pool():
    """Open a tiny pool for one CLI invocation."""
    import asyncpg
    return await asyncpg.create_pool(_dsn(), min_size=1, max_size=2)


_VALID_MEDIA = ("podcast", "video", "video_short")


@click.group(name="media")
def media_group():
    """Operator decisions on generated podcasts / videos / shorts."""


@media_group.command(name="pending")
@click.option(
    "--medium",
    type=click.Choice(_VALID_MEDIA, case_sensitive=False),
    default=None,
    help="Filter to one medium (default: all).",
)
@click.option("--limit", type=int, default=50, show_default=True)
@click.option("--json", "as_json", is_flag=True, help="Machine-readable output.")
def cmd_pending(medium: str | None, limit: int, as_json: bool):
    """List media awaiting operator approval."""
    async def _go():
        from services import media_approval_service

        pool = await _make_pool()
        try:
            rows = await media_approval_service.list_pending(
                pool, medium=medium, limit=limit,
            )
        finally:
            await pool.close()
        return rows

    rows = _run(_go())

    if as_json:
        # Serialize created_at via isoformat — datetime isn't json-native.
        for r in rows:
            ts = r.get("created_at")
            if ts is not None:
                r["created_at"] = ts.isoformat()
        click.echo(json.dumps(rows, indent=2))
        return

    if not rows:
        click.echo("No media awaiting approval.")
        return

    click.secho(f"{len(rows)} pending media item(s):", fg="cyan", bold=True)
    for r in rows:
        post_id_short = (r.get("post_id") or "")[:8]
        med = r.get("medium") or "?"
        title = (r.get("title") or "(untitled)")[:60]
        slug = r.get("slug") or ""
        click.secho(f"  {post_id_short}  {med:<14} {title}", fg="yellow")
        click.secho(f"    slug={slug}", fg="bright_black")


@media_group.command(name="approve")
@click.argument("post_id")
@click.argument("medium", type=click.Choice(_VALID_MEDIA, case_sensitive=False))
@click.option("--note", default=None, help="Optional rationale for the decision.")
def cmd_approve(post_id: str, medium: str, note: str | None):
    """Approve a generated medium — releases it to its distribution surface."""
    _decide(post_id, medium, approved=True, note=note)


@media_group.command(name="reject")
@click.argument("post_id")
@click.argument("medium", type=click.Choice(_VALID_MEDIA, case_sensitive=False))
@click.option("--note", default=None, help="Optional rationale for the decision.")
def cmd_reject(post_id: str, medium: str, note: str | None):
    """Reject a generated medium — file stays on disk, never published."""
    _decide(post_id, medium, approved=False, note=note)


def _decide(post_id: str, medium: str, *, approved: bool, note: str | None):
    async def _go():
        from services import media_approval_service

        pool = await _make_pool()
        try:
            await media_approval_service.decide(
                pool, post_id, medium,
                approved=approved,
                decided_by="operator:cli",
                notes=note,
            )
        finally:
            await pool.close()

    try:
        _run(_go())
    except ValueError as e:
        # decide() raises when the row doesn't exist — surface a clean
        # operator message instead of a stack trace.
        click.echo(f"Error: {e}", err=True)
        sys.exit(2)

    verb = "approved" if approved else "rejected"
    click.secho(f"{verb}: {medium} for post {post_id[:8]}", fg="green" if approved else "yellow")
