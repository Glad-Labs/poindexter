"""``trace_read`` — the assembling read layer for the console task-trace.

One read-only service stitches a request's whole story from the tables that
already hold it, so the operator sees it in one place instead of scattered
across surfaces:

- ``atom_runs`` — the node spine (per-node status / model / latency / cost /
  ``output_preview``), the QA rail verdicts (the ``qa.*`` nodes), and the halt
  anchor.
- ``pipeline_tasks`` — live progress (stage / percentage / message) + task meta.
- ``pipeline_versions.stage_data`` — the corpus the writer was handed
  (``task_metadata.research_context``) + the final content.
- ``pipeline_gate_history`` — HITL gate decisions (approve / regen / reject).
- ``cost_logs`` — the per-model spend rollup.

**Trace unit = a request → 1..N runs** keyed ``(task_id, run_id, template_slug)``.
:func:`runs_for_request` is the single grouping seam (spec R7): a retry is another
run of the same template, a composed media DAG is another run under the same
``task_id`` — so the arbitrary-composition roadmap ("make an image", "Q&A
podcast", "blog + short video") needs no redesign here.

Everything is read-only and guards to **honest-empty** (``{}`` / ``[]`` / ``""``
/ ``None``) on error — a scattered or partial run must never make the deep-dive
throw or fabricate a spine or a number. Mirrors the capture layer's best-effort
posture.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# Templates whose runs are composed media DAGs rather than the primary content
# run — used only to label a run's ``kind`` (they run under the same task_id).
_MEDIA_TEMPLATES = {"media_pipeline", "podcast_pipeline"}
# atom_runs.status values that mean "this node stopped the run".
_HALT_STATUSES = ("halted", "error")


# ---------------------------------------------------------------------------
# runs_for_request — the R7 grouping seam
# ---------------------------------------------------------------------------

_RUNS_SQL = """
    SELECT run_id,
           template_slug,
           count(*)                                            AS node_count,
           bool_or(status IN ('halted','error'))              AS halted,
           min(seq) FILTER (WHERE status IN ('halted','error')) AS halted_at,
           max(created_at)                                     AS last_at
      FROM atom_runs
     WHERE task_id = $1
     GROUP BY run_id, template_slug
     ORDER BY max(created_at) DESC
"""


async def runs_for_request(pool: Any, task_id: str) -> list[dict]:
    """Gather a request's 1..N runs, grouped by ``(run_id, template_slug)``.

    Returns ``[{run_id, template_slug, kind, status, node_count, halted_at}]``,
    ordered **content-first** (the latest content run first — the default
    deep-dive selection), then retries, then media. ``kind`` is ``"media"`` for
    a composed media DAG, else ``"content"`` for the newest non-media run and
    ``"retry"`` for older non-media runs. Honest-empty on error.
    """
    if pool is None or not task_id:
        return []
    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(_RUNS_SQL, task_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[trace_read] runs_for_request failed for %s: %s", task_id, exc)
        return []

    runs: list[dict] = []
    seen_content = False
    for r in rows:  # newest-first from the query
        template = r["template_slug"] or ""
        if template in _MEDIA_TEMPLATES:
            kind = "media"
        elif not seen_content:
            kind = "content"  # newest non-media run is the canonical content run
            seen_content = True
        else:
            kind = "retry"
        runs.append({
            "run_id": r["run_id"],
            "template_slug": template,
            "kind": kind,
            "status": "halted" if r["halted"] else "ok",
            "node_count": int(r["node_count"] or 0),
            "halted_at": r["halted_at"],  # int seq of first halted node, or None
        })

    # Content first (default selection), then retries, then media; stable within
    # each group preserves the newest-first ordering from the query.
    order = {"content": 0, "retry": 1, "media": 2}
    runs.sort(key=lambda x: order.get(x["kind"], 3))
    return runs


# ---------------------------------------------------------------------------
# get_active — the front-door board
# ---------------------------------------------------------------------------

_ACTIVE_SQL = """
    SELECT pt.task_id, pt.topic, pt.template_slug, pt.niche_slug, pt.status,
           pt.stage, pt.percentage, pt.message, pt.started_at, pt.last_progress_at,
           (SELECT count(*) FROM atom_runs ar WHERE ar.run_id = pt.task_id) AS nodes_done
      FROM pipeline_tasks pt
     WHERE pt.status IN ('pending','in_progress','awaiting_gate')
     ORDER BY pt.started_at DESC NULLS LAST
