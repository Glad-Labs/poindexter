"""``poindexter pipeline`` — operator interface for interrupt()-paused pipelines.

These commands drive the LangGraph ``interrupt()``-based approval gates
(Glad-Labs/poindexter#363). When an ``approval_gate`` atom trips, the graph
durably checkpoints (Postgres checkpointer, keyed on ``thread_id`` =
``task_id``) and pauses mid-execution. The operator resumes it here:

    poindexter pipeline list-paused             # tasks waiting at a gate
    poindexter pipeline status <task_id>        # one task's gate state
    poindexter pipeline resume <task_id>        # approve + resume the graph

``resume`` records the approval in ``pipeline_gate_history`` (so the gate
atom's idempotency check sees it) and then re-invokes the template with
``resume=True`` — LangGraph loads the checkpoint and continues from after the
gate, skipping every already-run node.

**Atomicity (resume-atomicity fix).** The approval write happens BEFORE the
resume, so a resume failure used to strand the task: ``awaiting_gate`` cleared
(not re-resumable) plus a stale ``approved`` row that the stale-inprogress
sweep would later let auto-pass a fresh run — republishing with no operator
review. ``resume`` now treats the approval as *provisional*:

- **approve-and-resume** (paused at a gate): if the resume RAISES — the graph
  never advanced past the gate (e.g. the checkpointer couldn't be set up) — the
  approval is rolled back (``rollback_resume_approval``: delete the row +
  restore the pause), so the task stays re-resumable and no stale approval
  lingers.
- **continue-resume** (stranded PAST the gate): when a downstream node crashed
  AFTER the gate passed, ``awaiting_gate`` is already NULL but the checkpoint is
  intact. ``resume`` detects this (``in_progress`` + a prior approval +
  ``has_resumable_checkpoint``) and continues from the checkpoint WITHOUT
  re-approving.

Defense-in-depth: ``approve`` stamps the task's ``retry_count`` into the
``approved`` row, and the gate atom only honors an approval whose attempt
matches the task's current ``retry_count`` (the sweep bumps it) — so even if a
rollback is missed (CLI crash), a stale approval can never auto-pass a
post-sweep fresh run. See ``modules/content/atoms/approval_gate.py::_gate_decision``.

NOTE: resume REQUIRES the Postgres checkpointer
(``template_runner_use_postgres_checkpointer=true`` — the prod default).
With MemorySaver the checkpoint does not survive across processes, so a
fresh CLI process can't find the paused state.

Single source of truth for the approval write (and its rollback) is
:mod:`services.approval_service`; the resume invocation reuses
:class:`services.template_runner.TemplateRunner`. This module is a thin
Click wrapper that resolves a DSN + SiteConfig and renders results.
"""

from __future__ import annotations

import asyncio
import json
import sys
from typing import Any

import click

from poindexter.cli._bootstrap import resolve_dsn as _dsn
from poindexter.cli._prefix import resolve_uuid_prefix


def _ensure_selector_event_loop_on_windows() -> None:
    """Switch to the SelectorEventLoop on Windows before ``asyncio.run``.

    ``pipeline resume`` depends on LangGraph's ``AsyncPostgresSaver`` (psycopg3)
    to load the durable checkpoint the worker wrote when the graph paused at a
    gate. psycopg3's async mode CANNOT run on Windows' default
    ``ProactorEventLoop`` — it raises ``InterfaceError``, which
    ``TemplateRunner._resolve_checkpointer`` catches and degrades to
    ``MemorySaver``. A fresh ``MemorySaver`` holds no checkpoint, so LangGraph
    re-runs the graph from its entry node with the CLI's thin initial state
    (no ``post_id``) and seo_refresh halts at ``content.load_existing_post``.

    asyncpg (the CLI pool) runs on either loop, so this switch is safe for
    every pipeline subcommand. Mirrors
    ``scripts/smoke_371_postgres_checkpointer.py``.
    """
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


def _run(coro):
    _ensure_selector_event_loop_on_windows()
    return asyncio.run(coro)


