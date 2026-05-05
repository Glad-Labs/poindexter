"""``poindexter dev-diary`` — operator-side controls for the dev_diary niche.

Per ``feedback_dev_diary_voice_is_founder_not_journalist``: bundle facts
are dry by design, so the operator injects 1-2 sentences of personality
that the dev_diary post draws its emotional through-line from. This
CLI is how the operator submits and reviews those notes.

Subcommands:

- ``note "<text>" [--mood <mood>]`` — submit an operator note for
  today's dev_diary. Multiple notes per day are allowed and
  concatenated in chronological order when the bundle assembles.
- ``notes [--date YYYY-MM-DD] [--last N]`` — list recent operator
  notes (default: today's notes).
- ``trigger`` — kick a fresh dev_diary task immediately, picking up
  whatever operator_notes are currently filed for today. Useful when
  you've just submitted a note and want to see how the post reads
  with that personality anchor.

Per ``feedback_design_for_llm_consumers``: every subcommand has a
``--json`` mode so an LLM operator can parse output reliably.
Per ``feedback_always_keep_ml_in_mind``: every operator_note row
becomes training data — the corpus of (note, edited_post) pairs is
the dataset for future voice fine-tuning.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import click

logger = logging.getLogger(__name__)


async def _open_pool() -> Any:
    import asyncpg
    from ._bootstrap import resolve_dsn
    return await asyncpg.create_pool(resolve_dsn(), min_size=1, max_size=2)


@click.group(
    "dev-diary",
    help=(
        "Operator-side controls for the dev_diary niche. Use 'note' to "
        "inject 1-2 sentences of personality that the next dev_diary "
        "post will weave through its narrative — bundle facts stay "
        "grounded; voice and mood come from your note."
    ),
)
def dev_diary_group() -> None:
    """dev-diary command group."""


@dev_diary_group.command("note")
@click.argument("text")
@click.option(
    "--mood", "mood", default=None,
    help=(
        "Optional categorical mood hint: slog, triumph, flow, frustrated, "
        "curious, relief. The post register can lean into the mood; "
        "the prose of the note is still the primary anchor."
    ),
)
@click.option(
    "--niche", "niche", default="dev_diary",
    help="Niche slug. Default dev_diary; the table supports other niches.",
)
@click.option(
    "--json", "as_json", is_flag=True,
    help="Emit JSON for LLM/script consumers.",
)
def cmd_note(text: str, mood: str | None, niche: str, as_json: bool) -> None:
    """Submit an operator note for today's dev_diary post."""
    asyncio.run(_run_note(text, mood, niche, as_json))


async def _run_note(text: str, mood: str | None, niche: str, as_json: bool) -> None:
    text = (text or "").strip()
    if not text:
        click.echo("note text is empty — nothing submitted")
        return
    pool = await _open_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO operator_notes (niche_slug, note, mood, created_by)
            VALUES ($1, $2, $3, 'operator')
            RETURNING id, note_date, created_at
            """,
            niche, text, mood,
        )
    await pool.close()
    payload = {
        "id": row["id"], "niche": niche, "note_date": str(row["note_date"]),
        "mood": mood, "note": text,
        "created_at": row["created_at"].isoformat() if row["created_at"] else None,
    }
    if as_json:
        click.echo(json.dumps(payload, indent=2, default=str))
    else:
        click.echo(f"saved operator note #{row['id']} for {niche} on {row['note_date']}")
        if mood:
            click.echo(f"  mood: {mood}")
        click.echo(f"  note: {text}")
        click.echo()
        click.echo(
            "next dev_diary task picks this up automatically. "
            "Use 'poindexter dev-diary trigger' to kick a fresh task now."
        )


@dev_diary_group.command("notes")
@click.option(
    "--date", "date_str", default=None,
    help="Date filter YYYY-MM-DD (default: today).",
)
@click.option(
    "--last", "last_n", type=int, default=10,
    help="Show the last N notes (default 10) when --date is omitted.",
)
@click.option(
    "--niche", "niche", default="dev_diary",
)
@click.option("--json", "as_json", is_flag=True)
def cmd_notes(date_str: str | None, last_n: int, niche: str, as_json: bool) -> None:
    """List operator notes — defaults to today's notes for dev_diary."""
    asyncio.run(_run_notes(date_str, last_n, niche, as_json))


async def _run_notes(
    date_str: str | None, last_n: int, niche: str, as_json: bool,
) -> None:
    pool = await _open_pool()
    async with pool.acquire() as conn:
        if date_str:
            rows = await conn.fetch(
                """
                SELECT id, note_date, note, mood, created_at, created_by
                  FROM operator_notes
                 WHERE niche_slug = $1 AND note_date = $2::date
                 ORDER BY created_at ASC
                """,
                niche, date_str,
            )
        else:
            rows = await conn.fetch(
                """
                SELECT id, note_date, note, mood, created_at, created_by
                  FROM operator_notes
                 WHERE niche_slug = $1
                 ORDER BY created_at DESC
                 LIMIT $2
                """,
                niche, last_n,
            )
    await pool.close()
    if as_json:
        click.echo(json.dumps([dict(r) for r in rows], indent=2, default=str))
        return
    if not rows:
        click.echo("(no operator notes)")
        return
    for r in rows:
        when = str(r["created_at"])[:19]
        mood_tag = f"[{r['mood']}] " if r["mood"] else ""
        click.echo(f"#{r['id']} {when} {r['note_date']} {mood_tag}{r['note']}")


@dev_diary_group.command("trigger")
@click.option(
    "--lookback-hours", "lookback_hours", type=int, default=24,
    help="How many hours of activity to gather (default 24).",
)
def cmd_trigger(lookback_hours: int) -> None:
    """Kick a fresh dev_diary task immediately — picks up today's
    operator_notes."""
    asyncio.run(_run_trigger(lookback_hours))


async def _run_trigger(lookback_hours: int) -> None:
    pool = await _open_pool()
    try:
        from services import site_config as sc_mod
        if hasattr(sc_mod.site_config, "load_from_db"):
            try:
                await sc_mod.site_config.load_from_db(pool)
            except Exception:
                pass

        from services.topic_sources.dev_diary_source import DevDiarySource
        from services.jobs.run_dev_diary_post import _create_dev_diary_task

        source = DevDiarySource()
        ctx = await source.gather_context(
            pool, hours_lookback=lookback_hours, confidence_floor=0.5,
        )
        if ctx.is_empty():
            click.echo("empty bundle — nothing to narrate")
            return
        notes_count = len(ctx.operator_notes)
        click.echo(
            f"bundle: prs={len(ctx.merged_prs)} "
            f"commits={len(ctx.notable_commits)} notes={notes_count}"
        )
        task_id = await _create_dev_diary_task(pool, ctx, gates="preview_approval")
        click.echo(f"queued task: {task_id}")
        if notes_count == 0:
            click.echo()
            click.echo(
                "tip: no operator notes filed for today. The post will fall "
                "back to inferred mood from the bundle. Submit a note first "
                "with 'poindexter dev-diary note \"...\"' for authentic voice."
            )
    finally:
        await pool.close()


__all__ = ["dev_diary_group"]
