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
import os
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any

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


# ---------------------------------------------------------------------------
# ``open`` subcommand — cross-platform "open the file with the OS default app"
# ---------------------------------------------------------------------------


def _resolve_media_path(post_id: str, medium: str) -> Path:
    """Return the on-disk path the backfill writes for ``(post_id, medium)``.

    Imports ``PODCAST_DIR`` / ``VIDEO_DIR`` from the producing services so
    the operator surface can't drift from the writer surface if those
    directories ever move.
    """
    # Lazy-import the directory constants so the CLI import path stays
    # cheap (these modules pull ffmpeg / torch helpers).
    from services.podcast_service import PODCAST_DIR
    from services.video_service import VIDEO_DIR

    if medium == "podcast":
        return PODCAST_DIR / f"{post_id}.mp3"
    if medium == "video":
        return VIDEO_DIR / f"{post_id}.mp4"
    if medium == "video_short":
        # The composed short lives next to the long-form video — see
        # ``services/video_service.py`` (``{post_id}-short.mp4``). The
        # ``-short-audio.mp3`` file is an intermediate TTS scratch piece;
        # the operator wants to preview the final composed short, not
        # the source narration.
        return VIDEO_DIR / f"{post_id}-short.mp4"
    raise ValueError(f"unknown medium {medium!r}")


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

    The path matches what the backfill jobs write to disk — imports
    ``PODCAST_DIR`` / ``VIDEO_DIR`` from the producing services so the
    operator surface and the writer surface can't drift.
    """
    # Validate post_id is a proper UUID before touching the filesystem —
    # a typo'd id should fail loud per ``feedback_no_silent_defaults``,
    # not silently render "file not found".
    try:
        uuid.UUID(post_id)
    except (ValueError, AttributeError, TypeError) as e:
        raise click.BadParameter(
            f"post_id must be a UUID; got {post_id!r} ({e})",
            param_hint="post_id",
        )

    path = _resolve_media_path(post_id, medium)

    if not path.exists():
        click.echo(
            f"No file at {path} — has the backfill produced this medium yet? "
            f"Check 'poindexter media pending'.",
            err=True,
        )
        sys.exit(2)

    _open_with_default_app(path)
    click.secho(f"Opened {medium} for post {post_id[:8]}: {path}", fg="cyan")
