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

# OAuth helper — local mirror of services.auth.oauth_client. See
# oauth_client.py docstring for why this is mirrored rather than
# imported from the worker tree (Glad-Labs/poindexter#243).
from oauth_client import (  # noqa: E402 — local module
    MCP_CLIENT_ID_KEY,
    MCP_CLIENT_SECRET_KEY,
    MCP_DEFAULT_SCOPES,
    McpOAuthClient,
    oauth_client_from_pool,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("poindexter-mcp")

# Customer-facing env var names — POINDEXTER_*. Old GLADLABS_* names still
# accepted as fallback for users upgrading from the pre-rebrand release.
# #198: no hardcoded defaults. Every value must come from the environment;
# the MCP server fails loud on startup if something required is missing.


def _first_env(*names: str) -> str | None:
    """Return the first set env var in order, or None. Pure lookup — no exit.

    Validation happens in :func:`setup_runtime`, called by the stdio entry
    point AND by any other process that imports this module to mount the
    HTTP transport. Keeping module import side-effect-free (no
    ``sys.exit``) is what lets the worker (``cofounder_agent.main``)
    import ``mcp`` and call ``mcp.streamable_http_app()`` to expose
    these tools as a remote MCP server (Glad-Labs/poindexter#237).
    """
    for n in names:
        v = os.getenv(n)
        if v:
            return v
    return None


# Module globals — populated from env at import (lazy, no exit). Validated
# in setup_runtime(); read at call time inside individual tool functions.
API_URL: str | None = _first_env("POINDEXTER_API_URL", "GLADLABS_API_URL")
# POINDEXTER_API_TOKEN was removed in Phase 3 (#249) — worker auth uses
# OAuth JWTs minted via app_settings.mcp_oauth_client_*.
OLLAMA_URL: str | None = _first_env("OLLAMA_URL")
EMBED_MODEL = os.getenv("EMBED_MODEL", "nomic-embed-text")
LOCAL_DB_DSN: str | None = None  # Populated by setup_runtime()


# --- Tool error formatting helper (#259) -----------------------------------
# Anti-pattern being replaced: ``return f"X failed: {e}"`` swallowed the
# exception class and produced empty messages whenever ``str(e) == ""``
# (common with chained ``raise ... from None`` and some asyncpg / network
# errors). Every MCP tool now routes errors through ``_format_tool_error``
# so callers see the exception class, a correlation id, and the server logs
# carry the full traceback under that same id.
#
# Import is kept at module scope (not in setup_runtime) because the
# ``_format_tool_error`` symbol is referenced by tool functions defined
# at module scope below, which evaluate type hints / decorators at import
# time. The src/cofounder_agent path is injected here so the
# services.logger_config import resolves; both injections are no-ops if
# the directories don't exist (e.g. minimal CI shape).
import sys as _sys_boot
import uuid as _uuid  # noqa: E402
from pathlib import Path as _Path_boot

_repo_root_boot = _Path_boot(__file__).resolve().parents[1]
if str(_repo_root_boot) not in _sys_boot.path:
    _sys_boot.path.insert(0, str(_repo_root_boot))
_cofounder_root = _repo_root_boot / "src" / "cofounder_agent"
if _cofounder_root.is_dir() and str(_cofounder_root) not in _sys_boot.path:
    _sys_boot.path.insert(0, str(_cofounder_root))

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


def setup_runtime() -> None:
    """Validate required env + resolve DB DSN. Idempotent.

    Must be called before :data:`mcp` actually serves requests — i.e.
    before ``mcp.run()`` (stdio) or before mounting
    ``mcp.streamable_http_app()`` (HTTP). Raises ``RuntimeError`` if a
    required value is missing; callers are expected to surface that
    cleanly (operator-notify + exit for stdio, return 500 for HTTP).

    Auth note (#243, finalised in #249): MCP tools mint OAuth JWTs
    from app_settings (``mcp_oauth_client_id`` / ``mcp_oauth_client_secret``).
    ``POINDEXTER_API_TOKEN`` is no longer consulted — Phase 3 removed
    the static-Bearer fallback. Run ``poindexter auth migrate-mcp`` if
    the OAuth client hasn't been provisioned; the helper raises loudly
    on the first ``_api`` call with a pointer to the migration command.
    """
    global LOCAL_DB_DSN
    missing = []
    if not API_URL:
        missing.append("POINDEXTER_API_URL")
    if not OLLAMA_URL:
        missing.append("OLLAMA_URL")
    if missing:
        joined = ", ".join(missing)
        raise RuntimeError(
            f"MCP server missing required env vars: {joined}. "
            "For local dev the Claude desktop config exports these; "
            "for the worker-mounted HTTP transport, main.py wires them "
            "from app_settings before mounting (see issue #237)."
        )

    # POINDEXTER_API_TOKEN is no longer consulted (Phase 3 / #249).
    # The OAuth helper reads ``mcp_oauth_client_id`` /
    # ``mcp_oauth_client_secret`` from app_settings on the first
    # ``_api`` call and raises loudly with a pointer to
    # ``poindexter auth migrate-mcp`` if they're missing.

    if LOCAL_DB_DSN is None:
        # Route through the bootstrap resolver so
        # ~/.poindexter/bootstrap.toml works for the MCP server just like
        # it does for the brain daemon (#198).
        import sys as _sys_boot
        from pathlib import Path as _Path_boot

        _repo_root_boot = _Path_boot(__file__).resolve().parents[1]
        if str(_repo_root_boot) not in _sys_boot.path:
            _sys_boot.path.insert(0, str(_repo_root_boot))
        from brain.bootstrap import require_database_url

        LOCAL_DB_DSN = require_database_url(source="mcp_server")

# Lazy-initialized connection pool and HTTP clients
_pool: asyncpg.Pool | None = None
_http: httpx.AsyncClient | None = None
# Worker-API OAuth client. Built lazily on first call to ``_get_oauth``
# because we need the asyncpg pool already up to read credentials from
# app_settings (and we want to defer that until someone actually calls
# a tool that talks to the worker).
_oauth: McpOAuthClient | None = None


async def _get_pool() -> asyncpg.Pool:
    """Get or create the local pgvector connection pool."""
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(LOCAL_DB_DSN, min_size=1, max_size=3)
    return _pool


async def _get_http() -> httpx.AsyncClient:
    """Get or create the async HTTP client (Ollama + ad-hoc probes)."""
    global _http
    if _http is None:
        _http = httpx.AsyncClient(timeout=30.0)
    return _http


async def _get_oauth() -> McpOAuthClient:
    """Get or build the worker-API OAuth client.

    Resolution (Glad-Labs/poindexter#243, finalised in #249):

    1. ``app_settings.mcp_oauth_client_id`` + ``mcp_oauth_client_secret``
       → mints + caches a JWT against ``POST /token``.
    2. Otherwise raises loudly — run ``poindexter auth migrate-mcp`` to
       provision the OAuth client. The legacy static-Bearer fallback was
       removed in Phase 3 (#249).

    Re-uses the same asyncpg pool the rest of this module uses, so the
    credential read is one extra DB round-trip on first call.
    """
    global _oauth
    if _oauth is None:
        pool = await _get_pool()
        _oauth = await oauth_client_from_pool(
            pool,
            base_url=API_URL or "",
            client_id_key=MCP_CLIENT_ID_KEY,
            client_secret_key=MCP_CLIENT_SECRET_KEY,
            # Don't request a scope subset — let the client use its full
            # grant. The migration helper provisions ``api:read api:write``
            # by default; tighter scoping (e.g. mcp:read for a read-only
            # operator MCP variant) belongs in a follow-up.
            scopes=None,
        )
    return _oauth


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


async def _api(method: str, path: str, data: dict | None = None) -> dict:
    """Call the Poindexter worker API through the OAuth-aware client.

    Uses ``McpOAuthClient`` (mint + cache + 401 retry, with legacy
    static-Bearer fallback) so the MCP server gets the same auth surface
    as the CLI and brain — see Glad-Labs/poindexter#243. The function
    keeps its original error-flattening shape (``{"error": "..."}`` on
    failure) so existing tool callers don't need updates.
    """
    try:
        oauth = await _get_oauth()
    except Exception as e:  # noqa: BLE001 — surfaced to caller as dict
        return {"error": f"oauth init failed: {type(e).__name__}: {str(e)[:200]}"}

    try:
        kwargs: dict[str, Any] = {"timeout": 15.0}
        if data is not None:
            kwargs["json"] = data
        resp = await oauth.request(method, path, **kwargs)
    except Exception as e:  # noqa: BLE001
        return {"error": str(e)[:200]}

    if resp.status_code // 100 != 2:
        # Mirror the previous urllib.HTTPError handling — try to surface
        # the structured error body, fall back to the status text.
        try:
            error_body = resp.json()
        except ValueError:
            return {"error": f"HTTP {resp.status_code}: {resp.text[:200]}"}
        return {"error": f"HTTP {resp.status_code}", **error_body}

    try:
        return resp.json()
    except ValueError:
        return {"error": f"non-JSON response: {resp.text[:200]}"}


# ============================================================================
# CONTENT PIPELINE TOOLS
# ============================================================================

@mcp.tool()
async def create_post(topic: str, category: str = "technology", target_audience: str = "developers and founders") -> str:
    """Create a new blog post task in the content pipeline. The worker will generate it via AI."""
    result = await _api("POST", "/api/tasks", {
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
    result = await _api("POST", f"/api/tasks/{full_id}/approve")
    return f"Status: {result.get('status', result.get('error', '?'))}"


@mcp.tool()
async def reject_post(task_id: str, reason: str = "Rejected by reviewer") -> str:
    """Reject a content task. Provide a reason for feedback to the pipeline."""
    full_id = await _resolve_task_id(task_id)
    result = await _api("POST", f"/api/tasks/{full_id}/reject", data={
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
    result = await _api("POST", f"/api/tasks/{full_id}/publish")
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
async def check_health() -> str:
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
    result = await _api("GET", "/api/health")
    checks.append(f"API: {result.get('status', result.get('error', '?'))}")

    # Posts
    result = await _api("GET", "/api/posts?limit=1")
    checks.append(f"Posts: {result.get('total', '?')}")

    # Worker — use the OAuth-aware client so the same auth semantics
    # apply to the diagnostic probe as to real tool calls. The previous
    # code hit /api/health without auth via urllib; the worker's
    # /api/health does require a valid bearer (or, today, the legacy
    # static one), so going through _api keeps that consistent.
    worker_health = await _api("GET", "/api/health")
    if "error" in worker_health:
        checks.append("Worker: offline")
    else:
        te = worker_health.get("components", {}).get("task_executor", {})
        checks.append(
            f"Worker: running={te.get('running')}, "
            f"processed={te.get('total_processed')}"
        )

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
async def rebuild_static_export() -> str:
    """Rebuild all static JSON files on CDN (posts, feed, sitemap, categories, authors).

    Triggers a full export of the headless CMS data to R2/S3 storage.
    Any frontend can consume these files without needing the API.
    """
    result = await _api("POST", "/api/export/rebuild")
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


# ============================================================================
# VOICE TOOLS (Half B — runtime brain-mode toggle + start_voice_call)
# ============================================================================
#
# The assistant calls `start_voice_call` when it wants to talk to Matt
# directly — typically when async chat is the wrong medium for the
# question (e.g. "I have a draft to review with you", "let's hop on a
# call", "talk to me about X", "demo this for me"). The tool optionally
# flips ``voice_agent_brain_mode`` so the next pipeline build picks
# the right brain (snappy ollama vs full Claude Code), then returns a
# tap-to-join URL the operator opens on phone or desktop.
#
# Defaults pulled from app_settings (NOT hardcoded here):
#   - voice_agent_public_join_url  → join URL ('voice' category)
#   - voice_agent_brain_mode       → effective brain after any flip
#
# Both keys are seeded by migration 20260506_220613.

# Mirrors _VALID_BRAIN_MODES in services/voice_agent_livekit.py. Kept
# duplicated here so the MCP server doesn't need to drag the pipecat
# dependency tree (livekit, kokoro, pipecat) onto the import path just
# to validate a two-string enum. If the list grows, promote both copies
# to a shared constants module.
_VALID_VOICE_BRAIN_MODES: tuple[str, ...] = ("ollama", "claude-code")


@mcp.tool()
async def start_voice_call(
    brain: str | None = None,
    note: str | None = None,
) -> str:
    """Hand the operator a tap-to-join voice call link.

    Use this when the conversation would go faster on a real call than
    over async chat — common triggers from the assistant side: "let's
    hop on a call", "Matt, I'd like to talk through this draft live",
    "I want to demo this for you", "easier if we just discuss it".

    The tool returns a join URL the operator (Matt) clicks on his
    phone or desktop browser. The always-on ``voice-agent-livekit``
    container is already in the room; he just joins.

    Args:
        brain: Optional brain-mode flip applied BEFORE returning. Valid
            values: ``"ollama"`` (snappy local LLM, read-only Poindexter
            tools — good for "what's the post count" / status questions)
            or ``"claude-code"`` (Max-sub ``claude -p`` subprocess
            bridge — slower but full repo / MCP / edit access; use this
            for "let's pair on a bug", "review this draft with me", or
            anything where the voice agent needs to be Claude Code, not
            Emma). When ``None`` (default), the existing
            ``voice_agent_brain_mode`` setting is left as-is. The flip
            is persisted to ``app_settings`` so the always-on container
            picks it up on the next pipeline build — no restart needed.

        note: Optional one-line context the assistant wants Matt to see
            so he knows why the call was initiated (e.g. "got a draft
            to review", "found a bug in the publish flow"). Echoed
            verbatim in the response and surfaced to the operator's
            client.

    Returns: a JSON string with ``join_url`` (clickable link),
        ``brain_mode`` (the effective mode after any flip), ``note``
        (echoed), and ``instructions`` (human-readable summary). Errors
        are returned as a JSON object with an ``error`` key — never a
        silent fallback (per ``feedback_no_silent_defaults``).
    """
    try:
        # Validate brain BEFORE touching the DB so a typo is rejected
        # without writing a half-applied state. The legacy
        # voice_agent_brain key is intentionally NOT consulted on the
        # write path — the assistant always touches the canonical new
        # key, and run_bot's resolver still falls back to the legacy
        # key for read.
        if brain is not None:
            normalised = str(brain).strip().lower()
            if normalised not in _VALID_VOICE_BRAIN_MODES:
                return json.dumps({
                    "error": (
                        f"Invalid brain={brain!r}. "
                        f"Valid values: {', '.join(_VALID_VOICE_BRAIN_MODES)}."
                    ),
                    "valid_brains": list(_VALID_VOICE_BRAIN_MODES),
                })
            brain = normalised

        pool = await _get_pool()

        # Optional brain flip — persist BEFORE we read back so the
        # response carries the just-flipped value, not whatever was
        # there before.
        if brain is not None:
            await pool.execute(
                """
                INSERT INTO app_settings (key, value, category, description, is_secret, is_active)
                VALUES ($1, $2, 'voice',
                        'LLM stage the always-on voice agent uses '
                        '(written by start_voice_call MCP tool).',
                        FALSE, TRUE)
                ON CONFLICT (key) DO UPDATE
                    SET value = EXCLUDED.value,
                        updated_at = NOW()
                """,
                "voice_agent_brain_mode", brain,
            )

        # Read back the effective mode + the public join URL. Falling
        # back to the legacy ``voice_agent_brain`` key for read so
        # operators who set the legacy key but never migrated still see
        # the right effective mode.
        row = await pool.fetchrow(
            """
            SELECT
                COALESCE(
                    (SELECT value FROM app_settings WHERE key = 'voice_agent_brain_mode'),
                    (SELECT value FROM app_settings WHERE key = 'voice_agent_brain'),
                    'ollama'
                ) AS brain_mode,
                COALESCE(
                    (SELECT value FROM app_settings WHERE key = 'voice_agent_public_join_url'),
                    ''
                ) AS join_url
            """,
        )
        brain_mode = (row["brain_mode"] or "ollama").strip().lower()
        join_url = (row["join_url"] or "").strip()

        if not join_url:
            # No silent fallback — fail loud so the operator notices and
            # seeds the migration / sets the value, instead of returning
            # a hardcoded URL that might not be reachable from their
            # network. (per feedback_no_silent_defaults)
            return json.dumps({
                "error": (
                    "voice_agent_public_join_url is unset. "
                    "Run migration 20260506_220613 to seed the default, "
                    "or set it manually: "
                    "`poindexter set voice_agent_public_join_url <url>`."
                ),
                "missing_setting": "voice_agent_public_join_url",
            })

        return json.dumps({
            "join_url": join_url,
            "brain_mode": brain_mode,
            "note": note,
            "instructions": (
                "Tap the join_url on your phone or click it on desktop "
                "to start the call. The voice agent is already in the "
                f"room ({brain_mode} brain). Allow microphone access "
                "when prompted; speak naturally."
            ),
        })
    except Exception as e:
        # _format_tool_error logs the traceback under a request id; the
        # JSON wrapper makes it parseable client-side without losing the
        # human-readable summary.
        return json.dumps({"error": _format_tool_error("start_voice_call", e)})


# ============================================================================
# LIVEKIT BRIDGE TOOLS — voice as a session-agnostic UI surface
# ============================================================================
#
# The bridge is the architecturally-correct alternative to the
# subprocess-spawn ``voice_agent_brain_mode=claude-code`` path. The
# always-on ``voice-agent-livekit`` container stays put — that's the
# always-on phone-tap-to-join interface using ollama as the brain. These
# tools are *additive*: an already-running Claude Code session can
# hijack a LiveKit room, claim voice in/out for itself, then hand back
# control when done.
#
# Lifecycle:
#   voice_join_room(channel_id?, session_id?)  # spin up bridge worker
#   voice_speak(text, session_id)              # TTS reply into the room
#   voice_leave_room(session_id)               # tear it down (idempotent)
#
# All three pull defaults from app_settings (per feedback_db_first_config):
#   voice_bridge_enabled            — master switch (boolean string)
#   voice_default_room              — LiveKit room name when caller omits
#   voice_bridge_stt_model          — faster-whisper model id (e.g. base.en)
#   voice_bridge_tts_voice          — Kokoro voice (e.g. af_bella)
#   voice_bridge_max_session_seconds — hard timeout, default 1800
#   voice_bridge_chunk_max_chars     — TTS chunk size, default 500
#
# Seeded by migration 20260507_*_seed_voice_bridge_app_settings.

# Import the bridge worker — kept module-level so unit tests can import
# both ``server.voice_join_room`` and ``server.livekit_bridge`` and
# patch the registry / pipe directory consistently.
try:  # pragma: no cover — import shape only
    from livekit_bridge import (  # noqa: E402
        BridgeConfig,
        chunk_text_for_tts,
        ensure_session_pipes,
        new_session_id,
        session_pipe_paths,
        speak_into_bridge,
        start_bridge,
        stop_bridge,
        voice_pipe_dir,
    )
    _BRIDGE_AVAILABLE = True
except Exception:  # noqa: BLE001 — bridge is optional in CI shape
    _BRIDGE_AVAILABLE = False


async def _bridge_settings(pool: asyncpg.Pool) -> dict[str, str]:
    """Return all voice_bridge_* keys (plus voice_default_room) as a dict.

    One round-trip to app_settings, scoped to keys we actually use, so
    a misconfigured installation can't drag in arbitrary settings.
    """
    rows = await pool.fetch(
        """
        SELECT key, value FROM app_settings
        WHERE key IN (
            'voice_bridge_enabled',
            'voice_default_room',
            'voice_bridge_stt_model',
            'voice_bridge_tts_voice',
            'voice_bridge_max_session_seconds',
            'voice_bridge_chunk_max_chars'
        )
        """,
    )
    return {r["key"]: (r["value"] or "") for r in rows}


def _parse_bool(value: str, *, default: bool) -> bool:
    """Forgiving boolean parser for app_settings string values."""
    if not value:
        return default
    return value.strip().lower() in {"true", "1", "yes", "on", "y"}


@mcp.tool()
async def voice_join_room(
    channel_id: str = "",
    session_id: str = "",
) -> str:
    """Spin up the LiveKit bridge so this Claude Code session can use voice.

    Voice in becomes the next user input to *this* session via a per-session
    pipe (``~/.poindexter/voice/<session_id>.in``); session output written
    via ``voice_speak`` becomes voice out. The always-on
    ``voice-agent-livekit`` container is unaffected — this tool joins the
    same LiveKit room as a separate participant identity (default
    ``claude-bridge``).

    Args:
        channel_id: LiveKit room name. Defaults to
            ``app_settings.voice_default_room`` (seeded as
            ``"claude-bridge"`` by the bridge migration). Pass ``"ops"``
            etc. to claim a non-default room.
        session_id: Optional explicit id. When empty, one is generated and
            returned in the response. The slash command uses this id for
            subsequent ``voice_speak`` / ``voice_leave_room`` calls AND to
            point a Monitor watcher at the right ``.in`` pipe.

    Returns: JSON string with ``session_id``, ``room``, ``in_pipe``,
        ``out_pipe``, and ``status``. ``status`` is always either
        ``"started"`` or an explicit error — no silent ok=True per
        feedback_no_silent_defaults.
    """
    try:
        if not _BRIDGE_AVAILABLE:
            return json.dumps({
                "error": (
                    "livekit_bridge module failed to import — bridge "
                    "is unavailable. Check the MCP server logs for "
                    "the import-time traceback."
                ),
            })
        pool = await _get_pool()
        settings = await _bridge_settings(pool)

        if not _parse_bool(settings.get("voice_bridge_enabled", ""), default=True):
            return json.dumps({
                "error": (
                    "voice_bridge_enabled=false in app_settings — bridge "
                    "is administratively disabled. Set it to true to "
                    "re-enable: `poindexter set voice_bridge_enabled true`."
                ),
                "missing_setting": None,
                "disabled": True,
            })

        room = (channel_id or settings.get("voice_default_room", "")).strip()
        if not room:
            return json.dumps({
                "error": (
                    "No room — pass channel_id or seed "
                    "voice_default_room in app_settings (migration "
                    "20260507_seed_voice_bridge_app_settings)."
                ),
                "missing_setting": "voice_default_room",
            })

        try:
            chunk_max = int(settings.get("voice_bridge_chunk_max_chars") or "500")
        except ValueError:
            chunk_max = 500
        try:
            max_session_seconds = int(
                settings.get("voice_bridge_max_session_seconds") or "1800",
            )
        except ValueError:
            max_session_seconds = 1800

        sid = (session_id or "").strip() or new_session_id()
        ensure_session_pipes(sid)
        config = BridgeConfig(
            room=room,
            chunk_max_chars=chunk_max,
            max_session_seconds=max_session_seconds,
        )

        state = await start_bridge(session_id=sid, config=config)
        paths = session_pipe_paths(state.session_id)

        return json.dumps({
            "status": "started",
            "session_id": state.session_id,
            "room": room,
            "in_pipe": str(paths["in"]),
            "out_pipe": str(paths["out"]),
            "max_session_seconds": max_session_seconds,
            "chunk_max_chars": chunk_max,
            "instructions": (
                "Watch in_pipe with `tail -F` (or a Monitor task) for "
                "transcripts. Write text to out_pipe (or call "
                "voice_speak) to TTS into the room."
            ),
        })
    except Exception as e:
        return json.dumps({"error": _format_tool_error("voice_join_room", e)})


@mcp.tool()
async def voice_speak(text: str, session_id: str) -> str:
    """TTS ``text`` into the bridge session's LiveKit room.

    Long replies (over ``voice_bridge_chunk_max_chars`` chars) are
    chunked at sentence boundaries so the user can interrupt mid-reply
    — the bridge emits one TTS request per chunk. Returns the chunk
    count plus any error.

    Args:
        text: The reply to speak. Empty / whitespace-only text is a no-op
            (returns ``chunks=0``) — explicit so accidental empty calls
            don't ping the room.
        session_id: The bridge session id returned by ``voice_join_room``.

    Returns: JSON string with ``status``, ``session_id``, and ``chunks``
        (number of TTS requests enqueued). ``status="not_running"`` if
        the bridge isn't up — caller should re-issue ``voice_join_room``.
    """
    try:
        if not _BRIDGE_AVAILABLE:
            return json.dumps({"error": "livekit_bridge module unavailable"})
        if not session_id or not session_id.strip():
            return json.dumps({"error": "session_id is required"})
        sid = session_id.strip()
        try:
            chunks = await speak_into_bridge(sid, text or "")
        except RuntimeError as e:
            # speak_into_bridge raises with a precise message when the
            # session isn't registered — surface as a structured error.
            return json.dumps({
                "error": str(e),
                "status": "not_running",
                "session_id": sid,
            })
        return json.dumps({
            "status": "queued",
            "session_id": sid,
            "chunks": chunks,
        })
    except Exception as e:
        return json.dumps({"error": _format_tool_error("voice_speak", e)})


@mcp.tool()
async def voice_leave_room(session_id: str) -> str:
    """Disconnect the bridge worker for ``session_id``. Idempotent.

    Calling twice on the same id returns ``status="stopped"`` the first
    time and ``status="not_running"`` the second — never raises in the
    happy path. The ``.in`` / ``.out`` pipe files are intentionally left
    in place so the slash command's Monitor can read final lines after
    the bridge exits.
    """
    try:
        if not _BRIDGE_AVAILABLE:
            return json.dumps({"error": "livekit_bridge module unavailable"})
        if not session_id or not session_id.strip():
            return json.dumps({"error": "session_id is required"})
        sid = session_id.strip()
        stopped = await stop_bridge(sid)
        return json.dumps({
            "status": "stopped" if stopped else "not_running",
            "session_id": sid,
        })
    except Exception as e:
        return json.dumps({"error": _format_tool_error("voice_leave_room", e)})


def _stdio_main() -> None:
    """Entry point for the stdio transport (existing local clients).

    Validates env / DB DSN via setup_runtime; if anything's missing,
    surfaces it through the operator-notifier path that the original
    ``_require_env`` used to fire (Telegram → Discord → alerts.log)
    before exiting with a clear status.
    """
    try:
        setup_runtime()
    except RuntimeError as exc:
        import sys as _sys
        from pathlib import Path as _Path

        _repo_root = _Path(__file__).resolve().parents[1]
        if str(_repo_root) not in _sys.path:
            _sys.path.insert(0, str(_repo_root))
        try:
            from brain.operator_notifier import notify_operator
            notify_operator(
                title="MCP server cannot start — missing required env var",
                detail=str(exc),
                source="mcp_server",
                severity="critical",
            )
        except Exception:  # noqa: BLE001 — notifier is best-effort
            pass
        print(f"FATAL: {exc}", file=_sys.stderr)
        _sys.exit(2)

    mcp.run(transport="stdio")


if __name__ == "__main__":
    _stdio_main()