class _PoolShim:
    """Minimal ``database_service``-shaped object exposing only ``.pool``.

    The gate atom + stage adapters read ``state['database_service'].pool``;
    a full DatabaseService needs DSN resolution + an ``initialize()`` round
    trip we don't want for a one-shot CLI resume.
    """

    def __init__(self, pool: Any) -> None:
        self.pool = pool


async def _make_pool():
    import asyncpg

    from poindexter.cli._bootstrap import ensure_secret_key

    # Load POINDEXTER_SECRET_KEY from bootstrap.toml into env so the
    # in-process graph's downstream ``plugins.secrets.get_secret`` calls
    # succeed — ``storage_secret_key`` (R2 upload) and ``revalidate_secret``
    # (Next.js ISR) are both is_secret rows. Worker containers have this set
    # at startup; a bare ``poindexter pipeline …`` shell doesn't, so without
    # it ``resume`` runs the graph but every secret read raises and the R2
    # upload + ISR revalidation silently skip, leaving a republish
    # half-propagated. Same fix as ``dev_diary._open_pool``.
    ensure_secret_key()
    return await asyncpg.create_pool(_dsn(), min_size=1, max_size=2)


async def _make_site_config(pool):
    from services.site_config import SiteConfig

    cfg = SiteConfig(pool=pool)
    try:
        await cfg.load(pool)
    except Exception as exc:  # noqa: BLE001 — best-effort; CLI still works on defaults
        click.echo(
            f"Warning: could not load site_config from DB ({exc}); "
            "using defaults.",
            err=True,
        )
    return cfg


def _exit_error(msg: str, code: int = 1) -> None:
    click.echo(f"Error: {msg}", err=True)
    sys.exit(code)


_TASK_COLUMNS = """
    SELECT task_id::text AS task_id,
           status,
           awaiting_gate,
           gate_artifact,
           gate_paused_at,
           topic,
           template_slug
      FROM pipeline_tasks
"""


async def _fetch_paused_row(pool: Any, task_id: str) -> dict[str, Any] | None:
    """Return the gate-relevant columns for a task, or None.

    Accepts full UUIDs or unambiguous prefixes (like ``git log --short``).
    Prefix resolution is delegated to the shared CLI resolver
    (:func:`poindexter.cli._prefix.resolve_uuid_prefix`); an ambiguous
    prefix raises ``AmbiguousPrefixError`` (a ``click.UsageError``).
    """
    resolved = await resolve_uuid_prefix(
        pool,
        table="pipeline_tasks",
        column="task_id",
        prefix=str(task_id),
        noun="task",
    )
    if resolved is None:
        return None

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            _TASK_COLUMNS + " WHERE task_id::text = $1",
            resolved,
        )
    return dict(row) if row is not None else None


@click.group(name="pipeline", help="Inspect + resume interrupt()-paused pipelines.")
def pipeline_group() -> None:
    pass


# ---------------------------------------------------------------------------
# list-paused
# ---------------------------------------------------------------------------


@pipeline_group.command("list-paused")
@click.option("--limit", type=int, default=100, show_default=True)
@click.option("--json", "json_output", is_flag=True)
def list_paused_command(limit: int, json_output: bool) -> None:
    """List every task currently paused at a gate (awaiting_gate IS NOT NULL)."""

    async def _impl():
        pool = await _make_pool()
        try:
            async with pool.acquire() as conn:
                rows = await conn.fetch(
                    """
                    SELECT task_id::text AS task_id,
                           awaiting_gate AS gate_name,
                           gate_paused_at,
                           status,
                           topic,
                           template_slug
                      FROM pipeline_tasks
                     WHERE awaiting_gate IS NOT NULL
                     ORDER BY gate_paused_at ASC NULLS LAST
                     LIMIT $1
                    """,
                    limit,
                )
            return [dict(r) for r in rows]
        finally:
            await pool.close()

    try:
        rows = _run(_impl())
    except Exception as e:
        _exit_error(f"{type(e).__name__}: {e}")
        return

    if json_output:
        click.echo(json.dumps(rows, indent=2, default=str))
        return

    if not rows:
        click.echo("(no paused pipelines)")
        return

    click.secho(f"Paused pipelines: {len(rows)}", fg="cyan")
    click.echo()
    for row in rows:
        tid = (row.get("task_id") or "")[:8]
        gate = row.get("gate_name") or "?"
        paused = row.get("gate_paused_at") or "-"
        topic = (row.get("topic") or "")[:50]
        click.secho(f"  {tid}  {gate:<20} {topic}", fg="yellow")
        click.secho(
            f"    paused_at={paused}  template={row.get('template_slug')}",
            fg="bright_black",
        )


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------


