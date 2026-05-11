"""`poindexter tasks` subcommands — wraps the worker task API.

Replaces the four individual openclaw skills `create-post`, `list-tasks`,
`approve-post`, `reject-post`, `publish-post`. One code path, typed flags,
proper `--help`. Subcommand groups can be slotted onto this later
(e.g. `poindexter tasks retry`, `poindexter tasks cancel`).
"""

from __future__ import annotations

import asyncio
import json
import sys
from typing import Any

import click

from ._api_client import WorkerClient


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Group root
# ---------------------------------------------------------------------------


@click.group(name="tasks", help="Manage the content pipeline task queue.")
def tasks_group() -> None:
    pass


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------

_VALID_STATUSES = (
    "pending",
    "in_progress",
    "awaiting_approval",
    "published",
    "rejected",
    "failed",
    "cancelled",
    "all",
)


@tasks_group.command("list")
@click.option(
    "--status",
    type=click.Choice(_VALID_STATUSES, case_sensitive=False),
    default="all",
    show_default=True,
    help="Filter by task status. 'all' returns every status.",
)
@click.option("--limit", type=int, default=20, show_default=True)
@click.option("--json", "json_output", is_flag=True, help="Emit JSON instead of human text.")
def tasks_list(status: str, limit: int, json_output: bool) -> None:
    """List recent tasks, optionally filtered by status.

    Note: the legacy statuses 'completed' and 'approved' were removed from
    the pipeline in 2026-04 — use 'published' for what the site has live
    and 'awaiting_approval' for the human-review queue.
    """
    params: dict[str, str | int] = {"limit": limit}
    if status and status != "all":
        params["status"] = status

    async def _list():
        async with WorkerClient() as c:
            resp = await c.get("/api/tasks", params=params)
            return await c.json_or_raise(resp)

    try:
        data = _run(_list())
    except RuntimeError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)

    tasks = data.get("tasks") or []
    if json_output:
        click.echo(json.dumps(tasks, indent=2, default=str))
        return

    if not tasks:
        click.echo("(no tasks)")
        return

    total = data.get("total", len(tasks))
    click.secho(f"Tasks: {len(tasks)} shown / {total} total", fg="cyan")
    click.echo()
    for t in tasks:
        _print_task_one_line(t)


def _print_task_one_line(t: dict) -> None:
    tid = (t.get("id") or t.get("task_id") or "?")[:8]
    status = t.get("status") or "?"
    quality = t.get("quality_score")
    quality_str = f"Q:{quality:<5}" if quality is not None else "Q:-   "
    title = t.get("title") or t.get("task_name") or t.get("topic") or "?"
    color = {
        "pending": "white",
        "in_progress": "yellow",
        "awaiting_approval": "cyan",
        "published": "green",
        "rejected": "red",
        "failed": "red",
        "cancelled": "white",
    }.get(status, "white")
    click.secho(
        f"  {tid}  {status:<18} {quality_str}  {title[:70]}",
        fg=color,
    )


# ---------------------------------------------------------------------------
# get
# ---------------------------------------------------------------------------


@tasks_group.command("get")
@click.argument("task_id")
@click.option("--json", "json_output", is_flag=True)
@click.option("--content", is_flag=True, help="Also print the full content field.")
def tasks_get(task_id: str, json_output: bool, content: bool) -> None:
    """Show details for a single task by id (full or prefix)."""

    async def _get():
        async with WorkerClient() as c:
            resp = await c.get(f"/api/tasks/{task_id}")
            return await c.json_or_raise(resp)

    try:
        t = _run(_get())
    except RuntimeError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)

    if json_output:
        if not content and isinstance(t, dict):
            t = {k: v for k, v in t.items() if k != "content"}
        click.echo(json.dumps(t, indent=2, default=str))
        return

    click.secho(
        f"Task {t.get('id', '?')}",
        fg="cyan", bold=True,
    )
    for key in (
        "status", "task_type", "topic", "category", "target_audience",
        "model_used", "quality_score", "created_at", "updated_at",
        "featured_image_url",
    ):
        val = t.get(key)
        if val is not None:
            click.echo(f"  {key:18s} {val}")
    err = t.get("error_message")
    if err:
        click.secho(f"  error_message:\n    {err}", fg="red")
    if content and t.get("content"):
        click.echo()
        click.secho("--- content ---", fg="cyan")
        click.echo(t["content"])


