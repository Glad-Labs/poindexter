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

import hashlib
import json
import logging
import os
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any

import asyncpg
import httpx

from mcp.server.fastmcp import FastMCP

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("poindexter-mcp")

# Customer-facing env var names — POINDEXTER_*. Old GLADLABS_* names still
# accepted as fallback for users upgrading from the pre-rebrand release.
# #198: no hardcoded defaults. Every value must come from the environment;
# the MCP server fails loud on startup if something required is missing.


def _require_env(*names: str) -> str:
    """Return the first set env var in order. Notify + exit if none are set."""
    for n in names:
        v = os.getenv(n)
        if v:
            return v
    import sys as _sys
    from pathlib import Path as _Path

    _repo_root = _Path(__file__).resolve().parents[1]
    if str(_repo_root) not in _sys.path:
        _sys.path.insert(0, str(_repo_root))
    from brain.operator_notifier import notify_operator

    joined = ", ".join(names)
    notify_operator(
        title="MCP server cannot start — missing required env var",
        detail=(
            f"Set one of these env vars before launching the MCP server: "
            f"{joined}.\n\n"
            "For local dev the Claude desktop config should export "
            "POINDEXTER_API_URL, POINDEXTER_API_TOKEN, LOCAL_DATABASE_URL, "
            "and OLLAMA_URL. No hardcoded defaults — see issue #198."
        ),
        source="mcp_server",
        severity="critical",
    )
    _sys.exit(2)


API_URL = _require_env("POINDEXTER_API_URL", "GLADLABS_API_URL")
API_TOKEN = _require_env("POINDEXTER_API_TOKEN", "GLADLABS_API_TOKEN")

# Route the DB DSN through the bootstrap resolver so ~/.poindexter/bootstrap.toml
# works for the MCP server just like it does for the brain daemon (#198).
import sys as _sys_boot
from pathlib import Path as _Path_boot

_repo_root_boot = _Path_boot(__file__).resolve().parents[1]
if str(_repo_root_boot) not in _sys_boot.path:
    _sys_boot.path.insert(0, str(_repo_root_boot))
# Also expose src/cofounder_agent so we can import services.logger_config
# without altering the rest of the import shape (#259).
_cofounder_root = _repo_root_boot / "src" / "cofounder_agent"
if _cofounder_root.is_dir() and str(_cofounder_root) not in _sys_boot.path:
    _sys_boot.path.insert(0, str(_cofounder_root))
from brain.bootstrap import require_database_url

LOCAL_DB_DSN = require_database_url(source="mcp_server")

# --- Tool error formatting helper (#259) -----------------------------------
# Anti-pattern being replaced: ``return f"X failed: {e}"`` swallowed the
# exception class and produced empty messages whenever ``str(e) == ""``
# (common with chained ``raise ... from None`` and some asyncpg / network
# errors). Every MCP tool now routes errors through ``_format_tool_error``
# so callers see the exception class, a correlation id, and the server logs
# carry the full traceback under that same id.
import traceback  # noqa: E402, F401  (traceback is used by logger.exception)
import uuid as _uuid  # noqa: E402

try:  # pragma: no cover - import-shape only
    from services.logger_config import get_logger as _get_logger

    _log = _get_logger(__name__)
except Exception:  # pragma: no cover - structlog/services optional
    _log = logging.getLogger(__name__)


def _format_tool_error(tool_name: str, e: Exception) -> str:
    """Return a user-visible error string and log the full traceback.

    The returned string always includes the exception class name and a short
    request id (``rid``); the server log carries the full traceback under the
    same id so an operator can grep for it. Use this in every MCP tool's
    ``except Exception`` block instead of ``f"... failed: {e}"``.
    """
    rid = _uuid.uuid4().hex[:8]
    _log.exception("[mcp-tool] %s failed [rid=%s]", tool_name, rid)
    return f"{tool_name} failed (rid={rid}): {type(e).__name__}: {e}"

