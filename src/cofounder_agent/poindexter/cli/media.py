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
import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import click

from poindexter.cli._bootstrap import resolve_dsn as _dsn
from poindexter.cli._prefix import looks_like_full_uuid, resolve_uuid_prefix

logger = logging.getLogger(__name__)


def _run(coro):
    return asyncio.run(coro)


async def _make_pool():
    """Open a tiny pool for one CLI invocation."""
    import asyncpg
    return await asyncpg.create_pool(_dsn(), min_size=1, max_size=2)


async def _make_site_config(pool):
    """Load a SiteConfig so ``decide()`` can rebuild the R2 feed on approve.

    Mirrors ``poindexter.cli.publish_approval._make_site_config``. The load is
    non-fatal: an approve still succeeds on partial config (the feed rebuild
    inside ``decide()`` is itself non-fatal — it just won't propagate).
    """
    from services.site_config import SiteConfig

    cfg = SiteConfig(pool=pool)
    try:
        await cfg.load(pool)
    except Exception as e:  # noqa: BLE001 — partial config still lets approve succeed
        # Warn (not debug): a failed load means decide()'s R2 feed rebuild
        # won't propagate, so the operator's approve silently won't refresh
        # Apple/Spotify/the video feed — exactly the silent-staleness class
        # this gate exists to surface. The approve itself still succeeds.
        logger.warning(
            "media CLI: site_config load failed (non-fatal) — approve will "
            "proceed but the R2 feed rebuild won't propagate: %s", e,
        )
    return cfg


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
        # Same for quality_evaluated_at, and unwrap numeric Decimal.
        from decimal import Decimal
        for r in rows:
            for ts_key in ("created_at", "quality_evaluated_at"):
                ts = r.get(ts_key)
                if ts is not None:
                    r[ts_key] = ts.isoformat()
            if isinstance(r.get("quality_score"), Decimal):
                r["quality_score"] = float(r["quality_score"])
            # quality_signals comes back as a JSON string from asyncpg;
            # parse for the JSON-output case so consumers don't get a
            # nested-quoted string.
            qs = r.get("quality_signals")
            if isinstance(qs, str):
                try:
                    r["quality_signals"] = json.loads(qs)
                except (json.JSONDecodeError, TypeError):
                    pass
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

        # Surface Layer 1 quality signals when present so the operator
        # can decide without spinning up a separate player. If
        # quality_score is None the eval hasn't run yet (legacy row
        # or pre-eval generation); render "—" to make that visible.
        score = r.get("quality_score")
        signals_raw = r.get("quality_signals")
        signals: dict[str, Any] = {}
        if isinstance(signals_raw, str):
            try:
                signals = json.loads(signals_raw)
            except (json.JSONDecodeError, TypeError):
                signals = {}
        elif isinstance(signals_raw, dict):
            signals = signals_raw

        if score is None and not signals:
            click.secho(
                "    quality: not evaluated yet (re-run backfill to populate)",
                fg="bright_black",
            )
        else:
            dur = signals.get("duration_seconds")
            sil = signals.get("silence_ratio")
            size = signals.get("file_size_bytes")
            parts: list[str] = []
            if dur is not None:
                parts.append(f"dur={dur:.0f}s")
            if sil is not None:
                parts.append(f"silence={sil:.0%}")
            if size is not None:
                parts.append(f"size={size // 1024}KB")
            score_str = f"{float(score):.2f}" if score is not None else "—"
            summary = " ".join(parts) if parts else "(no signals)"
            click.secho(
                f"    quality_score={score_str}  {summary}",
                fg="bright_black",
            )


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
            # Operators paste the 8-char prefix `media pending` renders;
            # decide() casts post_id::uuid, so expand it first. Resolve
            # WITHIN this medium (the medium already disambiguates) against
            # media_approvals — the surface the operator actually saw — via
            # the shared resolver (#1511 semantics, now on the common seam).
            resolved = await resolve_uuid_prefix(
                pool,
                table="media_approvals",
                column="post_id",
                prefix=post_id,
                extra_where="medium = $1",
                params=(medium,),
                noun="post",
            )
            if resolved is None:
                raise click.UsageError(
                    f"No {medium} media for a post matching {post_id!r}. "
                    f"See `poindexter media pending --medium {medium}`."
                )
            # Load site_config so an approve rebuilds the matching R2 feed
            # immediately (decide() rebuild is non-fatal). Only needed on
            # approve, but cheap + harmless to build for reject too.
            site_config = await _make_site_config(pool)
            await media_approval_service.decide(
                pool, resolved, medium,
                approved=approved,
                decided_by="operator:cli",
                notes=note,
                site_config=site_config,
            )
            return resolved
        finally:
            await pool.close()

    try:
        resolved_id = _run(_go())
    except click.UsageError as e:
        # Zero-match (above) or AmbiguousPrefixError (a click.UsageError) —
        # render cleanly and exit 2 like the rest of the CLI.
        click.echo(f"Error: {e}", err=True)
        sys.exit(2)
    except ValueError as e:
        # decide() raises when the row doesn't exist — surface a clean
        # operator message instead of a stack trace.
        click.echo(f"Error: {e}", err=True)
        sys.exit(2)

    verb = "approved" if approved else "rejected"
    click.secho(
        f"{verb}: {medium} for post {resolved_id[:8]}",
        fg="green" if approved else "yellow",
    )