# ---------------------------------------------------------------------------
# create
# ---------------------------------------------------------------------------


@tasks_group.command("create")
@click.argument("topic")
@click.option("--category", default="technology", show_default=True)
@click.option("--audience", "target_audience", default="developers and founders", show_default=True)
@click.option("--keyword", "primary_keyword", default="")
@click.option("--style", default="narrative", show_default=True)
@click.option("--tone", default="professional", show_default=True)
@click.option("--length", "target_length", type=int, default=1500, show_default=True)
def tasks_create(
    topic: str,
    category: str,
    target_audience: str,
    primary_keyword: str,
    style: str,
    tone: str,
    target_length: int,
) -> None:
    """Queue a new content task.

    Example:

        poindexter tasks create "Why VRAM bandwidth matters for LLM inference"
    """
    payload = {
        "task_name": f"Blog post: {topic}",
        "topic": topic,
        "category": category,
        "target_audience": target_audience,
        "primary_keyword": primary_keyword,
        "style": style,
        "tone": tone,
        "target_length": target_length,
    }

    async def _create():
        async with WorkerClient() as c:
            resp = await c.post("/api/tasks", json=payload)
            return await c.json_or_raise(resp)

    try:
        t = _run(_create())
    except RuntimeError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)

    click.secho(
        f"Created: {t.get('id', '?')}  status={t.get('status', '?')}",
        fg="green",
    )


# ---------------------------------------------------------------------------
# approve / reject / publish
# ---------------------------------------------------------------------------


def _post_action(task_id: str, action: str, payload: dict | None = None) -> dict:
    async def _call():
        async with WorkerClient() as c:
            resp = await c.post(f"/api/tasks/{task_id}/{action}", json=payload or {})
            return await c.json_or_raise(resp)

    return _run(_call())


@tasks_group.command("approve")
@click.argument("task_id")
def tasks_approve(task_id: str) -> None:
    """Approve a task for publishing."""
    try:
        t = _post_action(task_id, "approve")
    except RuntimeError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)
    click.secho(f"Approved: {task_id}  status={t.get('status', '?')}", fg="green")


@tasks_group.command("reject")
@click.argument("task_id")
@click.option(
    "--feedback",
    default="",
    help="Required explanation surfaced on the task's error_message and audit log.",
)
@click.option(
    "--final/--retry",
    default=False,
    help=(
        "--final → terminal rejection (status=rejected_final, no regen). "
        "--retry → send back for revisions (status=rejected_retry, the "
        "worker re-runs the pipeline and the task lands back in "
        "awaiting_approval). Default is --retry to match the API default. "
        "Use --final for stale or off-topic posts you don't want regenerated."
    ),
)
def tasks_reject(task_id: str, feedback: str, final: bool) -> None:
    """Reject a task. --feedback is required by the worker API."""
    if not feedback:
        click.echo(
            "Error: --feedback is required. Give a short reason — it lands on the "
            "task record and the audit log.",
            err=True,
        )
        sys.exit(2)
    # Two different /reject handlers exist on /api/tasks/{id}/reject (one
    # in approval_routes.py wants `feedback`, one in task_publishing_routes.py
    # wants `reason`). Send both so whichever FastAPI routes to is satisfied.
    payload: dict[str, Any] = {
        "feedback": feedback,
        "reason": feedback,
        "allow_revisions": not final,
    }
    try:
        t = _post_action(task_id, "reject", payload)
    except RuntimeError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)
    click.secho(f"Rejected: {task_id}  status={t.get('status', '?')}", fg="yellow")


@tasks_group.command("publish")
@click.argument("task_id")
def tasks_publish(task_id: str) -> None:
    """Manually publish an approved task."""
    try:
        t = _post_action(task_id, "publish")
    except RuntimeError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)
    click.secho(f"Published: {task_id}  status={t.get('status', '?')}", fg="green")


