"""
Poindexter MCP Server — exposes all system capabilities as MCP tools.

Built by Glad Labs LLC. Connects Claude desktop app directly to the Poindexter platform:
- Content pipeline (create, approve, publish, list tasks)
- Site monitoring (health checks, post counts)
- Cost management (budget status)
- Settings management (read/write app_settings)
- System status (worker, OpenClaw, GPU)
- Semantic memory (pgvector search across all embeddings)
- Audit log (pipeline event history)

Usage:
    python mcp-server/server.py
    # Or via uvx in Claude desktop config
"""

import json
import logging
import os
import subprocess
import urllib.parse
import urllib.request
from typing import Any

import asyncpg
import httpx

from mcp.server.fastmcp import FastMCP

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("poindexter-mcp")

# Customer-facing env var names — POINDEXTER_*. Old GLADLABS_* names still
# accepted as fallback for users upgrading from the pre-rebrand release.
API_URL = os.getenv("POINDEXTER_API_URL") or os.getenv("GLADLABS_API_URL", "http://localhost:8002")
API_TOKEN = os.getenv("POINDEXTER_API_TOKEN") or os.getenv("GLADLABS_API_TOKEN", "dev-token")
LOCAL_DB_DSN = os.getenv(
    "LOCAL_DATABASE_URL",
    "postgresql://poindexter:poindexter-brain-local@localhost:5433/poindexter_brain",
)
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
EMBED_MODEL = os.getenv("EMBED_MODEL", "nomic-embed-text")

# Lazy-initialized connection pool and HTTP client
_pool: asyncpg.Pool | None = None
_http: httpx.AsyncClient | None = None


async def _get_pool() -> asyncpg.Pool:
    """Get or create the local pgvector connection pool."""
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(LOCAL_DB_DSN, min_size=1, max_size=3)
    return _pool


async def _get_http() -> httpx.AsyncClient:
    """Get or create the async HTTP client."""
    global _http
    if _http is None:
        _http = httpx.AsyncClient(timeout=30.0)
    return _http


async def _embed_text(text: str) -> list[float]:
    """Embed text via Ollama nomic-embed-text. Returns 768-dim vector."""
    client = await _get_http()
    resp = await client.post(
        f"{OLLAMA_URL}/api/embed",
        json={"model": EMBED_MODEL, "input": text},
    )
    resp.raise_for_status()
    embeddings = resp.json().get("embeddings", [])
    if not embeddings:
        raise RuntimeError("Ollama returned no embeddings")
    return embeddings[0]


mcp = FastMCP("Poindexter", instructions="""
Poindexter MCP server — your direct interface to the AI content pipeline.
Built by Glad Labs LLC. Use these tools to manage content, monitor the
system, and control operations.

SEMANTIC MEMORY: Use search_memory to recall prior decisions, context, and knowledge.
Search before asking the user — the answer may already be in memory.
""")


def _api(method: str, path: str, data: dict | None = None) -> dict:
    """Call the Poindexter API."""
    url = f"{API_URL}{path}"
    headers = {"Authorization": f"Bearer {API_TOKEN}", "Content-Type": "application/json"}
    body = json.dumps(data).encode() if data else None
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        resp = urllib.request.urlopen(req, timeout=15)
        return json.loads(resp.read())
    except Exception as e:
        return {"error": str(e)[:200]}


# ============================================================================
# CONTENT PIPELINE TOOLS
# ============================================================================

@mcp.tool()
def create_post(topic: str, category: str = "technology", target_audience: str = "developers and founders") -> str:
    """Create a new blog post task in the content pipeline. The worker will generate it via AI."""
    result = _api("POST", "/api/tasks", {
        "task_name": f"Blog post: {topic}",
        "topic": topic,
        "category": category,
        "target_audience": target_audience,
    })
    if "error" in result:
        return f"Failed: {result['error']}"
    return f"Task created: {result.get('task_id', '?')} — status: {result.get('status', '?')}"