"""

_RECENT_SQL = """
    SELECT task_id, topic, template_slug, status, started_at, completed_at
      FROM pipeline_tasks
     WHERE status IN ('approved','awaiting_approval','published','rejected',
                      'rejected_final','failed','cancelled')
     ORDER BY COALESCE(completed_at, updated_at) DESC
     LIMIT $1
"""


async def get_active(pool: Any, recent_limit: int = 10) -> dict:
    """Front-door board: currently-running tasks (with live stage/percentage)
    plus the last ``recent_limit`` finished tasks. Honest-empty
    (``{"runs": [], "recent": []}``) on error."""
    out: dict[str, list] = {"runs": [], "recent": []}
    if pool is None:
        return out
    try:
        async with pool.acquire() as conn:
            active = await conn.fetch(_ACTIVE_SQL)
            recent = await conn.fetch(_RECENT_SQL, int(recent_limit or 10))
    except Exception as exc:  # noqa: BLE001
        logger.warning("[trace_read] get_active failed: %s", exc)
        return out
    out["runs"] = [dict(r) for r in active]
    out["recent"] = [dict(r) for r in recent]
    return out


# ---------------------------------------------------------------------------
# get_summary — 24h KPIs (the board's health strip)
# ---------------------------------------------------------------------------

_SUMMARY_SQL = """
    SELECT count(*)                                                       AS tasks,
           count(*) FILTER (WHERE status IN ('approved','published'))     AS approved,
           count(*) FILTER (WHERE status IN ('rejected','rejected_final','failed')) AS rejected
      FROM pipeline_tasks
     WHERE created_at >= now() - interval '24 hours'