OLLAMA_URL = _require_env("OLLAMA_URL")
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
    except urllib.error.HTTPError as e:
        # Capture the response body — it contains the actual error details
        try:
            error_body = json.loads(e.read())
            return {"error": f"HTTP {e.code}", **error_body}
        except Exception:
            return {"error": f"HTTP {e.code}: {str(e)[:200]}"}
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
                "FROM pipeline_tasks_view WHERE status = $1 ORDER BY created_at DESC LIMIT $2",
                status, limit,
            )
        else:
            rows = await pool.fetch(
                "SELECT task_id, topic, status, quality_score, created_at "
                "FROM pipeline_tasks_view ORDER BY created_at DESC LIMIT $1",
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


async def _resolve_task_id(task_id: str) -> str:
    """Resolve a short task ID prefix to the full UUID via database lookup."""
    if len(task_id) >= 32:
        return task_id
    try:
        pool = await _get_pool()
        row = await pool.fetchrow(
            "SELECT task_id FROM pipeline_tasks_view WHERE task_id::text LIKE $1 || '%' LIMIT 1",
            task_id,
        )
        if row:
            return str(row["task_id"])
    except Exception:
        pass
    return task_id  # Fall back to whatever was given


@mcp.tool()
async def approve_post(task_id: str) -> str:
    """Approve a content task for publishing."""
    full_id = await _resolve_task_id(task_id)
    result = _api("POST", f"/api/tasks/{full_id}/approve")
    return f"Status: {result.get('status', result.get('error', '?'))}"


@mcp.tool()
async def reject_post(task_id: str, reason: str = "Rejected by reviewer") -> str:
    """Reject a content task. Provide a reason for feedback to the pipeline."""
    full_id = await _resolve_task_id(task_id)
    result = _api("POST", f"/api/tasks/{full_id}/reject", data={
        "reason": reason,
        "feedback": reason,
        "allow_revisions": False,
    })
    status = result.get("status", result.get("error", "?"))
    return f"Rejected: {status} — {reason}"


@mcp.tool()
async def publish_post(task_id: str) -> str:
    """Publish an approved content task to the configured site."""
    full_id = await _resolve_task_id(task_id)
    result = _api("POST", f"/api/tasks/{full_id}/publish")
    return f"Status: {result.get('status', result.get('error', '?'))}"


@mcp.tool()
async def get_post_count() -> str:
    """Get the total number of published posts on the configured site."""
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

    # Site (configurable via SITE_URL; defaults to local Next.js dev server)
    site_url = os.getenv("SITE_URL", "http://localhost:3000")
    try:
        req = urllib.request.Request(site_url)
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
        resp = urllib.request.urlopen(f"{API_URL}/api/health", timeout=5)
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

def _strip_category_prefix(key: str) -> tuple[str, str | None]:
    """Strip an optional ``<category>/`` prefix from a settings key.

    ``list_settings`` renders rows as ``category/key`` (e.g.
    ``site/public_site_url``), so operators copy-paste that form into
    ``get_setting`` / ``set_setting``. The DB only stores the bare key,
    so we strip the prefix before lookup. The supplied prefix is
    returned alongside so callers can warn when it disagrees with the
    row's actual ``category`` column (Glad-Labs/poindexter#253).
    """
    if "/" not in key:
        return key, None
    cat, _, bare = key.partition("/")
    return bare, cat


@mcp.tool()
async def get_setting(key: str) -> str:
    """Get a configuration setting value from the database.

    Accepts either a bare ``key`` or the ``category/key`` form printed by
    :func:`list_settings`. If a category prefix is supplied but does not
    match the row's actual category, a warning is logged and the value
    is still returned (the supplied prefix is informational only).
    """
    try:
        pool = await _get_pool()
        bare_key, declared_category = _strip_category_prefix(key)
        row = await pool.fetchrow(
            "SELECT key, value, category, is_secret FROM app_settings WHERE key = $1",
            bare_key,
        )
        if not row:
            return f"Setting '{key}' not found."
        if declared_category and declared_category != (row["category"] or ""):
            logger.warning(
                "get_setting: supplied category prefix %r does not match "
                "row's actual category %r for key %r — proceeding anyway",
                declared_category, row["category"] or "", bare_key,
            )
        val = "********" if row["is_secret"] else row["value"]
        return f"{row['key']} = {val} (category: {row['category'] or '?'})"
    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
async def set_setting(key: str, value: str) -> str:
    """Update a configuration setting in the database.

    Accepts either a bare ``key`` or the ``category/key`` form printed by
    :func:`list_settings`. If a category prefix is supplied but does not
    match the row's existing category, a warning is logged and the
    update proceeds against the bare key.

    Respects agent_permissions table — checks if mcp_server is allowed to write app_settings.
    """
    bare_key, declared_category = _strip_category_prefix(key)

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
                    json.dumps({"key": bare_key, "value": value}),
                )
                return f"Permission denied: change to {bare_key} queued for approval"
            return f"Permission denied: mcp_server cannot write to app_settings"
    except Exception:
        pass  # Permission check failed — fall through to direct DB write

    try:
        pool = await _get_pool()
        if declared_category:
            existing_category = await pool.fetchval(
                "SELECT category FROM app_settings WHERE key = $1", bare_key
            )
            if existing_category is not None and declared_category != (existing_category or ""):
                logger.warning(
                    "set_setting: supplied category prefix %r does not match "
                    "row's existing category %r for key %r — updating value anyway",
                    declared_category, existing_category or "", bare_key,
                )
        await pool.execute(
            "INSERT INTO app_settings (key, value) VALUES ($1, $2) "
            "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = NOW()",
            bare_key, value,
        )
        return f"Updated: {bare_key} = {value}"
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
# SEMANTIC MEMORY TOOLS (pgvector)
# ============================================================================