@mcp.tool()
async def list_tasks(status: str = "all", limit: int = 10) -> str:
    """List content tasks with their status and quality scores."""
    try:
        pool = await _get_pool()
        limit = min(limit, 100)
        if status != "all":
            rows = await pool.fetch(
                "SELECT task_id, topic, status, quality_score, created_at "
                "FROM content_tasks WHERE status = $1 ORDER BY created_at DESC LIMIT $2",
                status, limit,
            )
        else:
            rows = await pool.fetch(
                "SELECT task_id, topic, status, quality_score, created_at "
                "FROM content_tasks ORDER BY created_at DESC LIMIT $1",
                limit,
            )
        if not rows:
            return "No tasks found."
        lines = [f"Tasks ({len(rows)}):"]
        for r in rows:
            tid = str(r["task_id"])
            tid_short = tid[:8] if len(tid) > 8 else tid
            qs = str(r["quality_score"]) if r["quality_score"] is not None else "-"
            topic = (r["topic"] or "?")[:50]
            lines.append(f"  {tid_short} | {r['status'] or '?':20s} | Q:{qs:5s} | {topic}")
        return "\n".join(lines)
    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
def approve_post(task_id: str) -> str:
    """Approve a content task for publishing."""
    result = _api("POST", f"/api/tasks/{task_id}/approve")
    return f"Status: {result.get('status', result.get('error', '?'))}"


@mcp.tool()
def reject_post(task_id: str, reason: str = "Rejected by reviewer") -> str:
    """Reject a content task. Provide a reason for feedback to the pipeline."""
    result = _api("POST", f"/api/tasks/{task_id}/reject", data={
        "reason": reason,
        "feedback": reason,
        "allow_revisions": False,
    })
    status = result.get("status", result.get("error", "?"))
    return f"Rejected: {status} — {reason}"


@mcp.tool()
def publish_post(task_id: str) -> str:
    """Publish an approved content task to gladlabs.io."""
    result = _api("POST", f"/api/tasks/{task_id}/publish")
    return f"Status: {result.get('status', result.get('error', '?'))}"


@mcp.tool()
async def get_post_count() -> str:
    """Get the total number of published posts on gladlabs.io."""
    try:
        pool = await _get_pool()
        count = await pool.fetchval("SELECT COUNT(*) FROM posts WHERE status = 'published'")
        return f"Published posts: {count}"
    except Exception as e:
        return f"Error: {e}"


# ============================================================================
# MONITORING TOOLS
# ============================================================================