"""


async def get_summary(pool: Any) -> dict:
    """24h KPIs for the board health strip: task count, per-status breakdown,
    pass-rate, average quality, average per-task cost. Always a dict with the
    KPI keys (values may be ``None``/``0``); honest-empty on error."""
    out: dict[str, Any] = {
        "window_hours": 24, "tasks": 0, "by_status": {},
        "pass_rate": None, "avg_quality": None, "avg_cost_usd": None,
    }
    if pool is None:
        return out
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(_SUMMARY_SQL)
            by_status = await conn.fetch(
                "SELECT status, count(*) AS n FROM pipeline_tasks "
                "WHERE created_at >= now() - interval '24 hours' GROUP BY status"
            )
            avg_q = await conn.fetchval(
                "SELECT avg(quality_score) FROM atom_runs "
                "WHERE created_at >= now() - interval '24 hours' "
                "AND quality_score IS NOT NULL"
            )
            avg_cost = await conn.fetchval(
                "SELECT avg(t.c) FROM ("
                "  SELECT sum(cost_usd) AS c FROM cost_logs "
                "  WHERE created_at >= now() - interval '24 hours' GROUP BY task_id"
                ") t"
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[trace_read] get_summary failed: %s", exc)
        return out

    approved = int(row["approved"] or 0) if row else 0
    rejected = int(row["rejected"] or 0) if row else 0
    decided = approved + rejected
    out["tasks"] = int(row["tasks"] or 0) if row else 0
    out["by_status"] = {r["status"]: int(r["n"]) for r in by_status}
    out["pass_rate"] = round(approved / decided, 3) if decided else None
    out["avg_quality"] = round(float(avg_q), 1) if avg_q is not None else None
    out["avg_cost_usd"] = round(float(avg_cost), 4) if avg_cost is not None else None
    return out


# ---------------------------------------------------------------------------
# get_trace — the per-task deep-dive assembly
# ---------------------------------------------------------------------------


async def get_trace(pool: Any, task_id: str, run_id: str | None = None) -> dict:
    """Assemble the per-task deep-dive: run selection, node spine, corpus, QA
    rail verdicts, HITL decisions, cost rollup, final post, and the halt anchor
    (for open-on-halt). Every section guards independently to honest-empty, so a
    partial or scattered run still renders what exists.

    ``run_id=None`` selects the default run (the latest content run, else the
    most recent run). The Langfuse session id is the ``task_id`` (the route
    composes the deeplink from ``langfuse_public_url``); ``run_id`` scopes it.
    """
    trace: dict[str, Any] = {
        "task_id": task_id,
        "run_id": run_id,
        "runs": [],
        "task": None,
        "nodes": [],
        "corpus": "",
        "decisions": [],
        "qa": [],
        "cost_rollup": {"by_model": [], "total_usd": 0.0},
        "final": None,
        "halt": None,
        "langfuse": {"session_id": task_id, "run_id": run_id},
    }
    if pool is None or not task_id:
        return trace

    # 1. Runs under this request + default selection (content-first).
    runs = await runs_for_request(pool, task_id)
    trace["runs"] = runs
    if run_id is None:
        content = next((r for r in runs if r["kind"] == "content"), None)
        run_id = (
            content["run_id"] if content
            else (runs[0]["run_id"] if runs else task_id)
        )
    trace["run_id"] = run_id
    trace["langfuse"]["run_id"] = run_id

    try:
        async with pool.acquire() as conn:
            # 2. Node spine of the selected run.
            node_rows = await conn.fetch(
                """
                SELECT seq, atom, node_id, status, tier, model, latency_ms,
                       cost, output_keys, output_preview
                  FROM atom_runs WHERE run_id = $1 ORDER BY seq
                """,
                run_id,
            )
            trace["nodes"] = [dict(r) for r in node_rows]

            # 3. Task meta (live progress + identity).
            task_row = await conn.fetchrow(
                """
                SELECT task_id, topic, status, stage, template_slug, niche_slug,
                       percentage, message, created_at, started_at, completed_at,
                       last_progress_at
                  FROM pipeline_tasks WHERE task_id = $1
                """,
                task_id,
            )
            trace["task"] = dict(task_row) if task_row else None

            # 4. Corpus + final content (latest version). research_context is the
            #    name→URL bullet corpus the writer was handed (persisted on
            #    stage_data.task_metadata since #553).
            ver = await conn.fetchrow(
                """
                SELECT title, quality_score, content,
                       stage_data -> 'task_metadata' ->> 'research_context' AS corpus
                  FROM pipeline_versions
                 WHERE task_id = $1 ORDER BY version DESC LIMIT 1
                """,
                task_id,
            )
            if ver:
                trace["corpus"] = ver["corpus"] or ""
                trace["final"] = {
                    "title": ver["title"],
                    "quality_score": ver["quality_score"],
                    "content_length": len(ver["content"] or ""),
                }

            # 5. HITL gate decisions.
            dec_rows = await conn.fetch(
                """
                SELECT gate_name, event_kind, feedback, actor, created_at
                  FROM pipeline_gate_history WHERE task_id = $1 ORDER BY created_at
                """,
                task_id,
            )
            trace["decisions"] = [dict(r) for r in dec_rows]

            # 6. Cost rollup by model over the whole task's cost_logs.
            cost_rows = await conn.fetch(
                """
                SELECT model, provider, sum(cost_usd) AS usd,
                       sum(total_tokens) AS tokens, count(*) AS calls
                  FROM cost_logs WHERE task_id = $1
                 GROUP BY model, provider ORDER BY sum(cost_usd) DESC NULLS LAST
                """,
                task_id,
            )
            trace["cost_rollup"] = {
                "by_model": [dict(r) for r in cost_rows],
                "total_usd": float(sum((r["usd"] or 0) for r in cost_rows)),
            }
    except Exception as exc:  # noqa: BLE001
        logger.warning("[trace_read] get_trace assembly failed for %s: %s", task_id, exc)

    # 7. Derived from the spine: QA rail verdicts + the halt anchor. Done in
    #    Python so a query failure above still yields whatever nodes loaded.
    trace["qa"] = [
        {
            "seq": n["seq"], "atom": n["atom"], "status": n["status"],
            "preview": n.get("output_preview"),
        }
        for n in trace["nodes"]
        if str(n.get("atom") or "").startswith("qa.")
    ]
    halted = next(
        (n for n in trace["nodes"] if n.get("status") in _HALT_STATUSES), None
    )
    if halted:
        trace["halt"] = {
            "seq": halted["seq"],
            "atom": halted["atom"],
            "reason": halted.get("output_preview") or halted.get("status"),
        }
    return trace


__all__ = ["runs_for_request", "get_active", "get_summary", "get_trace"]