@mcp.tool()
async def search_memory(
    query: str,
    top_k: int = 8,
    source_filter: str = "",
    min_similarity: float = 0.3,
    include_summaries: bool = True,
) -> str:
    """Search semantic memory (pgvector) using natural language.

    Searches across ALL embedded content: memory files, blog posts, Gitea issues, audit logs.
    Use this to recall prior decisions, find related content, or check what the system knows.

    Results below min_similarity are filtered out to reduce noise as the
    embedding count grows. The similarity score is shown per result so
    callers can gauge relevance.

    Args:
        query: Natural language search query (e.g. "cost tracking decisions", "ollama configuration")
        top_k: Number of results to return (default 8)
        source_filter: Optional filter by source_table (e.g. "memory", "post", "issue", "audit")
        min_similarity: Minimum cosine similarity threshold (default 0.3). Results below this are dropped.
        include_summaries: If False, exclude collapsed-cluster summary rows from results
            (GH-81). Defaults to True — summaries preserve semantic signal from
            collapsed old embeddings and are usually what you want.
    """
    try:
        embedding = await _embed_text(query)
        pool = await _get_pool()

        vector_str = "[" + ",".join(str(v) for v in embedding) + "]"

        # Fetch more candidates than requested so we can filter by threshold
        fetch_limit = top_k * 3
        summaries_clause = "" if include_summaries else " AND is_summary = FALSE"

        if source_filter:
            rows = await pool.fetch(
                f"""
                SELECT source_table, source_id, text_preview, metadata,
                       1 - (embedding <=> $1::vector) as similarity
                FROM embeddings
                WHERE source_table = $2{summaries_clause}
                ORDER BY embedding <=> $1::vector
                LIMIT $3
                """,
                vector_str, source_filter, fetch_limit,
            )
        else:
            where_sql = f"WHERE TRUE{summaries_clause}" if summaries_clause else ""
            rows = await pool.fetch(
                f"""
                SELECT source_table, source_id, text_preview, metadata,
                       1 - (embedding <=> $1::vector) as similarity
                FROM embeddings
                {where_sql}
                ORDER BY embedding <=> $1::vector
                LIMIT $2
                """,
                vector_str, fetch_limit,
            )

        # Apply similarity threshold and cap at top_k
        rows = [r for r in rows if float(r["similarity"]) >= min_similarity][:top_k]

        if not rows:
            return f'No results for "{query}" above similarity threshold {min_similarity}.'

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
        return _format_tool_error("Memory search", e)


@mcp.tool()
async def recall_decision(topic: str) -> str:
    """Search memory specifically for past decisions, feedback, and project context.

    Filters to memory-type embeddings only (Claude Code memory, OpenClaw memory, shared context).
    Best for: "why did we choose X", "what was decided about Y", "user preferences for Z".
    """
    return await search_memory(topic, top_k=5, source_filter="memory")


