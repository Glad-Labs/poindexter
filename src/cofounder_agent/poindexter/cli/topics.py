"""``poindexter topics`` — operator commands for the topic-decision gate (#146).

Scoped wrapper over the generic approval CLI. Five subcommands:

- ``poindexter topics list``       — what's queued (filterable by source).
- ``poindexter topics show``       — full artifact for one queued topic.
- ``poindexter topics approve``    — flip a queued topic into ``pending``.
- ``poindexter topics reject``     — flip a queued topic into ``dismissed``.
- ``poindexter topics propose``    — manually inject a topic into the queue.

Every command supports ``--help`` (Click default) and read commands
support ``--json`` for piping into ``jq``. Empty queues exit zero with
a friendly message — they're an expected state, not an error.

The CLI is the canonical operator surface; MCP tools and any future
REST endpoints call into the same service modules
(``services.approval_service``, ``services.topic_proposal_service``).
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from typing import Any

import click


# ---------------------------------------------------------------------------
# Shared helpers — match approval.py so the user-facing flag conventions
# stay consistent across both command groups.
# ---------------------------------------------------------------------------


def _dsn() -> str:
    """Resolve the PostgreSQL DSN.

    Same env-var ladder as ``poindexter approve`` / ``qa-gates``. The
    CLI runs outside the worker process, so it can't read
    ``app.state.site_config``; env vars are the standard escape hatch.
    """
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


def _run(coro):
    return asyncio.run(coro)


async def _make_pool():
    import asyncpg
    return await asyncpg.create_pool(_dsn(), min_size=1, max_size=2)


async def _make_site_config(pool):
    """Construct a SiteConfig the CLI can hand into service modules."""
    from services.site_config import SiteConfig

    cfg = SiteConfig(pool=pool)
    try:
        await cfg.load(pool)
    except Exception:
        # Defensive — load can fail in odd environments (missing
        # app_settings, partial bootstrap). Fall back to an empty
        # config so commands that only need the gate flag still work.
        pass
    return cfg


def _exit_error(msg: str, code: int = 1) -> None:
    click.echo(f"Error: {msg}", err=True)
    sys.exit(code)


def _format_age(paused_at_iso: str | None) -> str:
    """Render `gate_paused_at` as a human-friendly age string.

    Returns ``"-"`` when the timestamp is missing / unparseable so the
    table doesn't gain stray exception output mid-render.
    """
    if not paused_at_iso:
        return "-"
    try:
        # datetime.fromisoformat handles both with-offset and naive,
        # but service emits UTC ISO so we coerce to aware.
        ts = datetime.fromisoformat(paused_at_iso.replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
    except ValueError:
        return paused_at_iso
    delta = datetime.now(timezone.utc) - ts
    secs = int(delta.total_seconds())
    if secs < 60:
        return f"{secs}s"
    if secs < 3600:
        return f"{secs // 60}m"
    if secs < 86400:
        return f"{secs // 3600}h"
    return f"{secs // 86400}d"


# ---------------------------------------------------------------------------
# Group root
# ---------------------------------------------------------------------------


@click.group(
    name="topics",
    help=(
        "Operator commands for the topic-decision approval queue.\n\n"
        "Anticipation_engine and manual proposals both land in the same "
        "queue when ``pipeline_gate_topic_decision = on``. Drain at your "
        "pace with `topics approve` / `topics reject`.\n\n"
        "Use `poindexter gates set topic_decision on` to enable the gate."
    ),
)
def topics_group() -> None:
    pass


# ---------------------------------------------------------------------------
# topics list
# ---------------------------------------------------------------------------


@topics_group.command("list")
@click.option(
    "--source", "source_filter", default=None,
    help=(
        "Filter to one origin (e.g. ``manual`` or ``anticipation_engine``). "
        "Matches ``artifact->>'source'``."
    ),
)
@click.option(
    "--limit", type=int, default=100, show_default=True,
    help="Max rows to return.",
)
@click.option("--json", "json_output", is_flag=True, help="Print as JSON.")
def topics_list_command(
    source_filter: str | None, limit: int, json_output: bool,
) -> None:
    """List every topic currently paused at the topic_decision gate.

    Empty queue exits zero with an informative message — that's a
    legitimate state, not an error.
    """
    from services.approval_service import list_pending

    async def _impl():
        pool = await _make_pool()
        try:
            return await list_pending(
                pool=pool, gate_name="topic_decision", limit=limit,
            )
        finally:
            await pool.close()

    try:
        rows = _run(_impl())
    except Exception as e:
        _exit_error(f"{type(e).__name__}: {e}")
        return

    if source_filter:
        rows = [
            r for r in rows
            if (r.get("artifact") or {}).get("source") == source_filter
        ]

    if json_output:
        click.echo(json.dumps(rows, indent=2, default=str))
        return

    if not rows:
        label = f" (source={source_filter})" if source_filter else ""
        click.echo(f"(no topics in the queue{label})")
        return

    label = f" source={source_filter!r}" if source_filter else ""
    click.secho(
        f"Topic-decision queue ({len(rows)} pending{label}):", fg="cyan",
    )
    click.echo()
    click.echo(f"  {'TASK_ID':<10} {'AGE':<6} {'SOURCE':<22} TOPIC")
    for row in rows:
        tid = (row.get("task_id") or "")[:8]
        artifact = row.get("artifact") or {}
        topic = (
            artifact.get("topic")
            or row.get("title")
            or row.get("topic")
            or "(no topic)"
        )
        source = (artifact.get("source") or "?")[:22]
        age = _format_age(row.get("gate_paused_at"))
        click.secho(
            f"  {tid:<10} {age:<6} {source:<22} {topic[:60]}",
            fg="yellow",
        )


# ---------------------------------------------------------------------------
# topics show
# ---------------------------------------------------------------------------


@topics_group.command("show")
@click.argument("task_id")
@click.option("--json", "json_output", is_flag=True, help="Print as JSON.")
def topics_show_command(task_id: str, json_output: bool) -> None:
    """Pretty-print the full artifact for one queued topic."""
    from services.approval_service import (
        ApprovalServiceError,
        show_pending,
    )

    async def _impl():
        pool = await _make_pool()
        try:
            return await show_pending(pool=pool, task_id=task_id)
        finally:
            await pool.close()

    try:
        row = _run(_impl())
    except ApprovalServiceError as e:
        _exit_error(str(e))
        return
    except Exception as e:
        _exit_error(f"unexpected: {type(e).__name__}: {e}")
        return

    # Defensive: if the task is paused at a different gate, surface the
    # mismatch loudly. ``poindexter topics show`` is scoped to the
    # topic_decision gate; pointing it at a preview-approval task
    # should fail clean rather than render confusing output.
    if row.get("gate_name") and row["gate_name"] != "topic_decision":
        _exit_error(
            f"Task {task_id} is paused at gate {row['gate_name']!r}, "
            f"not 'topic_decision'. Use `poindexter show-pending` for "
            f"the generic command."
        )
        return

    if json_output:
        click.echo(json.dumps(row, indent=2, default=str))
        return

    artifact = row.get("artifact") or {}
    click.secho(f"Task {row['task_id']}", fg="cyan", bold=True)
    click.echo(f"  paused_at      {row.get('gate_paused_at')}")
    click.echo(f"  age            {_format_age(row.get('gate_paused_at'))}")
    click.echo(f"  status         {row.get('status')}")
    click.echo()
    click.echo(f"  topic              {artifact.get('topic', '')}")
    click.echo(f"  primary_keyword    {artifact.get('primary_keyword', '')}")
    click.echo(f"  tags               {', '.join(artifact.get('tags') or []) or '-'}")
    click.echo(f"  category           {artifact.get('category_suggestion', '')}")
    click.echo(f"  source             {artifact.get('source', '')}")
    summary = artifact.get("research_summary") or ""
    if summary:
        click.echo()
        click.secho("  research_summary:", fg="bright_black")
        click.echo(f"    {summary}")
    signals = artifact.get("score_signals") or {}
    if signals:
        click.echo()
        click.secho("  score_signals:", fg="bright_black")
        for k, v in signals.items():
            click.echo(f"    {k:<25} {v}")


# ---------------------------------------------------------------------------
# topics approve
# ---------------------------------------------------------------------------


@topics_group.command("approve")
@click.argument("task_id")
@click.option(
    "--feedback", default=None,
    help="Optional operator note (recorded in audit_log).",
)
@click.option("--json", "json_output", is_flag=True, help="Print as JSON.")
def topics_approve_command(
    task_id: str, feedback: str | None, json_output: bool,
) -> None:
    """Approve a queued topic — flips it to ``pending`` and resumes the pipeline.

    Alias for ``poindexter approve <task_id> --gate topic_decision``.
    The ``--gate`` flag is asserted explicitly so a misrouted task
    surfaces as a GateMismatchError rather than silently approving
    the wrong artifact.
    """
    from services.approval_service import (
        ApprovalServiceError,
        approve as approve_service,
    )

    async def _impl():
        pool = await _make_pool()
        try:
            site_config = await _make_site_config(pool)
            return await approve_service(
                task_id=task_id,
                gate_name="topic_decision",
                feedback=feedback,
                site_config=site_config,
                pool=pool,
            )
        finally:
            await pool.close()

    try:
        result = _run(_impl())
    except ApprovalServiceError as e:
        _exit_error(str(e))
        return
    except Exception as e:
        _exit_error(f"unexpected: {type(e).__name__}: {e}")
        return

    if json_output:
        click.echo(json.dumps(result, indent=2, default=str))
        return

    click.secho(
        f"Approved topic for task {task_id} — pipeline resumed.",
        fg="green",
    )
    if feedback:
        click.echo(f"  feedback: {feedback}")


# ---------------------------------------------------------------------------
# topics reject
# ---------------------------------------------------------------------------


@topics_group.command("reject")
@click.argument("task_id")
@click.option(
    "--reason", default=None,
    help="Optional veto reason (recorded in audit_log).",
)
@click.option("--json", "json_output", is_flag=True, help="Print as JSON.")
def topics_reject_command(
    task_id: str, reason: str | None, json_output: bool,
) -> None:
    """Reject a queued topic — flips it to ``dismissed`` and ends the task.

    Alias for ``poindexter reject <task_id> --gate topic_decision``.
    The reject status defaults to ``"dismissed"`` for the
    topic_decision gate (configured via the
    ``approval_gate_topic_decision_reject_status`` app_settings key
    seeded by the migration / first-run init helper).
    """
    from services.approval_service import (
        ApprovalServiceError,
        reject as reject_service,
    )

    async def _impl():
        pool = await _make_pool()
        try:
            site_config = await _make_site_config(pool)
            return await reject_service(
                task_id=task_id,
                gate_name="topic_decision",
                reason=reason,
                site_config=site_config,
                pool=pool,
            )
        finally:
            await pool.close()

    try:
        result = _run(_impl())
    except ApprovalServiceError as e:
        _exit_error(str(e))
        return
    except Exception as e:
        _exit_error(f"unexpected: {type(e).__name__}: {e}")
        return

    if json_output:
        click.echo(json.dumps(result, indent=2, default=str))
        return

    click.secho(
        f"Rejected topic for task {task_id} → status={result.get('new_status')!r}.",
        fg="yellow",
    )
    if reason:
        click.echo(f"  reason: {reason}")


# ---------------------------------------------------------------------------
# topics propose
# ---------------------------------------------------------------------------


@topics_group.command("propose")
@click.option(
    "--topic", "topic_text", required=True,
    help="Topic string to inject (required, non-empty).",
)
@click.option(
    "--keyword", "primary_keyword", default=None,
    help="Primary SEO keyword. Falls back to the first tag.",
)
@click.option(
    "--tags", default=None,
    help="Comma-separated tag list (e.g. ``ai,llm,local-inference``).",
)
@click.option(
    "--category", default=None,
    help="Category slug (e.g. ``hardware``, ``ai-ml``).",
)
@click.option(
    "--source", default="manual", show_default=True,
    help="Origin label — recorded on the artifact for queue filtering.",
)
@click.option(
    "--target-length", type=int, default=1500, show_default=True,
    help="Target word count for the eventual draft.",
)
@click.option(
    "--style", default="technical", show_default=True,
    help="Writing style (``technical`` | ``narrative`` | ``listicle`` | ``educational``).",
)
@click.option(
    "--tone", default="professional", show_default=True,
    help="Tone (``professional`` | ``casual``).",
)
@click.option("--json", "json_output", is_flag=True, help="Print as JSON.")
def topics_propose_command(
    topic_text: str,
    primary_keyword: str | None,
    tags: str | None,
    category: str | None,
    source: str,
    target_length: int,
    style: str,
    tone: str,
    json_output: bool,
) -> None:
    """Manually inject a topic into the topic-decision queue.

    Creates a ``pipeline_tasks`` row with ``status='pending'``, then
    routes it through the gate so it lands at
    ``awaiting_gate='topic_decision'`` (when the gate is enabled).
    Manual proposals share the same queue as anticipation_engine's
    auto-proposals so the operator drains them uniformly.

    With the gate disabled (default), the row is left at
    ``status='pending'`` and the worker picks it up as a normal
    content task — the gate is opt-in.
    """
    from services.topic_proposal_service import propose_topic

    if not topic_text or not topic_text.strip():
        _exit_error("--topic must be a non-empty string")
        return

    tags_list = [t.strip() for t in (tags or "").split(",") if t.strip()]

    async def _impl():
        pool = await _make_pool()
        try:
            site_config = await _make_site_config(pool)
            return await propose_topic(
                topic=topic_text,
                primary_keyword=primary_keyword,
                tags=tags_list,
                category=category,
                source=source,
                target_length=target_length,
                style=style,
                tone=tone,
                site_config=site_config,
                pool=pool,
            )
        finally:
            await pool.close()

    try:
        result = _run(_impl())
    except ValueError as e:
        # propose_topic raises ValueError for empty topic — surface as
        # a clean CLI error rather than an unexpected traceback.
        _exit_error(str(e))
        return
    except Exception as e:
        _exit_error(f"unexpected: {type(e).__name__}: {e}")
        return

    if json_output:
        click.echo(json.dumps(result, indent=2, default=str))
        return

    if not result.get("ok"):
        # Queue-full path is the most likely "ok=False" — render
        # the detail string the service module supplies.
        _exit_error(result.get("detail") or "propose_topic returned ok=False")
        return

    if result.get("awaiting_gate"):
        click.secho(
            f"Proposed topic queued for review (task {result['task_id']}).",
            fg="green",
        )
        click.echo(f"  topic:       {result['topic']}")
        click.echo(f"  source:      {source}")
        click.echo(
            "  Drain the queue with `poindexter topics list` + "
            "`poindexter topics approve|reject`.",
        )
    else:
        # Gate off — landed at pending, will run end-to-end without
        # operator intervention. Tell the operator that's what
        # happened so they don't expect to see it in the queue.
        click.secho(
            f"Proposed topic queued at status=pending (task {result['task_id']}).",
            fg="green",
        )
        click.echo(
            "  Note: pipeline_gate_topic_decision is OFF — the worker "
            "will run this end-to-end."
        )


__all__ = ["topics_group"]
