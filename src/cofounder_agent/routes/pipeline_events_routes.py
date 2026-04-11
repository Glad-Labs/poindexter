"""
Pipeline events observability endpoints.

Exposes the per-gate QA decisions, rewrite loop events, and other
structured audit_log entries so operators can see WHAT decision is
being made and WHY at every gate or fork in the pipeline.

Three surfaces:
    GET /api/pipeline/events           JSON, filters by event_type / task_id / since
    GET /api/pipeline/events/task/{id} JSON, all events for one task (ordered)
    GET /pipeline                      minimal HTML dashboard, polls the JSON
                                       endpoint every 5s. Designed for mobile.

The HTML view is intentionally zero-dependency — no framework, no
build step, no auth friction. Open it on your phone on the same
network as the worker, or via Tailscale if you're remote.

Consumed by Grafana (via the pipeline_events audit_log view) and
future Discord ops-channel notifier, so the structured field shape
matters — don't rename keys without bumping a version.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse

from services.logger_config import get_logger
from utils.route_utils import get_database_dependency

logger = get_logger(__name__)


async def _get_pool():
    """Get the DB pool via the shared DatabaseService (same pattern
    the CMS routes use — no new service instances per request)."""
    db_service = get_database_dependency()
    return getattr(db_service, "cloud_pool", None) or db_service.pool

router = APIRouter(tags=["pipeline-events"])


# The audit_log event types that represent QA / pipeline decisions.
# Add to this list when a new structured event type is emitted.
_PIPELINE_EVENT_TYPES = (
    "qa_decision",
    "qa_aggregate",
    "qa_passed",
    "qa_failed",
    "rewrite_decision",
    "qa_rewrite_triggered",  # legacy name, same meaning as rewrite_decision
    "task_started",
    "task_created",
    "pipeline_complete",
    "generation_complete",
)


def _format_event(row: dict) -> dict:
    """Flatten a single audit_log row into a uniform dict the frontend
    can render without knowing event-type specifics."""
    details = row.get("details") or {}
    if isinstance(details, str):
        try:
            details = json.loads(details)
        except (ValueError, TypeError):
            details = {"raw": details}
    return {
        "id": row["id"],
        "timestamp": row["timestamp"].isoformat() if row.get("timestamp") else None,
        "event_type": row.get("event_type"),
        "source": row.get("source"),
        "task_id": row.get("task_id"),
        "severity": row.get("severity", "info"),
        "details": details,
    }


@router.get("/api/pipeline/events")
async def list_pipeline_events(
    limit: int = Query(50, ge=1, le=500, description="Max events to return"),
    task_id: str | None = Query(None, description="Filter to a single task"),
    event_type: str | None = Query(
        None, description="Filter to a single event type (e.g. qa_decision)"
    ),
    since_minutes: int = Query(
        60, ge=1, le=1440, description="Only events from the last N minutes (default 60)"
    ),
) -> JSONResponse:
    """List recent pipeline events (QA decisions, rewrites, approvals).

    This is the JSON backbone for the mobile dashboard, the Discord
    notifier, and anything else that wants to see decisions as they
    happen. Filters stack (AND semantics). By default returns the
    last hour of pipeline events, newest first.
    """
    try:
        pool = await _get_pool()
        async with pool.acquire() as conn:
            where: list[str] = ["timestamp > NOW() - ($1::int * INTERVAL '1 minute')"]
            params: list[Any] = [since_minutes]
            idx = 2

            if event_type:
                where.append(f"event_type = ${idx}")
                params.append(event_type)
                idx += 1
            else:
                where.append(f"event_type = ANY(${idx})")
                params.append(list(_PIPELINE_EVENT_TYPES))
                idx += 1

            if task_id:
                where.append(f"task_id = ${idx}")
                params.append(task_id)
                idx += 1

            sql = (
                "SELECT id, timestamp, event_type, source, task_id, details, severity "
                "FROM audit_log WHERE "
                + " AND ".join(where)
                + f" ORDER BY timestamp DESC LIMIT ${idx}"
            )
            params.append(limit)

            rows = await conn.fetch(sql, *params)
            events = [_format_event(dict(r)) for r in rows]
            return JSONResponse(
                content={
                    "count": len(events),
                    "events": events,
                    "server_time": datetime.now(timezone.utc).isoformat(),
                }
            )
    except Exception as e:
        logger.error("[pipeline_events] list failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to load pipeline events") from e


@router.get("/api/pipeline/events/task/{task_id}")
async def task_pipeline_events(task_id: str) -> JSONResponse:
    """Every pipeline event for a single task, oldest first — the
    full decision trail for one post from creation to approval or
    rejection. Useful for debugging why a specific task landed where
    it did.
    """
    try:
        pool = await _get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT id, timestamp, event_type, source, task_id, details, severity
                FROM audit_log
                WHERE task_id = $1 AND event_type = ANY($2)
                ORDER BY timestamp ASC
                """,
                task_id,
                list(_PIPELINE_EVENT_TYPES),
            )
            events = [_format_event(dict(r)) for r in rows]
            return JSONResponse(
                content={
                    "task_id": task_id,
                    "count": len(events),
                    "events": events,
                }
            )
    except Exception as e:
        logger.error("[pipeline_events] task fetch failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to load task events") from e