@mcp.tool()
async def find_similar_posts(topic: str, top_k: int = 5) -> str:
    """Find published blog posts similar to a topic. Use before creating new content to avoid duplicates.

    Filters to `source_table='posts'`. (Was `source_filter="post"` singular until
    2026-04-11 — that silently returned zero rows every call because the actual
    DB value is plural. Caught and fixed during shared-memory architecture work.)
    """
    return await search_memory(topic, top_k=top_k, source_filter="posts")


@mcp.tool()
async def store_memory(
    text: str,
    writer: str = "claude-code",
    source_id: str = "",
    tags: str = "",
    source_table: str = "memory",
) -> str:
    """Write a memory note directly into the shared pgvector store.

    Use this when an agent learns something worth recording DURING a session —
    a decision, a user preference, a non-obvious finding — and wants it to be
    immediately queryable from every other tool (Claude Code, OpenClaw, the
    worker's RAG context, the poindexter CLI). Skips the file-based flow
    entirely; the note is embedded and indexed before this function returns.

    The `writer` column added in migration 024 makes origin explicit, so two
    different tools writing to source_id="shared/decision_123.md" under
    different writers don't collide.

    Args:
        text: The full text to store. Usually 200-2000 chars. Will be embedded
              by nomic-embed-text (768 dim).
        writer: Origin label. Standard values: "claude-code", "openclaw",
                "worker", "user". New writers are fine — just stay consistent.
        source_id: Stable identifier for this memory entry. If empty, a
                   timestamp-based ID is generated. Use for dedup: calling
                   store_memory with the same source_id updates the existing
                   row in place rather than creating a duplicate.
        tags: Comma-separated tag list for metadata (e.g. "decision,pipeline,2026-q2").
        source_table: Top-level namespace. Default "memory" for notes; use
                      "audit" for sync-state events etc. Don't write to
                      "posts"/"issues" — those are managed by the auto-embed job.
    """
    try:
        text = (text or "").strip()
        if not text:
            return "store_memory failed: text is empty."
        if not writer:
            return "store_memory failed: writer is required (e.g. 'claude-code', 'openclaw', 'user')."

        if not source_id:
            # Generate a stable-ish id so duplicate calls in quick succession
            # get a unique row. Use writer + epoch seconds + hash of text.
            epoch = int(datetime.now(timezone.utc).timestamp())
            short = hashlib.sha256(text.encode("utf-8")).hexdigest()[:10]
            source_id = f"{writer}/adhoc-{epoch}-{short}.md"

        content_hash_value = hashlib.sha256(text.encode("utf-8")).hexdigest()
        embedding = await _embed_text(text)
        if not embedding or len(embedding) != 768:
            return f"store_memory failed: got {len(embedding) if embedding else 0}-dim vector from embedder (expected 768)."

        metadata: dict[str, Any] = {
            "origin": writer,
            "writer": writer,
            "stored_via": "mcp.store_memory",
            "stored_at": datetime.now(timezone.utc).isoformat(),
        }
        if tags:
            metadata["tags"] = [t.strip() for t in tags.split(",") if t.strip()]

        vector_str = "[" + ",".join(str(v) for v in embedding) + "]"
        preview = text[:500].replace("\n", " ").strip()
        now = datetime.now(timezone.utc)

        pool = await _get_pool()
        await pool.execute(
            """
            INSERT INTO embeddings (source_table, source_id, chunk_index,
                                    content_hash, text_preview,
                                    embedding_model, embedding, metadata,
                                    writer, origin_path,
                                    created_at, updated_at)
            VALUES ($1, $2, 0, $3, $4, $5, $6::vector, $7::jsonb, $8, $9, $10, $10)
            ON CONFLICT (source_table, source_id, chunk_index, embedding_model)
            DO UPDATE SET content_hash = EXCLUDED.content_hash,
                          text_preview = EXCLUDED.text_preview,
                          embedding    = EXCLUDED.embedding,
                          metadata     = EXCLUDED.metadata,
                          writer       = EXCLUDED.writer,
                          origin_path  = EXCLUDED.origin_path,
                          updated_at   = EXCLUDED.updated_at
            """,
            source_table,
            source_id,
            content_hash_value,
            preview,
            EMBED_MODEL,
            vector_str,
            json.dumps(metadata),
            writer,
            source_id,
            now,
        )

        return (
            f"Stored: [{source_table}/{writer}] {source_id}\n"
            f"  {len(text)} chars, 768-dim vector, content_hash={content_hash_value[:12]}...\n"
            f"  Queryable via search_memory immediately."
        )
    except Exception as e:
        return _format_tool_error("store_memory", e)


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
        return _format_tool_error("Audit log query", e)


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
        return _format_tool_error("Audit summary", e)


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
        return _format_tool_error("Brain knowledge query", e)