# ---------------------------------------------------------------------------
# ``open`` subcommand — cross-platform "open the file with the OS default app"
# ---------------------------------------------------------------------------

# Worker container runs as appuser; the CLI runs on the host where the same
# volume is bind-mounted.  Strip the container prefix to get the host path.
_CONTAINER_POINDEXTER = "/home/appuser/.poindexter/"
_HOST_POINDEXTER = Path(os.path.expanduser("~")) / ".poindexter"


def _translate_container_path(storage_path: str) -> Path:
    """Translate a container-side ``storage_path`` to the equivalent host path.

    Videos are rendered by name derived from the pipeline task UUID, not the
    post UUID, so constructing the path from ``post_id`` is wrong for video
    assets.  Reading ``media_assets.storage_path`` from the DB and translating
    it here is the single source of truth for where each file actually lives.
    """
    if storage_path.startswith(_CONTAINER_POINDEXTER):
        relative = storage_path[len(_CONTAINER_POINDEXTER):]
        return _HOST_POINDEXTER / relative
    return Path(storage_path)


async def _resolve_open_path(post_id: str, medium: str) -> tuple[str, Path | None]:
    """Resolve post_id prefix and look up ``media_assets.storage_path`` in one pool.

    Returns ``(resolved_uuid, host_path)``; ``host_path`` is ``None`` when no
    asset row exists for the ``(post_id, medium)`` pair (not yet rendered).
    Raises ``click.BadParameter`` for an unresolvable id prefix.
    """
    from services import media_approval_service

    pool = await _make_pool()
    try:
        if looks_like_full_uuid(post_id):
            resolved = post_id
        else:
            resolved = await resolve_uuid_prefix(
                pool, table="posts", column="id", prefix=post_id, noun="post",
            )
            if resolved is None:
                raise click.BadParameter(
                    f"no post matches {post_id!r} (need a full UUID or a known id prefix)",
                    param_hint="post_id",
                )

        storage_path = await media_approval_service.get_asset_storage_path(
            pool, resolved, medium,
        )
    finally:
        await pool.close()

    if storage_path is None:
        return resolved, None
    return resolved, _translate_container_path(storage_path)


def _open_with_default_app(path: Path) -> None:
    """Hand the file to the OS-default application.

    Windows: ``os.startfile`` (stdlib). macOS: ``open``. Linux: ``xdg-open``.
    The dispatch keys on ``sys.platform`` so unit tests can swap the
    platform and assert the right branch fires.
    """
    platform = sys.platform
    if platform == "win32":
        os.startfile(str(path))  # type: ignore[attr-defined]
        return
    if platform == "darwin":
        subprocess.run(["open", str(path)], check=False)
        return
    # Linux + everything else fall back to xdg-open — the freedesktop
    # standard. If xdg-open isn't installed the subprocess returns
    # non-zero but we don't raise; the operator sees the failure inline.
    subprocess.run(["xdg-open", str(path)], check=False)


@media_group.command(name="open")
@click.argument("post_id")
@click.argument("medium", type=click.Choice(_VALID_MEDIA, case_sensitive=False))
def cmd_open(post_id: str, medium: str):
    """Open a generated media file with the OS default application.

    Accepts a full post UUID or the 8-char prefix the dashboards /
    ``media pending`` render. Looks up the canonical path from
    ``media_assets.storage_path`` so the path is always correct regardless
    of whether the file is named after the post UUID or the pipeline task UUID.
    """
    resolved, path = _run(_resolve_open_path(post_id, medium))

    if path is None or not path.exists():
        label = str(path) if path is not None else "(no asset record — not rendered yet)"
        click.echo(
            f"No file at {label} — has the backfill produced this medium yet? "
            f"Check 'poindexter media pending'.",
            err=True,
        )
        sys.exit(2)

    _open_with_default_app(path)
    click.secho(f"Opened {medium} for post {resolved[:8]}: {path}", fg="cyan")