@pipeline_group.command("status")
@click.argument("task_id")
@click.option("--json", "json_output", is_flag=True)
def status_command(task_id: str, json_output: bool) -> None:
    """Show whether a task is paused at a gate, which gate, and since when."""

    async def _impl():
        pool = await _make_pool()
        try:
            return await _fetch_paused_row(pool, task_id)
        finally:
            await pool.close()

    try:
        row = _run(_impl())
    except Exception as e:
        _exit_error(f"{type(e).__name__}: {e}")
        return

    if row is None:
        _exit_error(f"Task {task_id} not found")
        return

    paused = row.get("awaiting_gate") is not None
    payload = {
        "task_id": row["task_id"],
        "paused": paused,
        "gate_name": row.get("awaiting_gate"),
        "gate_paused_at": (
            row["gate_paused_at"].isoformat()
            if row.get("gate_paused_at") else None
        ),
        "status": row.get("status"),
        "topic": row.get("topic"),
        "template_slug": row.get("template_slug"),
    }

    if json_output:
        click.echo(json.dumps(payload, indent=2, default=str))
        return

    click.secho(f"Task {payload['task_id']}", fg="cyan", bold=True)
    if paused:
        click.secho(f"  PAUSED at gate {payload['gate_name']!r}", fg="yellow")
        click.echo(f"  since        {payload['gate_paused_at']}")
    else:
        click.secho("  not paused at any gate", fg="green")
    click.echo(f"  status       {payload['status']}")
    click.echo(f"  template     {payload['template_slug']}")
    click.echo(f"  topic        {payload['topic']}")
    if paused:
        click.echo()
        click.echo(f"  Resume with: poindexter pipeline resume {payload['task_id']}")


# ---------------------------------------------------------------------------
# resume
# ---------------------------------------------------------------------------


