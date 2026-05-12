"""
Shared-memory observability endpoints — the /memory dashboard.

Surfaces the state of the pgvector `embeddings` table grouped by source_table
AND by the new `writer` column (added in migration 024). Answers the
question "is my memory file actually embedded?" without hand-running SQL.

Three endpoints, same pattern as /pipeline:

    GET /api/memory/stats    JSON stats for dashboards / Grafana / Discord
    GET /api/memory/search   JSON semantic search (thin wrapper over MemoryClient.search)
    GET /memory              minimal HTML dashboard, polls /api/memory/stats every 10s

Staleness thresholds per writer are resolved from app_settings keys of the
shape `memory_stale_threshold_seconds_<writer>` (with a global fallback
`memory_stale_threshold_seconds`, default 21600 = 6 hours). Anything past
the threshold shows up with a red border and feeds a future
`memory_sync_stale` audit event (slice 5 of Gitea #192).

This is the public-facing dashboard that will ship in the public
`Glad-Labs/poindexter` repo per the public-naming rule — no gladlabs-brand
strings in the HTML.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse, JSONResponse

from middleware.api_token_auth import verify_api_token

logger = logging.getLogger(__name__)

router = APIRouter(tags=["memory-dashboard"])


async def _get_memory_client():
    """Lazy import so the dashboard route doesn't fail to register if the
    poindexter package isn't available at route-registration time.

    Returns a connected `MemoryClient` ready to use. Caller is responsible
    for calling `.close()` when done (or use `async with`).
    """
    from poindexter.memory import MemoryClient

    # Route-local clients reuse the same DSN resolution as the library —
    # POINDEXTER_MEMORY_DSN, DATABASE_URL, or defaults.
    mem = MemoryClient()
    await mem.connect()
    return mem


def _iso(ts: datetime | None) -> str | None:
    if ts is None:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts.isoformat()


def _seconds_since(ts: datetime | None) -> int | None:
    if ts is None:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return int((datetime.now(timezone.utc) - ts).total_seconds())


async def _resolve_staleness_threshold(writer: str) -> int:
    """Look up the staleness threshold for a writer from app_settings.

    Falls back to `memory_stale_threshold_seconds` and then to 6h default.
    Returns an integer number of seconds.
    """
    # Lazy import to avoid coupling this module to the rest of the worker
    # startup path.
    try:
        import asyncpg

        dsn = (
            os.getenv("POINDEXTER_MEMORY_DSN")
            or os.getenv("LOCAL_DATABASE_URL")
            or os.getenv("DATABASE_URL")
            or ""
        )
        if not dsn:
            return 6 * 3600
        conn = await asyncpg.connect(dsn)
        try:
            row = await conn.fetchrow(
                "SELECT value FROM app_settings WHERE key = $1",
                f"memory_stale_threshold_seconds_{writer}",
            )
            if row is None:
                row = await conn.fetchrow(
                    "SELECT value FROM app_settings WHERE key = $1",
                    "memory_stale_threshold_seconds",
                )
            if row and row["value"]:
                try:
                    return int(row["value"])
                except (ValueError, TypeError):
                    pass
        finally:
            await conn.close()
    except Exception as exc:
        logger.debug("Staleness threshold lookup failed (%s) — using 6h default", exc)
    return 6 * 3600


@router.get("/api/memory/stats")
async def memory_stats(
    _principal: str = Depends(verify_api_token),
) -> JSONResponse:
    """Aggregated counts + timestamps across the shared memory store.

    Returns::

        {
          "total": 1408,
          "embed_model": "nomic-embed-text",
          "embed_dim": 768,
          "by_source_table": [
            {"key": "memory", "count": 172, "oldest": ..., "newest": ..., "age_seconds": 12},
            ...
          ],
          "by_writer": [
            {"key": "claude-code", "count": 70, "oldest": ..., "newest": ..., "age_seconds": 12, "stale_threshold": 21600, "stale": false},
            ...
          ]
        }
    """
    mem = None
    try:
        mem = await _get_memory_client()
        stats = await mem.stats()
    except Exception as exc:
        logger.error("memory stats failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Stats failed: {exc}") from exc
    finally:
        if mem is not None:
            await mem.close()

    total = sum(row["count"] for row in stats["by_source_table"].values())

    by_source_table = [
        {
            "key": key,
            "count": data["count"],
            "oldest": _iso(data["oldest"]),
            "newest": _iso(data["newest"]),
            "age_seconds": _seconds_since(data["newest"]),
        }
        for key, data in stats["by_source_table"].items()
    ]
    by_source_table.sort(key=lambda r: r["count"], reverse=True)

    by_writer_rows: list[dict[str, Any]] = []
    for key, data in stats["by_writer"].items():
        threshold = await _resolve_staleness_threshold(key)
        age = _seconds_since(data["newest"])
        by_writer_rows.append(
            {
                "key": key,
                "count": data["count"],
                "oldest": _iso(data["oldest"]),
                "newest": _iso(data["newest"]),
                "age_seconds": age,
                "stale_threshold": threshold,
                "stale": bool(age is not None and age > threshold),
            }
        )
    by_writer_rows.sort(key=lambda r: r["count"], reverse=True)

    return JSONResponse(
        {
            "total": total,
            "embed_model": "nomic-embed-text",
            "embed_dim": 768,
            "by_source_table": by_source_table,
            "by_writer": by_writer_rows,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }
    )


@router.get("/api/memory/search")
async def memory_search_endpoint(
    q: str = Query(..., min_length=1, description="Natural language query"),
    writer: str = Query("", description="Optional writer filter"),
    source_table: str = Query("", description="Optional source_table filter"),
    min_similarity: float = Query(0.3, ge=0.0, le=1.0),
    limit: int = Query(10, ge=1, le=50),
    _principal: str = Depends(verify_api_token),
) -> JSONResponse:
    """Thin HTTP wrapper over `MemoryClient.search` for debugging +
    dashboard use. Not intended as a high-throughput public search API —
    it opens a fresh MemoryClient per request.
    """
    mem = None
    try:
        mem = await _get_memory_client()
        hits = await mem.search(
            q,
            writer=writer or None,
            source_table=source_table or None,
            min_similarity=min_similarity,
            limit=limit,
        )
    except Exception as exc:
        logger.error("memory search failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Search failed: {exc}") from exc
    finally:
        if mem is not None:
            await mem.close()

    return JSONResponse(
        {
            "query": q,
            "filters": {
                "writer": writer or None,
                "source_table": source_table or None,
                "min_similarity": min_similarity,
                "limit": limit,
            },
            "count": len(hits),
            "hits": [
                {
                    "source_table": h.source_table,
                    "source_id": h.source_id,
                    "similarity": round(h.similarity, 4),
                    "writer": h.writer,
                    "text_preview": h.text_preview,
                    "metadata": h.metadata,
                }
                for h in hits
            ],
        }
    )


@router.get("/memory", response_class=HTMLResponse)
async def memory_dashboard(
    _principal: str = Depends(verify_api_token),
) -> HTMLResponse:
    """Minimal HTML dashboard that polls /api/memory/stats every 10s.

    Two tables: by source_table (memory/posts/issues/audit) and by writer
    (claude-code/openclaw/worker/gitea/user). Stale writers show a red
    border. No framework, no build step, mobile-friendly.
    """
    return HTMLResponse(
        content=r"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow">
<title>Poindexter Memory</title>
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
  }
  * { box-sizing: border-box; }
  body { margin: 0; padding: 0; font-family: -apple-system, system-ui, sans-serif;
         background: var(--bg); color: var(--text); font-size: 14px; }
  header { position: sticky; top: 0; background: var(--panel);
           border-bottom: 1px solid var(--border); padding: 12px 16px;
           display: flex; justify-content: space-between; align-items: center;
           z-index: 10; }
  header h1 { margin: 0; font-size: 16px; color: #fff; }
  header .total { font-size: 12px; color: var(--muted); }
  main { padding: 16px 12px 80px; max-width: 900px; margin: 0 auto; }
  section { background: var(--panel); border: 1px solid var(--border);
            border-radius: 8px; padding: 12px 16px; margin: 12px 0; }
  section h2 { margin: 0 0 8px; font-size: 13px; color: #fff;
               text-transform: uppercase; letter-spacing: 0.05em; }
  table { width: 100%; border-collapse: collapse; font-size: 12px; }
  th { text-align: left; color: var(--muted); font-weight: 500;
       padding: 6px 8px; border-bottom: 1px solid var(--border); }
  td { padding: 8px; border-bottom: 1px solid var(--border); vertical-align: top; }
  tbody tr:last-child td { border-bottom: none; }
  tr.stale { background: rgba(239, 68, 68, 0.1); }
  tr.stale td.key { color: var(--red); font-weight: 600; }
  td.count { font-family: monospace; text-align: right; color: #fff; }
  td.age { font-family: monospace; color: var(--muted); white-space: nowrap; }
  td.age.fresh { color: var(--green); }
  td.age.stale { color: var(--red); font-weight: 600; }
  td.age.warn { color: var(--yellow); }
  .empty { text-align: center; padding: 40px 16px; color: var(--muted); }
  footer { position: fixed; bottom: 0; left: 0; right: 0; background: var(--panel);
           border-top: 1px solid var(--border); padding: 8px 16px;
           font-size: 11px; color: var(--muted); display: flex;
           justify-content: space-between; }
  .search-box { display: flex; gap: 6px; margin-bottom: 8px; }
  .search-box input { flex: 1; background: var(--bg); color: var(--text);
                      border: 1px solid var(--border); padding: 6px 10px;
                      border-radius: 6px; font-size: 13px; }
  .search-box button { background: var(--blue); color: #fff; border: none;
                       padding: 6px 14px; border-radius: 6px; font-size: 12px;
                       cursor: pointer; }
  .hit { background: var(--bg); border: 1px solid var(--border);
         border-radius: 6px; padding: 8px 10px; margin: 6px 0; font-size: 12px; }
  .hit .head { display: flex; justify-content: space-between; gap: 8px; }
  .hit .sim { font-family: monospace; color: var(--green); }
  .hit .meta { color: var(--muted); font-size: 11px; }
  .hit .preview { margin-top: 4px; color: var(--text);
                  word-break: break-word; line-height: 1.4; }
</style>
</head><body>
<header>
  <h1>Poindexter Memory</h1>
  <div class="total" id="total">-</div>
</header>
<main>
  <section>
    <h2>Search</h2>
    <div class="search-box">
      <input id="q" type="text" placeholder='Natural language query (e.g. "why did we pick gemma3")' />
      <button id="searchBtn">Search</button>
    </div>
    <div id="hits"></div>
  </section>
  <section>
    <h2>By Source Table</h2>
    <table id="sourceTable"><thead><tr>
      <th>namespace</th><th class="count">count</th><th>oldest</th><th>newest</th><th>age</th>
    </tr></thead><tbody><tr><td colspan="5" class="empty">Loading...</td></tr></tbody></table>
  </section>
  <section>
    <h2>By Writer</h2>
    <table id="writerTable"><thead><tr>
      <th>writer</th><th class="count">count</th><th>oldest</th><th>newest</th><th>age</th>
    </tr></thead><tbody><tr><td colspan="5" class="empty">Loading...</td></tr></tbody></table>
  </section>
</main>
<footer>
  <span id="lastUpdate">Loading...</span>
  <span>Refresh 10s</span>
</footer>
<script>
function formatAge(seconds) {
  if (seconds === null || seconds === undefined) return "-";
  if (seconds < 60) return seconds + "s";
  if (seconds < 3600) return Math.floor(seconds / 60) + "m";
  if (seconds < 86400) return Math.floor(seconds / 3600) + "h";
  return Math.floor(seconds / 86400) + "d";
}

function formatTs(iso) {
  if (!iso) return "-";
  const d = new Date(iso);
  return d.toISOString().replace("T", " ").replace(/\.\d+Z$/, "Z");
}

function ageClass(row) {
  if (row.stale) return "age stale";
  const threshold = row.stale_threshold || 21600;
  const age = row.age_seconds;
  if (age === null || age === undefined) return "age";
  if (age < threshold / 4) return "age fresh";
  if (age < threshold / 2) return "age";
  return "age warn";
}

function renderTable(tbodyEl, rows, highlightStale) {
  if (!rows || rows.length === 0) {
    tbodyEl.innerHTML = '<tr><td colspan="5" class="empty">No data</td></tr>';
    return;
  }
  tbodyEl.innerHTML = rows.map(r => {
    const klass = (highlightStale && r.stale) ? ' class="stale"' : '';
    return `<tr${klass}>
      <td class="key">${r.key}</td>
      <td class="count">${r.count.toLocaleString()}</td>
      <td>${formatTs(r.oldest)}</td>
      <td>${formatTs(r.newest)}</td>
      <td class="${ageClass(r)}">${formatAge(r.age_seconds)}</td>
    </tr>`;
  }).join("");
}

async function loadStats() {
  try {
    const res = await fetch("/api/memory/stats");
    const data = await res.json();
    document.getElementById("total").textContent = data.total.toLocaleString() + " embeddings";
    renderTable(
      document.querySelector("#sourceTable tbody"),
      data.by_source_table,
      false,
    );
    renderTable(
      document.querySelector("#writerTable tbody"),
      data.by_writer,
      true,
    );
    document.getElementById("lastUpdate").textContent = "Updated " + new Date().toLocaleTimeString();
  } catch (e) {
    document.getElementById("lastUpdate").textContent = "Error: " + e.message;
  }
}

async function runSearch() {
  const q = document.getElementById("q").value.trim();
  if (!q) return;
  const hitsEl = document.getElementById("hits");
  hitsEl.innerHTML = '<div class="empty">Searching...</div>';
  try {
    const res = await fetch("/api/memory/search?q=" + encodeURIComponent(q) + "&limit=10&min_similarity=0.3");
    const data = await res.json();
    if (!data.hits || data.hits.length === 0) {
      hitsEl.innerHTML = '<div class="empty">No matches</div>';
      return;
    }
    hitsEl.innerHTML = data.hits.map(h => `<div class="hit">
      <div class="head">
        <span class="sim">${h.similarity.toFixed(3)}</span>
        <span class="meta">${h.source_table}/${h.writer || "?"} · ${h.source_id}</span>
      </div>
      <div class="preview">${(h.text_preview || "").substring(0, 240).replace(/</g, "&lt;")}</div>
    </div>`).join("");
  } catch (e) {
    hitsEl.innerHTML = '<div class="empty">Search failed: ' + e.message + '</div>';
  }
}

document.getElementById("searchBtn").addEventListener("click", runSearch);
document.getElementById("q").addEventListener("keyup", (e) => {
  if (e.key === "Enter") runSearch();
});

loadStats();
setInterval(loadStats, 10000);
</script>
</body></html>
"""
    )