@mcp.tool()
def check_health() -> str:
    """Check the health of all Poindexter systems (site, API, worker, OpenClaw)."""
    checks = []

    # Site
    try:
        req = urllib.request.Request("https://gladlabs.io")
        resp = urllib.request.urlopen(req, timeout=10)
        checks.append(f"Site: {resp.status} OK")
    except Exception as e:
        checks.append(f"Site: DOWN ({e})")

    # API
    result = _api("GET", "/api/health")
    checks.append(f"API: {result.get('status', result.get('error', '?'))}")

    # Posts
    result = _api("GET", "/api/posts?limit=1")
    checks.append(f"Posts: {result.get('total', '?')}")

    # Worker
    try:
        resp = urllib.request.urlopen("http://localhost:8002/api/health", timeout=5)
        data = json.loads(resp.read())
        te = data.get("components", {}).get("task_executor", {})
        checks.append(f"Worker: running={te.get('running')}, processed={te.get('total_processed')}")
    except Exception:
        checks.append("Worker: offline")

    # OpenClaw
    try:
        urllib.request.urlopen("http://localhost:18789/status", timeout=5)
        checks.append("OpenClaw: running")
    except Exception:
        checks.append("OpenClaw: offline")

    # GPU
    try:
        r = subprocess.run(
            ["nvidia-smi", "--query-gpu=utilization.gpu,temperature.gpu,memory.used,memory.total",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5
        )
        vals = r.stdout.strip().split(", ")
        if len(vals) >= 4:
            checks.append(f"GPU: {vals[0]}% util, {vals[1]}C, {vals[2]}/{vals[3]} MiB VRAM")
    except Exception:
        checks.append("GPU: unavailable")

    return "\n".join(checks)


@mcp.tool()
async def get_budget() -> str:
    """Get current AI spending status (daily and monthly)."""
    try:
        pool = await _get_pool()
        monthly = await pool.fetchval(
            "SELECT COALESCE(SUM(cost_usd), 0) FROM cost_logs "
            "WHERE created_at >= date_trunc('month', NOW())"
        )
        daily = await pool.fetchval(
            "SELECT COALESCE(SUM(cost_usd), 0) FROM cost_logs "
            "WHERE created_at >= date_trunc('day', NOW())"
        )
        return json.dumps({
            "monthly_total_usd": float(monthly),
            "daily_total_usd": float(daily),
        }, indent=2)
    except Exception as e:
        return f"Error: {e}"


# ============================================================================
# SETTINGS TOOLS
# ============================================================================

@mcp.tool()
async def get_setting(key: str) -> str:
    """Get a configuration setting value from the database."""
    try:
        pool = await _get_pool()
        row = await pool.fetchrow(
            "SELECT key, value, category, is_secret FROM app_settings WHERE key = $1", key
        )
        if not row:
            return f"Setting '{key}' not found."
        val = "********" if row["is_secret"] else row["value"]
        return f"{row['key']} = {val} (category: {row['category'] or '?'})"
    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
async def set_setting(key: str, value: str) -> str:
    """Update a configuration setting in the database.

    Respects agent_permissions table — checks if mcp_server is allowed to write app_settings.
    """
    # Permission check
    try:
        pool = await _get_pool()
        perm = await pool.fetchrow(
            "SELECT allowed, requires_approval FROM agent_permissions "
            "WHERE agent_name = 'mcp_server' AND resource = 'app_settings' AND action = 'write'",
        )
        if perm and not perm["allowed"]:
            if perm["requires_approval"]:
                await pool.execute(
                    "INSERT INTO approval_queue (agent_name, resource, action, proposed_change, reason) "
                    "VALUES ('mcp_server', 'app_settings', 'write', $1, 'MCP set_setting tool')",
                    json.dumps({"key": key, "value": value}),
                )
                return f"Permission denied: change to {key} queued for approval"
            return f"Permission denied: mcp_server cannot write to app_settings"
    except Exception:
        pass  # Permission check failed — fall through to direct DB write

    try:
        pool = await _get_pool()
        await pool.execute(
            "INSERT INTO app_settings (key, value) VALUES ($1, $2) "
            "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = NOW()",
            key, value,
        )
        return f"Updated: {key} = {value}"
    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
async def list_settings(category: str = "") -> str:
    """List all configuration settings, optionally filtered by category."""
    try:
        pool = await _get_pool()
        if category:
            rows = await pool.fetch(
                "SELECT key, value, category, is_secret FROM app_settings "
                "WHERE category = $1 ORDER BY key",
                category,
            )
        else:
            rows = await pool.fetch(
                "SELECT key, value, category, is_secret FROM app_settings ORDER BY key"
            )
        if not rows:
            return "No settings found."
        lines = [f"Settings ({len(rows)}):"]
        for r in rows:
            val = "********" if r["is_secret"] else (r["value"] or "")
            lines.append(f"  {r['category'] or '?'}/{r['key']} = {val}")
        return "\n".join(lines)
    except Exception as e:
        return f"Error: {e}"


# ============================================================================
# COMPOSE TOOLS
# ============================================================================

@mcp.tool()
def compose_plan(intent: str) -> str:
    """Create a process execution plan from natural language intent. Returns a plan for review before execution."""
    result = _api("POST", "/api/compose/plan", {"intent": intent})
    if "error" in result:
        return f"Error: {result['error']}"
    return result.get("summary", json.dumps(result, indent=2))


@mcp.tool()
def compose_execute(intent: str) -> str:
    """Execute a business process immediately from natural language intent."""
    result = _api("POST", "/api/compose/execute", {"intent": intent})
    if "error" in result:
        return f"Error: {result['error']}"
    return result.get("summary", json.dumps(result, indent=2))


# ============================================================================
# SEMANTIC MEMORY TOOLS (pgvector)
# ============================================================================

@mcp.tool()
async def search_memory(query: str, top_k: int = 8, source_filter: str = "") -> str:
    """Search semantic memory (pgvector) using natural language.

    Searches across ALL embedded content: memory files, blog posts, Gitea issues, audit logs.
    Use this to recall prior decisions, find related content, or check what the system knows.

    Args:
        query: Natural language search query (e.g. "cost tracking decisions", "ollama configuration")
        top_k: Number of results to return (default 8)
        source_filter: Optional filter by source_table (e.g. "memory", "post", "issue", "audit")
    """
    try:
        embedding = await _embed_text(query)
        pool = await _get_pool()

        vector_str = "[" + ",".join(str(v) for v in embedding) + "]"

        if source_filter:
            rows = await pool.fetch(
                """
                SELECT source_table, source_id, text_preview, metadata,
                       1 - (embedding <=> $1::vector) as similarity
                FROM embeddings
                WHERE source_table = $2
                ORDER BY embedding <=> $1::vector
                LIMIT $3
                """,
                vector_str, source_filter, top_k,
            )
        else:
            rows = await pool.fetch(
                """
                SELECT source_table, source_id, text_preview, metadata,
                       1 - (embedding <=> $1::vector) as similarity
                FROM embeddings
                ORDER BY embedding <=> $1::vector
                LIMIT $2
                """,
                vector_str, top_k,
            )

        if not rows:
            return f'No results for "{query}". The embedding database may be empty or Ollama may have generated an incompatible vector.'

        lines = [f'Memory search: "{query}" ({len(rows)} results)\n']
        for i, row in enumerate(rows, 1):
            sim = float(row["similarity"])
            meta = json.loads(row["metadata"]) if row["metadata"] else {}
            source = row["source_table"]
            sid = row["source_id"]
            preview = (row["text_preview"] or "")[:200].replace("\n", " ")
            origin = meta.get("origin", source)
            mtype = meta.get("type", meta.get("state", ""))

            lines.append(
                f"{i}. [{sim:.4f}] [{source}] {sid}"
                + (f" (origin={origin}, type={mtype})" if mtype else f" (origin={origin})")
                + f"\n   {preview}"
            )
        return "\n".join(lines)

    except Exception as e:
        return f"Memory search failed: {e}"


@mcp.tool()
async def recall_decision(topic: str) -> str:
    """Search memory specifically for past decisions, feedback, and project context.

    Filters to memory-type embeddings only (Claude Code memory, OpenClaw memory, shared context).
    Best for: "why did we choose X", "what was decided about Y", "user preferences for Z".
    """
    return await search_memory(topic, top_k=5, source_filter="memory")


@mcp.tool()
async def find_similar_posts(topic: str, top_k: int = 5) -> str:
    """Find published blog posts similar to a topic. Use before creating new content to avoid duplicates."""
    return await search_memory(topic, top_k=top_k, source_filter="post")


@mcp.tool()
async def memory_stats() -> str:
    """Get statistics about the semantic memory database — embedding counts by source type."""
    try:
        pool = await _get_pool()
        rows = await pool.fetch("""
            SELECT source_table, COUNT(*) as count,
                   MIN(created_at) as oldest,
                   MAX(updated_at) as newest
            FROM embeddings
            GROUP BY source_table
            ORDER BY count DESC
        """)
        if not rows:
            return "No embeddings in database."

        total = sum(row["count"] for row in rows)
        lines = [f"Semantic Memory: {total} embeddings\n"]
        for row in rows:
            lines.append(
                f"  {row['source_table']:15s} {row['count']:5d} vectors"
                f"  (oldest: {row['oldest'].strftime('%Y-%m-%d') if row['oldest'] else '?'}"
                f", newest: {row['newest'].strftime('%Y-%m-%d') if row['newest'] else '?'})"
            )
        return "\n".join(lines)
    except Exception as e:
        return f"Failed to get memory stats: {e}"


# ============================================================================
# AUDIT LOG TOOLS
# ============================================================================

@mcp.tool()
async def get_audit_log(event_type: str = "", severity: str = "", limit: int = 20) -> str:
    """Query the pipeline audit log for recent events.

    Args:
        event_type: Filter by event type (e.g. "pipeline_start", "qa_review", "publish", "error")
        severity: Filter by severity (e.g. "info", "warning", "error", "critical")
        limit: Number of events to return (default 20, max 100)
    """
    try:
        pool = await _get_pool()
        limit = min(limit, 100)

        conditions = []
        params: list[Any] = []
        idx = 1

        if event_type:
            conditions.append(f"event_type = ${idx}")
            params.append(event_type)
            idx += 1
        if severity:
            conditions.append(f"severity = ${idx}")
            params.append(severity)
            idx += 1

        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        params.append(limit)

        rows = await pool.fetch(
            f"""
            SELECT timestamp, event_type, source, task_id, severity, details
            FROM audit_log
            {where}
            ORDER BY timestamp DESC
            LIMIT ${idx}
            """,
            *params,
        )

        if not rows:
            return "No audit log entries found."

        lines = [f"Audit log ({len(rows)} entries):\n"]
        for row in rows:
            ts = row["timestamp"].strftime("%m-%d %H:%M") if row["timestamp"] else "?"
            details = json.loads(row["details"]) if isinstance(row["details"], str) else (row["details"] or {})
            detail_str = json.dumps(details)[:120] if details else ""
            lines.append(
                f"  [{ts}] {row['severity']:8s} {row['event_type']:25s} "
                f"src={row['source'] or '?'} task={row['task_id'] or '-'}"
                + (f"\n           {detail_str}" if detail_str else "")
            )
        return "\n".join(lines)
    except Exception as e:
        return f"Audit log query failed: {e}"


@mcp.tool()
async def get_audit_summary(hours: int = 24) -> str:
    """Get a summary of audit log activity over the last N hours."""
    try:
        pool = await _get_pool()
        rows = await pool.fetch("""
            SELECT event_type, severity, COUNT(*) as count
            FROM audit_log
            WHERE timestamp > NOW() - $1 * INTERVAL '1 hour'
            GROUP BY event_type, severity
            ORDER BY count DESC
        """, hours)

        if not rows:
            return f"No audit activity in the last {hours} hours."

        total = sum(row["count"] for row in rows)
        lines = [f"Audit summary (last {hours}h): {total} events\n"]
        for row in rows:
            lines.append(f"  {row['event_type']:30s} {row['severity']:8s} x{row['count']}")
        return "\n".join(lines)
    except Exception as e:
        return f"Audit summary failed: {e}"


# ============================================================================
# STATIC EXPORT TOOLS
# ============================================================================

@mcp.tool()
def rebuild_static_export() -> str:
    """Rebuild all static JSON files on CDN (posts, feed, sitemap, categories, authors).

    Triggers a full export of the headless CMS data to R2/S3 storage.
    Any frontend can consume these files without needing the API.
    """
    result = _api("POST", "/api/export/rebuild")
    if "error" in result:
        return f"Export failed: {result['error']}"
    posts = result.get("posts_exported", 0)
    errors = result.get("errors", [])
    status = "with errors" if errors else "successfully"
    return (
        f"Static export completed {status}.\n"
        f"  Posts: {posts}\n"
        f"  Categories: {result.get('categories_exported', 0)}\n"
        f"  Authors: {result.get('authors_exported', 0)}\n"
        f"  Total files: {result.get('total_files', 0)}\n"
        + (f"  Errors: {', '.join(errors)}\n" if errors else "")
    )


# ============================================================================
# BRAIN KNOWLEDGE TOOLS
# ============================================================================

@mcp.tool()
async def get_brain_knowledge(entity: str = "", attribute: str = "", limit: int = 20) -> str:
    """Query the brain's knowledge base (health probes, system state, learned facts).

    Args:
        entity: Filter by entity (e.g. "probe.db_ping", "probe.ollama_models")
        attribute: Filter by attribute (e.g. "health_status")
        limit: Max results
    """
    try:
        pool = await _get_pool()
        conditions = []
        params: list[Any] = []
        idx = 1

        if entity:
            conditions.append(f"entity LIKE ${idx}")
            params.append(f"%{entity}%")
            idx += 1
        if attribute:
            conditions.append(f"attribute = ${idx}")
            params.append(attribute)
            idx += 1

        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        params.append(min(limit, 100))

        rows = await pool.fetch(
            f"""
            SELECT entity, attribute, value, confidence, source, tags, updated_at
            FROM brain_knowledge
            {where}
            ORDER BY updated_at DESC
            LIMIT ${idx}
            """,
            *params,
        )

        if not rows:
            return "No brain knowledge entries found."

        lines = [f"Brain knowledge ({len(rows)} entries):\n"]
        for row in rows:
            ts = row["updated_at"].strftime("%m-%d %H:%M") if row["updated_at"] else "?"
            val = str(row["value"])[:150] if row["value"] else ""
            lines.append(
                f"  [{ts}] {row['entity']}::{row['attribute']} "
                f"(confidence={row['confidence']:.1f})\n"
                f"    {val}"
            )
        return "\n".join(lines)
    except Exception as e:
        return f"Brain knowledge query failed: {e}"


if __name__ == "__main__":
    mcp.run(transport="stdio")