# ============================================================================
# NICHE TOPIC-DISCOVERY TOOLS
# ============================================================================
# Mirror the ``poindexter topics ...`` CLI commands so an MCP client can drive
# the discover -> rank -> batch -> gate flow exposed by
# ``services.topic_batch_service.TopicBatchService``.


@mcp.tool()
async def topics_show_batch(niche: str) -> str:
    """Show the current open batch for a niche, sorted by effective_score."""
    try:
        pool = await _get_pool()
        from services.niche_service import NicheService
        from services.topic_batch_service import TopicBatchService
        n = await NicheService(pool).get_by_slug(niche)
        if not n:
            return f"unknown niche: {niche}"
        async with pool.acquire() as conn:
            bid = await conn.fetchval(
                "SELECT id FROM topic_batches WHERE niche_id = $1 AND status = 'open'",
                n.id,
            )
        if bid is None:
            return f"No open batch for niche {niche}."
        view = await TopicBatchService(pool).show_batch(batch_id=bid)
        lines = [f"Batch {view.id} (status={view.status}, niche={niche})"]
        for c in view.candidates:
            marker = f"#{c.operator_rank}" if c.operator_rank else f"sys#{c.rank_in_batch}"
            lines.append(
                f"  {marker:6s} [{c.kind:8s}] eff={c.effective_score:5.1f} | {c.id} | {c.title}"
            )
        return "\n".join(lines)
    except Exception as e:
        return _format_tool_error("topics_show_batch", e)


@mcp.tool()
async def topics_rank_batch(batch_id: str, ordered_candidate_ids: list[str]) -> str:
    """Set operator ranking for a batch's candidates. Pass IDs in best-first order."""
    try:
        pool = await _get_pool()
        from services.topic_batch_service import TopicBatchService
        await TopicBatchService(pool).rank_batch(
            batch_id=batch_id, ordered_candidate_ids=ordered_candidate_ids,
        )
        return f"Ranked {len(ordered_candidate_ids)} candidates in batch {batch_id}"
    except Exception as e:
        return _format_tool_error("topics_rank_batch", e)


@mcp.tool()
async def topics_edit_winner(batch_id: str, topic: str = "", angle: str = "") -> str:
    """Edit the title/angle of the rank-1 candidate before resolution."""
    if not topic and not angle:
        return "topics_edit_winner failed: provide topic and/or angle"
    try:
        pool = await _get_pool()
        from services.topic_batch_service import TopicBatchService
        await TopicBatchService(pool).edit_winner(
            batch_id=batch_id,
            topic=topic or None,
            angle=angle or None,
        )
        return "Edited winner."
    except Exception as e:
        return _format_tool_error("topics_edit_winner", e)


@mcp.tool()
async def topics_resolve_batch(batch_id: str) -> str:
    """Resolve a batch — advance the rank-1 candidate into the content pipeline."""
    try:
        pool = await _get_pool()
        from services.topic_batch_service import TopicBatchService
        await TopicBatchService(pool).resolve_batch(batch_id=batch_id)
        return f"Resolved {batch_id}"
    except Exception as e:
        return _format_tool_error("topics_resolve_batch", e)


@mcp.tool()
async def topics_reject_batch(batch_id: str, reason: str = "") -> str:
    """Reject a batch — discard candidates, allow a fresh sweep."""
    try:
        pool = await _get_pool()
        from services.topic_batch_service import TopicBatchService
        await TopicBatchService(pool).reject_batch(batch_id=batch_id, reason=reason)
        return f"Rejected {batch_id}"
    except Exception as e:
        return _format_tool_error("topics_reject_batch", e)


if __name__ == "__main__":
    mcp.run(transport="stdio")
