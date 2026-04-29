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
@click.option(
    "--publish", is_flag=True,
    help="Publish immediately instead of just staging (default: stage-only — call `tasks publish` later).",
)
def tasks_approve(task_id: str, publish: bool) -> None:
    """Approve (stage) a task for publishing.

    As of gh#189, approve defaults to STAGING ONLY — task moves to status='approved'
    but NOT 'published'. Use --publish for the legacy "approve = ship it now" behavior,
    or run `poindexter tasks publish <id>` as a separate explicit step.
    """
    payload = {"auto_publish": True} if publish else None
    try:
        t = _post_action(task_id, "approve", payload)
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
def tasks_reject(task_id: str, feedback: str) -> None:
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
    try:
        t = _post_action(task_id, "reject", {"feedback": feedback, "reason": feedback})
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