# ---------------------------------------------------------------------------
# bulk-ops: reject-batch / approve-batch
# ---------------------------------------------------------------------------
#
# A batch is any union of three input sources:
#
# 1. Variadic positional task_ids  →  ``poindexter tasks reject-batch <id1> <id2>``
# 2. ``--filter "<SQL WHERE>"``    →  runs ``SELECT task_id FROM pipeline_tasks
#                                      WHERE <SQL WHERE>``. The clause is
#                                      passed verbatim — operator-only surface;
#                                      authn is the OAuth token + DB DSN, not
#                                      input sanitisation.
# 3. ``--from-stdin``              →  reads task_ids one-per-line from stdin
#                                      so ``psql … | poindexter tasks reject-batch
#                                      --from-stdin --feedback "..."`` pipes
#                                      cleanly.
#
# Sources are unioned and de-duplicated. ``--dry-run`` prints the plan
# and exits 0 without firing anything. Confirmation prompt fires for
# >5 tasks unless ``--yes`` (matches ``gh pr merge`` defaults).


_BATCH_CONFIRM_THRESHOLD = 5


async def _resolve_filter_ids(filter_clause: str) -> list[str]:
    """Run a SELECT against pipeline_tasks with the operator's WHERE.

    Operator-only surface; no SQL sanitisation. The trust boundary is
    that anyone who can run ``poindexter`` against the local DB
    already holds the DSN.
    """
    import asyncpg

    from poindexter.cli._bootstrap import resolve_dsn as _dsn

    pool = await asyncpg.create_pool(_dsn(), min_size=1, max_size=2)
    try:
        rows = await pool.fetch(
            f"SELECT task_id FROM pipeline_tasks WHERE {filter_clause}"
        )
    finally:
        await pool.close()
    return [r["task_id"] for r in rows]


def _gather_batch_ids(
    args: tuple[str, ...],
    filter_clause: str | None,
    from_stdin: bool,
) -> list[str]:
    """Union the three input sources, preserving first-seen order."""
    seen: dict[str, None] = {}

    for tid in args:
        if tid and tid not in seen:
            seen[tid] = None

    if from_stdin:
        for line in sys.stdin:
            tid = line.strip()
            if tid and not tid.startswith("#") and tid not in seen:
                seen[tid] = None

    if filter_clause:
        for tid in _run(_resolve_filter_ids(filter_clause)):
            if tid not in seen:
                seen[tid] = None

    return list(seen.keys())


def _confirm_or_abort(ids: list[str], action_label: str, yes: bool) -> None:
    """Prompt before bulk operations unless ``--yes`` is set.

    Short-circuit when the count is below the threshold — single-digit
    batches don't need a prompt and rerunning a small command twice is
    cheap.
    """
    if yes or len(ids) <= _BATCH_CONFIRM_THRESHOLD:
        return
    click.echo(f"\nAbout to {action_label} {len(ids)} tasks:")
    for tid in ids[:10]:
        click.echo(f"  {tid}")
    if len(ids) > 10:
        click.echo(f"  ...and {len(ids) - 10} more")
    if not click.confirm("\nProceed?", default=False):
        click.echo("Aborted.")
        sys.exit(2)


def _execute_batch(
    ids: list[str],
    action: str,
    payload: dict | None,
    fg_ok: str,
) -> tuple[int, int]:
    """Loop the post action across every id, never halts on per-task error.

    Returns ``(success_count, fail_count)`` so the caller can compose a
    final summary line and set the exit code. Each per-task error is
    printed inline so operators see *which* tasks failed when scrolling
    back through the output.
    """
    ok = 0
    fail = 0
    for tid in ids:
        try:
            t = _post_action(tid, action, payload)
            status = t.get("status", "?")
            click.secho(f"  ✓ {tid}  status={status}", fg=fg_ok)
            ok += 1
        except RuntimeError as e:
            click.secho(f"  ✗ {tid}  {e}", fg="red")
            fail += 1
    return ok, fail