@pipeline_group.command("resume")
@click.argument("task_id")
@click.option("--feedback", default=None, help="Optional operator note (recorded in gate_history).")
@click.option("--json", "json_output", is_flag=True)
def resume_command(task_id: str, feedback: str | None, json_output: bool) -> None:
    """Approve the active gate and resume the interrupted graph.

    Two paths, picked from the task's state:

    **approve-and-resume** (task paused at a gate). Records the approval in
    ``pipeline_gate_history`` and re-invokes the template with ``resume=True``.
    The approval is *provisional*: if the resume RAISES (the graph never
    advanced past the gate — e.g. the Postgres checkpointer couldn't be set
    up), it is rolled back (approval row deleted + pause restored) so the task
    stays re-resumable and no stale approval can auto-pass a later fresh run.

    **continue-resume** (task stranded PAST its gate). When ``awaiting_gate`` is
    already NULL but the task is still ``in_progress`` with an intact checkpoint
    and a prior approval on record — the shape left by a downstream node
    crashing AFTER the gate passed — resume from the checkpoint WITHOUT
    re-approving (the gate was already cleared, the approval is still valid).
    """

    async def _impl():
        pool = await _make_pool()
        try:
            row = await _fetch_paused_row(pool, task_id)
            if row is None:
                return {"error": f"Task {task_id} not found", "code": 1}

            task_id_str = str(row["task_id"])
            template_slug = row.get("template_slug")
            if not template_slug:
                return {
                    "error": (
                        f"Task {task_id} has no template_slug — cannot resume"
                    ),
                    "code": 1,
                }

            site_config = await _make_site_config(pool)

            # We need the live service handles re-threaded into the
            # RunnableConfig, so build a minimal context with database_service +
            # site_config. A full DatabaseService needs DSN + an initialize()
            # round trip; the gate atom (and stage adapters) only read
            # ``.pool``, so a tiny shim exposing the CLI pool is sufficient.
            db_service = _PoolShim(pool)

            from services.template_runner import (
                TemplateRunner,
                has_resumable_checkpoint,
            )

            async def _run_resume(resume_gate: str | None):
                runner = TemplateRunner(pool, site_config=site_config)
                return await runner.run(
                    template_slug,
                    {
                        "task_id": task_id_str,
                        "topic": row.get("topic") or "",
                        "database_service": db_service,
                        "site_config": site_config,
                    },
                    thread_id=task_id_str,
                    resume=True,
                    resume_value={"approved": True, "gate_name": resume_gate},
                )

            gate_name = row.get("awaiting_gate")

            # ---- PATH 1: approve-and-resume (paused at a gate) -------------
            if gate_name:
                # Capture the paused state BEFORE approve clears it, so a
                # failed resume can be compensated back to exactly here.
                original_artifact = row.get("gate_artifact")
                original_paused_at = row.get("gate_paused_at")

                from services.approval_service import approve as approve_service
                from services.approval_service import rollback_resume_approval

                approval = await approve_service(
                    task_id=task_id_str,
                    gate_name=gate_name,
                    feedback=feedback,
                    actor="human",
                    site_config=site_config,
                    pool=pool,
                )

                try:
                    summary = await _run_resume(gate_name)
                except Exception as exc:  # noqa: BLE001 — surfaced below
                    # The resume raised before the gate was durably passed
                    # (the graph never advanced). Roll the approval back so the
                    # task is re-resumable and no stale approval lingers.
                    await rollback_resume_approval(
                        task_id=task_id_str,
                        gate_name=gate_name,
                        gate_history_id=approval.get("gate_history_id"),
                        artifact=original_artifact,
                        paused_at=original_paused_at,
                        pool=pool,
                    )
                    return {
                        "error": (
                            f"resume failed — approval rolled back, task is "
                            f"re-resumable: {type(exc).__name__}: {exc}"
                        ),
                        "code": 1,
                        "rolled_back": True,
                    }

                return {
                    "ok": summary.ok,
                    "task_id": task_id_str,
                    "gate_name": gate_name,
                    "template_slug": template_slug,
                    "halted_at": summary.halted_at,
                    "approval": approval,
                    "mode": "approve_resume",
                }

            # ---- PATH 2: continue-resume (stranded past the gate) ----------
            # awaiting_gate is NULL. If the task is an operator-driven resume
            # that died past the gate (in_progress + intact checkpoint + a
            # prior approval), continue from the checkpoint without re-approving.
            from services.approval_service import latest_approved_gate

            approved_gate = await latest_approved_gate(pool, task_id_str)
            resumable = (
                str(row.get("status")) == "in_progress"
                and approved_gate is not None
                and await has_resumable_checkpoint(pool, task_id_str)
            )
            if resumable:
                summary = await _run_resume(approved_gate)
                return {
                    "ok": summary.ok,
                    "task_id": task_id_str,
                    "gate_name": approved_gate,
                    "template_slug": template_slug,
                    "halted_at": summary.halted_at,
                    "mode": "continue_resume",
                }

            return {
                "error": (
                    f"Task {task_id} is not paused at a gate "
                    f"(status={row.get('status')!r}) — nothing to resume"
                ),
                "code": 1,
            }
        finally:
            await pool.close()

    try:
        result = _run(_impl())
    except Exception as e:
        _exit_error(f"unexpected: {type(e).__name__}: {e}")
        return

    if "error" in result:
        _exit_error(result["error"], code=result.get("code", 1))
        return

    if json_output:
        click.echo(json.dumps(result, indent=2, default=str))
        return

    verb = "Continued" if result.get("mode") == "continue_resume" else "Resumed"
    if result.get("ok"):
        click.secho(
            f"{verb} task {result['task_id']} past gate "
            f"{result['gate_name']!r} — pipeline completed.",
            fg="green",
        )
    else:
        click.secho(
            f"{verb} task {result['task_id']} past gate "
            f"{result['gate_name']!r}, but the pipeline halted at "
            f"{result.get('halted_at')!r}.",
            fg="yellow",
        )


__all__ = [
    "pipeline_group",
    "list_paused_command",
    "status_command",
    "resume_command",
]