@router.get("/pipeline", response_class=HTMLResponse)
async def pipeline_dashboard(request: Request) -> HTMLResponse:
    """Minimal mobile-friendly HTML dashboard that polls the JSON
    endpoint every 5 seconds. No framework, no build step — just
    vanilla JS.

    Color coding:
        green   — approved / passed
        yellow  — warning / borderline
        red     — rejected / failed
        blue    — rewrite triggered
        grey    — info
    """
    return HTMLResponse(
        content="""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow">
<title>Poindexter Pipeline</title>
<style>
  :root {
    --bg: #0f172a;
    --panel: #1e293b;
    --border: #334155;
    --text: #e2e8f0;
    --muted: #94a3b8;
    --green: #22c55e;
    --red: #ef4444;
    --yellow: #f59e0b;
    --blue: #3b82f6;
    --grey: #64748b;
  }
  * { box-sizing: border-box; }
  body { margin: 0; padding: 0; font-family: -apple-system, system-ui, sans-serif;
         background: var(--bg); color: var(--text); font-size: 14px; }
  header { position: sticky; top: 0; background: var(--panel);
           border-bottom: 1px solid var(--border); padding: 12px 16px;
           display: flex; justify-content: space-between; align-items: center;
           z-index: 10; }
  header h1 { margin: 0; font-size: 16px; color: #fff; }
  header .stats { font-size: 12px; color: var(--muted); }
  .controls { padding: 12px 16px; background: var(--panel);
              border-bottom: 1px solid var(--border); display: flex;
              gap: 8px; flex-wrap: wrap; }
  .controls button { background: var(--bg); color: var(--text);
                     border: 1px solid var(--border); padding: 6px 12px;
                     border-radius: 6px; font-size: 12px; cursor: pointer; }
  .controls button.active { background: var(--blue); border-color: var(--blue); color: #fff; }
  main { padding: 8px 12px 80px; }
  .event { background: var(--panel); border: 1px solid var(--border);
           border-left: 3px solid var(--grey); border-radius: 8px;
           padding: 10px 12px; margin: 8px 0; }
  .event.severity-info { border-left-color: var(--grey); }
  .event.severity-warning { border-left-color: var(--yellow); }
  .event.severity-error { border-left-color: var(--red); }
  .event.passed { border-left-color: var(--green); }
  .event.failed { border-left-color: var(--red); }
  .event.rewrite { border-left-color: var(--blue); }
  .event .head { display: flex; justify-content: space-between; gap: 8px;
                 margin-bottom: 4px; align-items: baseline; }
  .event .type { font-weight: 600; font-size: 13px; color: #fff; }
  .event .time { font-size: 11px; color: var(--muted); white-space: nowrap; }
  .event .meta { font-size: 11px; color: var(--muted); margin-bottom: 6px; }
  .event .body { font-size: 12px; line-height: 1.5; color: var(--text);
                 white-space: pre-wrap; word-break: break-word; }
  .event .kv { display: inline-block; background: var(--bg); padding: 2px 6px;
               border-radius: 4px; margin: 2px 4px 2px 0; font-family: monospace;
               font-size: 11px; }
  .kv.good { background: #0f2a1f; color: var(--green); }
  .kv.bad { background: #2a0f0f; color: var(--red); }
  .empty { text-align: center; padding: 40px 16px; color: var(--muted); }
  footer { position: fixed; bottom: 0; left: 0; right: 0; background: var(--panel);
           border-top: 1px solid var(--border); padding: 8px 16px;
           font-size: 11px; color: var(--muted); display: flex;
           justify-content: space-between; }
</style>
</head><body>
<header>
  <h1>Poindexter Pipeline</h1>
  <div class="stats" id="stats">-</div>
</header>
<div class="controls">
  <button data-type="" class="active">all</button>
  <button data-type="qa_decision">qa decisions</button>
  <button data-type="qa_aggregate">aggregate</button>
  <button data-type="rewrite_decision">rewrites</button>
  <button data-type="qa_failed">failures</button>
</div>
<main id="events"><div class="empty">Loading...</div></main>
<footer>
  <span id="lastUpdate">Loading...</span>
  <span id="autoStatus">Auto-refresh: on</span>
</footer>
<script>
const state = { filter: "", events: [], last: null };
const evContainer = document.getElementById("events");
const stats = document.getElementById("stats");
const lastUpdate = document.getElementById("lastUpdate");

function severityClass(ev) {
  const t = ev.event_type || "";
  const d = ev.details || {};
  if (t === "qa_decision") {
    return d.approved === false ? "event failed" : "event passed";
  }
  if (t === "qa_aggregate") {
    return d.approved === false ? "event failed" : "event passed";
  }
  if (t.includes("rewrite")) return "event rewrite";
  if (t === "qa_failed") return "event failed";
  if (t === "qa_passed") return "event passed";
  return "event severity-" + (ev.severity || "info");
}

function renderEvent(ev) {
  const d = ev.details || {};
  const time = ev.timestamp ? new Date(ev.timestamp).toLocaleTimeString() : "?";
  const task = ev.task_id ? ev.task_id.substring(0, 8) : "-";
  const div = document.createElement("div");
  div.className = severityClass(ev);

  let headline = ev.event_type || "event";
  let body = "";

  if (ev.event_type === "qa_decision") {
    headline = `${d.reviewer || "?"} — ${d.approved ? "PASS" : "FAIL"}`;
    body = `score: ${d.score ?? "?"}\\nprovider: ${d.provider || "?"}\\n${d.feedback || ""}`;
  } else if (ev.event_type === "qa_aggregate") {
    headline = `multi-model QA — ${d.approved ? "APPROVED" : "REJECTED"} (${d.final_score ?? "?"}/100)`;
    const failed = (d.failed_reviewers || []).join(", ") || "none";
    body = `rewrites: ${d.rewrite_attempts ?? 0} / failed: ${failed}`;
  } else if (ev.event_type === "rewrite_decision" || ev.event_type === "qa_rewrite_triggered") {
    headline = `rewrite attempt ${d.attempt}/${d.max_attempts || "?"}`;
    body = `issues: ${d.issue_count ?? "?"} / prior score: ${d.prior_score ?? "?"}\\n${d.issues_sample || ""}`;
  } else {
    body = JSON.stringify(d, null, 2);
  }

  div.innerHTML = `
    <div class="head">
      <div class="type">${headline}</div>
      <div class="time">${time}</div>
    </div>
    <div class="meta">task ${task}</div>
    <div class="body">${body.replace(/\\n/g, "<br>")}</div>
  `;
  return div;
}

async function fetchEvents() {
  try {
    const url = "/api/pipeline/events?limit=50&since_minutes=120" +
      (state.filter ? "&event_type=" + encodeURIComponent(state.filter) : "");
    const resp = await fetch(url);
    if (!resp.ok) throw new Error("HTTP " + resp.status);
    const data = await resp.json();
    state.events = data.events || [];
    state.last = data.server_time;
    render();
  } catch (e) {
    stats.textContent = "error";
    console.error(e);
  }
}

function render() {
  if (!state.events.length) {
    evContainer.innerHTML = '<div class="empty">No events in the window.</div>';
  } else {
    evContainer.innerHTML = "";
    for (const ev of state.events) {
      evContainer.appendChild(renderEvent(ev));
    }
  }
  stats.textContent = state.events.length + " events";
  lastUpdate.textContent = "Last update: " + new Date().toLocaleTimeString();
}

document.querySelectorAll(".controls button").forEach(btn => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".controls button").forEach(b => b.classList.remove("active"));
    btn.classList.add("active");
    state.filter = btn.dataset.type || "";
    fetchEvents();
  });
});

fetchEvents();
setInterval(fetchEvents, 5000);
</script>
</body></html>""",
        media_type="text/html; charset=utf-8",
    )