@tasks_group.command("reject-batch")
@click.argument("task_ids", nargs=-1)
@click.option(
    "--filter",
    "filter_clause",
    default=None,
    help=(
        "SQL WHERE clause appended to ``SELECT task_id FROM pipeline_tasks``. "
        "Example: --filter \"status='awaiting_approval' AND topic LIKE '%batch C%'\"."
    ),
)
@click.option(
    "--from-stdin",
    is_flag=True,
    help="Read task_ids from stdin (one per line). Lines starting with # are ignored.",
)
@click.option(
    "--feedback",
    default="",
    help="Explanation surfaced on each task's error_message + audit log. Required.",
)
@click.option(
    "--final/--retry",
    default=False,
    help=(
        "--final → terminal rejection (rejected_final, no regen). "
        "--retry → send back for revisions (rejected_retry). Default --retry."
    ),
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Print the resolved task list + exit without firing the worker API.",
)
@click.option(
    "--yes",
    "-y",
    is_flag=True,
    help=(
        f"Skip the confirmation prompt that fires for batches > "
        f"{_BATCH_CONFIRM_THRESHOLD} tasks. Match ``gh pr merge`` semantics."
    ),
)
def tasks_reject_batch(
    task_ids: tuple[str, ...],
    filter_clause: str | None,
    from_stdin: bool,
    feedback: str,
    final: bool,
    dry_run: bool,
    yes: bool,
) -> None:
    """Reject multiple tasks in one call.

    Inputs union from positional args, --filter SQL-where, and --from-stdin.
    Worker API is called once per task; per-task failures are surfaced
    inline and counted in the final summary.
    """
    if not feedback:
        click.echo(
            "Error: --feedback is required. It lands on every task's "
            "error_message + the audit log.",
            err=True,
        )
        sys.exit(2)

    ids = _gather_batch_ids(task_ids, filter_clause, from_stdin)
    if not ids:
        click.echo(
            "Error: no task_ids resolved from args / --filter / --from-stdin.",
            err=True,
        )
        sys.exit(2)

    if dry_run:
        click.echo(f"Would reject {len(ids)} task(s):")
        for tid in ids:
            click.echo(f"  {tid}")
        return

    _confirm_or_abort(ids, "reject", yes)

    payload = {"feedback": feedback, "reason": feedback, "allow_revisions": not final}
    ok, fail = _execute_batch(ids, "reject", payload, fg_ok="yellow")

    color = "green" if fail == 0 else ("yellow" if ok > 0 else "red")
    click.secho(
        f"\nRejected: {ok} ok, {fail} failed (of {len(ids)} requested)",
        fg=color,
    )
    sys.exit(0 if fail == 0 else 1)


@tasks_group.command("approve-batch")
@click.argument("task_ids", nargs=-1)
@click.option(
    "--filter",
    "filter_clause",
    default=None,
    help=(
        "SQL WHERE clause appended to ``SELECT task_id FROM pipeline_tasks``. "
        "Example: --filter \"status='awaiting_approval' AND quality_score>=85\"."
    ),
)
@click.option(
    "--from-stdin",
    is_flag=True,
    help="Read task_ids from stdin (one per line).",
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Print the resolved task list + exit without firing the worker API.",
)
@click.option(
    "--yes",
    "-y",
    is_flag=True,
    help=(
        f"Skip the confirmation prompt that fires for batches > "
        f"{_BATCH_CONFIRM_THRESHOLD} tasks."
    ),
)
def tasks_approve_batch(
    task_ids: tuple[str, ...],
    filter_clause: str | None,
    from_stdin: bool,
    dry_run: bool,
    yes: bool,
) -> None:
    """Approve multiple tasks in one call.

    Use with care — approval moves a task from ``awaiting_approval``
    into the publish pipeline. Pair with --dry-run + a tight --filter
    when you're confident the gate signal is right (e.g.
    ``quality_score >= 85 AND topic LIKE '%batch C%'``).
    """
    ids = _gather_batch_ids(task_ids, filter_clause, from_stdin)
    if not ids:
        click.echo(
            "Error: no task_ids resolved from args / --filter / --from-stdin.",
            err=True,
        )
        sys.exit(2)

    if dry_run:
        click.echo(f"Would approve {len(ids)} task(s):")
        for tid in ids:
            click.echo(f"  {tid}")
        return

    _confirm_or_abort(ids, "approve", yes)

    ok, fail = _execute_batch(ids, "approve", None, fg_ok="green")

    color = "green" if fail == 0 else ("yellow" if ok > 0 else "red")
    click.secho(
        f"\nApproved: {ok} ok, {fail} failed (of {len(ids)} requested)",
        fg=color,
    )
    sys.exit(0 if fail == 0 else 1)
