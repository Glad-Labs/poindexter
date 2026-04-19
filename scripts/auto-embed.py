"""
Auto-Embed Cron — Keep pgvector in sync with new content.

Standalone script designed to run as a Windows Scheduled Task every hour.
Connects to both the Railway cloud DB (for published posts) and the local
pgvector DB (for embeddings), embeds any new or changed content, and logs
all activity to ~/.gladlabs/auto-embed.log.

Dependencies: asyncpg, httpx (same as embed-memory.py)

Usage:
    pythonw scripts/auto-embed.py      # windowless (scheduled task)
    python  scripts/auto-embed.py      # interactive with stdout
"""

import asyncio
import hashlib
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import asyncpg
import httpx

# Make `poindexter.memory` importable when this script is run from the repo
# root or from inside the worker container. The package lives under
# `src/cofounder_agent/poindexter/` for docker-build-context reasons; when
# the eventual refactor moves it to `src/poindexter/`, update this path.
_REPO_ROOT = Path(__file__).resolve().parent.parent
for _candidate in (
    _REPO_ROOT / "src" / "cofounder_agent",  # host layout
    Path("/app"),                             # worker container layout
):
    if _candidate.exists() and str(_candidate) not in sys.path:
        sys.path.insert(0, str(_candidate))

try:
    from poindexter.memory import MemoryClient
    _HAS_MEMORY_CLIENT = True
except ImportError as _imp_err:
    MemoryClient = None  # type: ignore[assignment,misc]
    _HAS_MEMORY_CLIENT = False
    _MEMORY_CLIENT_IMPORT_ERR = _imp_err

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Resolve DB URL via bootstrap.toml first, env vars as fallback
_dsn = os.getenv("LOCAL_DATABASE_URL") or os.getenv("DATABASE_URL", "")
if not _dsn:
    try:
        import sys as _sys
        from pathlib import Path as _Path
        for _p in _Path(__file__).resolve().parents:
            if (_p / "brain" / "bootstrap.py").is_file():
                if str(_p) not in _sys.path:
                    _sys.path.insert(0, str(_p))
                break
        from brain.bootstrap import resolve_database_url
        _dsn = resolve_database_url() or ""
    except Exception:
        pass
LOCAL_DSN = _dsn or "postgresql://poindexter:poindexter-brain-local@localhost:15432/poindexter_brain"
CLOUD_DSN = LOCAL_DSN

# In-Docker URL translation. Per Matt's DB-first-config rule, app_settings holds
# canonical URLs (ollama_url, gitea_url) that work from the host (e.g.
# http://localhost:3001). Inside a container, `localhost` loops back to the
# container itself, not the host. When IN_DOCKER=true, rewrite `localhost` and
# `127.0.0.1` to `host.docker.internal`, preserving the port — so the same DB
# value works from both sides without per-env env-var overrides.
IN_DOCKER = os.getenv("IN_DOCKER", "").lower() in ("1", "true", "yes") \
    or Path("/.dockerenv").exists()


def localize_url(url: str) -> str:
    if not url or not IN_DOCKER:
        return url
    return (
        url.replace("://localhost:", "://host.docker.internal:")
           .replace("://127.0.0.1:", "://host.docker.internal:")
    )


OLLAMA_URL = localize_url(os.getenv("OLLAMA_URL") or "http://127.0.0.1:11434")
EMBED_MODEL = "nomic-embed-text"
MAX_CHARS = 6000  # nomic-embed-text has 8192 token context; ~6k chars stays safe

# Source tables
MEMORY_SOURCE = "memory"
POSTS_SOURCE = "posts"
ISSUES_SOURCE = "issues"
AUDIT_SOURCE = "audit"

# Gitea defaults. Real values come from app_settings at connect time — these
# placeholders just keep the script importable before the DB load.
GITEA_URL = localize_url(os.getenv("GITEA_URL") or "http://localhost:3001")
GITEA_USER = os.getenv("GITEA_USER") or ""
GITEA_PASS = os.getenv("GITEA_PASSWORD") or os.getenv("GITEA_PASS") or ""
GITEA_REPO = os.getenv("GITEA_REPO") or "gladlabs/glad-labs-codebase"

# Memory directories: (path, origin label)
#
# Claude Code maintains a separate `memory/` directory per project scope, keyed
# on the current working directory (e.g. `C--users-mattm`, `C--WINDOWS-system32`).
# The auto-embed job must scan ALL of them — hardcoding a single scope caused
# the 2026-04-18 incident where sessions 49-59 written from a system32 cwd were
# silently skipped because the embedder only knew about `C--users-mattm`.
#
# CLAUDE_PROJECTS_DIR and OPENCLAW_MEMORY_DIR env vars override the host-level
# Path.home() defaults so the containerized auto-embed can bind-mount these
# directories into a fixed in-container location (see docker-compose.local.yml).
_CLAUDE_PROJECTS = Path(
    os.getenv("CLAUDE_PROJECTS_DIR")
    or (Path.home() / ".claude" / "projects")
)
_CLAUDE_SCOPE_DIRS: List[Tuple[Path, str]] = []
if _CLAUDE_PROJECTS.is_dir():
    for _scope in sorted(_CLAUDE_PROJECTS.glob("C--*")):
        _mem = _scope / "memory"
        if _mem.is_dir():
            _CLAUDE_SCOPE_DIRS.append((_mem, "claude-code"))

_OPENCLAW_MEMORY = Path(
    os.getenv("OPENCLAW_MEMORY_DIR")
    or (Path.home() / ".openclaw" / "workspace" / "memory")
)

MEMORY_DIRS: List[Tuple[Path, str]] = [
    *_CLAUDE_SCOPE_DIRS,
    (Path.home() / "glad-labs-website" / ".shared-context", "shared-context"),
    (_OPENCLAW_MEMORY, "openclaw"),
]

# Logging
LOG_DIR = Path.home() / ".gladlabs"
LOG_FILE = LOG_DIR / "auto-embed.log"

# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------

LOG_DIR.mkdir(parents=True, exist_ok=True)

logger = logging.getLogger("auto-embed")
logger.setLevel(logging.INFO)

file_handler = logging.FileHandler(LOG_FILE, encoding="utf-8")
file_handler.setFormatter(logging.Formatter(
    "%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
))
logger.addHandler(file_handler)

# Also log to stdout when run interactively
if sys.stdout and sys.stdout.isatty():
    stdout_handler = logging.StreamHandler(sys.stdout)
    stdout_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    logger.addHandler(stdout_handler)

# ---------------------------------------------------------------------------
# Helpers (shared with embed-memory.py logic)
# ---------------------------------------------------------------------------


def content_hash(text: str) -> str:
    """SHA-256 hash of content for deduplication."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def classify_file(filename: str) -> str:
    """Infer a type tag from the filename."""
    name = filename.lower().replace(".md", "")
    if "handoff" in name or "session" in name:
        return "handoff"
    if "feedback" in name or "preference" in name:
        return "feedback"
    if "decision" in name:
        return "decision"
    if "identity" in name or "profile" in name or "career" in name or "voice" in name:
        return "identity"
    if "project" in name or "strategy" in name or "vision" in name:
        return "project"
    if "audit" in name or "report" in name:
        return "audit"
    if "state" in name or "current" in name:
        return "state"
    if "issue" in name:
        return "issues"
    if "memory" in name or "shared" in name:
        return "index"
    return "knowledge"


def make_relative_id(filepath: Path, origin: str) -> str:
    """Build a stable source_id from origin + relative path."""
    for dir_path, dir_origin in MEMORY_DIRS:
        if dir_origin == origin and str(filepath).startswith(str(dir_path)):
            rel = filepath.relative_to(dir_path)
            return f"{origin}/{rel.as_posix()}"
    return f"{origin}/{filepath.name}"


def collect_files() -> List[Tuple[Path, str]]:
    """Collect all .md files from all memory directories."""
    files = []
    for dir_path, origin in MEMORY_DIRS:
        if not dir_path.exists():
            logger.debug(f"  [skip] Directory not found: {dir_path}")
            continue
        md_files = sorted(dir_path.rglob("*.md"))
        logger.debug(f"  [{origin}] Found {len(md_files)} .md files in {dir_path}")
        for f in md_files:
            files.append((f, origin))
    return files


# ---------------------------------------------------------------------------
# Ollama
# ---------------------------------------------------------------------------


async def check_ollama(client: httpx.AsyncClient) -> bool:
    """Return True if Ollama is reachable and model is available."""
    try:
        resp = await client.get(f"{OLLAMA_URL}/api/tags", timeout=5.0)
        resp.raise_for_status()
        models = [m.get("name", "") for m in resp.json().get("models", [])]
        # Model names may include ":latest" suffix
        found = any(EMBED_MODEL in m for m in models)
        if not found:
            logger.warning(f"Ollama is up but model '{EMBED_MODEL}' not found. Available: {models}")
        return found
    except Exception as e:
        logger.warning(f"Ollama not reachable: {e}")
        return False


async def embed_text(client: httpx.AsyncClient, text: str) -> List[float]:
    """Call Ollama embed API for a single text, truncating if needed."""
    if len(text) > MAX_CHARS:
        text = text[:MAX_CHARS]
    resp = await client.post(
        f"{OLLAMA_URL}/api/embed",
        json={"model": EMBED_MODEL, "input": text},
        timeout=60.0,
    )
    resp.raise_for_status()
    data = resp.json()
    embeddings = data.get("embeddings", [])
    if not embeddings:
        raise RuntimeError("No embeddings returned from Ollama")
    return embeddings[0]


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------


async def needs_reembedding(
    conn: asyncpg.Connection, source_table: str, source_id: str, new_hash: str
) -> bool:
    """Check if content hash has changed (or row doesn't exist)."""
    row = await conn.fetchrow(
        """SELECT content_hash FROM embeddings
           WHERE source_table = $1 AND source_id = $2
             AND chunk_index = 0 AND embedding_model = $3""",
        source_table,
        source_id,
        EMBED_MODEL,
    )
    if row is None:
        return True
    return row["content_hash"] != new_hash


async def store_embedding(
    conn: asyncpg.Connection,
    source_table: str,
    source_id: str,
    chash: str,
    text_preview: str,
    embedding: List[float],
    metadata: Dict[str, Any],
    writer: str = "worker",
) -> None:
    """Upsert embedding row.

    2026-04-12: Added the `writer` column from migration 024. The three
    phases that still use this helper (posts, issues, audit) are all
    produced by the worker — the default `writer='worker'` is correct
    for every current caller. Slice 3b follow-up will migrate those
    phases to MemoryClient and this helper can then be removed.
    """
    now = datetime.now(timezone.utc)
    vector_str = "[" + ",".join(str(v) for v in embedding) + "]"
    metadata_json = json.dumps(metadata)

    # Map the phase-level source_table to a more specific writer so the
    # /memory dashboard can distinguish audit sync events from Gitea
    # issue sync events without having to join source_table.
    if source_table == "issues":
        writer = "gitea"

    await conn.execute(
        """
        INSERT INTO embeddings (source_table, source_id, chunk_index, content_hash,
                                text_preview, embedding_model, embedding, metadata,
                                writer, origin_path,
                                created_at, updated_at)
        VALUES ($1, $2, 0, $3, $4, $5, $6::vector, $7::jsonb, $8, $9, $10, $10)
        ON CONFLICT (source_table, source_id, chunk_index, embedding_model)
        DO UPDATE SET content_hash  = EXCLUDED.content_hash,
                      text_preview  = EXCLUDED.text_preview,
                      embedding     = EXCLUDED.embedding,
                      metadata      = EXCLUDED.metadata,
                      writer        = EXCLUDED.writer,
                      updated_at    = EXCLUDED.updated_at
        """,
        source_table,
        source_id,
        chash,
        text_preview[:500],
        EMBED_MODEL,
        vector_str,
        metadata_json,
        writer,
        source_id,  # origin_path defaults to source_id for legacy phases
        now,
    )


# ---------------------------------------------------------------------------
# Phase 1b: OpenClaw SQLite chunks
# ---------------------------------------------------------------------------
#
# OpenClaw maintains its own memory store at ~/.openclaw/memory/main.sqlite.
# When we discovered on 2026-04-11 that the `~/.openclaw/memory/*.md` path
# was only picking up one stale 2026-03-22.md file, the real story was:
# OpenClaw moved to a SQLite-backed store that pre-chunks, embeds, and tracks
# files internally. The 10 source markdown files still exist as rows in the
# sqlite `files` table along with 80 pre-computed nomic-embed-text vectors
# in the `chunks` table — we just never read it.
#
# This phase ingests those chunks straight into pgvector without re-embedding.
# Embeddings are bit-for-bit identical (same nomic-embed-text model,
# 768 dim, same tokenizer) so the cosine math lines up with everything else
# already in the table. Each chunk becomes one row:
#   source_table = 'memory'
#   source_id    = 'openclaw/<path>'
#   chunk_index  = 0,1,2,... per file
#   metadata     = {"origin": "openclaw-sqlite", "start_line", "end_line"}
#
# If OpenClaw is not installed / the sqlite file is missing, this phase
# is a no-op — do not fail the overall run.

OPENCLAW_SQLITE_PATH = Path.home() / ".openclaw" / "memory" / "main.sqlite"


async def sync_openclaw_sqlite(local_conn: asyncpg.Connection) -> Dict[str, int]:
    """Ingest pre-embedded chunks from OpenClaw's SQLite memory store.

    2026-04-12: Migrated to `MemoryClient.store(embedding=...)` as part of
    Gitea #192 slice 3b. The pre-computed vectors from OpenClaw are passed
    through explicitly — no Ollama round-trip — while the upsert logic and
    writer column flow through the same library every other caller uses.
    """
    stats = {"embedded": 0, "skipped": 0, "failed": 0}

    if not _HAS_MEMORY_CLIENT or MemoryClient is None:
        logger.error("sync_openclaw_sqlite: MemoryClient unavailable — skipping phase")
        stats["failed"] += 1
        return stats

    if not OPENCLAW_SQLITE_PATH.exists():
        logger.info(
            f"OpenClaw SQLite not found at {OPENCLAW_SQLITE_PATH} — skipping phase"
        )
        return stats

    try:
        import sqlite3
    except ImportError:
        logger.warning("sqlite3 not available — skipping OpenClaw phase")
        return stats

    try:
        conn = sqlite3.connect(str(OPENCLAW_SQLITE_PATH))
        cur = conn.cursor()
        cur.execute(
            """
            SELECT id, path, source, start_line, end_line, model, text, embedding
            FROM chunks
            WHERE embedding IS NOT NULL
            ORDER BY path, start_line
            """
        )
        rows = cur.fetchall()
        conn.close()
    except Exception as e:
        logger.error(f"Could not read OpenClaw SQLite: {e}")
        stats["failed"] += 1
        return stats

    logger.info(f"OpenClaw: {len(rows)} pre-embedded chunks in main.sqlite")

    # Group chunks per file so we can assign stable chunk_index values.
    chunks_by_path: Dict[str, list] = {}
    for row in rows:
        chunks_by_path.setdefault(row[1], []).append(row)

    async with MemoryClient(dsn=LOCAL_DSN, ollama_url=OLLAMA_URL) as mem:
        for path, chunks in chunks_by_path.items():
            source_id = f"openclaw/{path}"
            for chunk_index, (
                chunk_id,
                _path,
                _source,
                start_line,
                end_line,
                model,
                text,
                emb_str,
            ) in enumerate(chunks):
                try:
                    # OpenClaw stores embeddings as JSON-encoded float arrays.
                    vec = json.loads(emb_str)
                    if not isinstance(vec, list) or len(vec) != 768:
                        logger.warning(
                            f"  [openclaw] {source_id}#{chunk_index}: unexpected "
                            f"embedding shape (got {type(vec).__name__} len={len(vec) if isinstance(vec, list) else '?'})"
                        )
                        stats["failed"] += 1
                        continue

                    # `model` from OpenClaw is "nomic-embed-text:latest" —
                    # strip the :latest tag so queries by embedding_model
                    # align with ours.
                    embed_model = (model or EMBED_MODEL).replace(":latest", "")

                    # Skip if already in pgvector with the same hash. The
                    # hash IS the OpenClaw chunk id (SHA-256 of the text).
                    existing = await local_conn.fetchrow(
                        """SELECT content_hash FROM embeddings
                           WHERE source_table = $1 AND source_id = $2
                             AND chunk_index = $3 AND embedding_model = $4""",
                        MEMORY_SOURCE,
                        source_id,
                        chunk_index,
                        embed_model,
                    )
                    if existing is not None and existing["content_hash"] == chunk_id:
                        stats["skipped"] += 1
                        continue

                    await mem.store(
                        text=text or "",
                        writer="openclaw",
                        source_id=source_id,
                        source_table=MEMORY_SOURCE,
                        chunk_index=chunk_index,
                        embedding=vec,
                        content_hash=chunk_id,
                        embedding_model=embed_model,
                        metadata={
                            "origin": "openclaw-sqlite",
                            "openclaw_source": _source,
                            "start_line": start_line,
                            "end_line": end_line,
                            "filename": Path(path).name,
                        },
                        origin_path=path,
                    )
                    stats["embedded"] += 1

                except Exception as e:
                    stats["failed"] += 1
                    logger.error(
                        f"  [openclaw] FAIL {source_id}#{chunk_index}: {e}"
                    )

    return stats


# ---------------------------------------------------------------------------
# Phase 1: Memory files (same logic as embed-memory.py)
# ---------------------------------------------------------------------------


async def sync_memory_files(
    local_conn: asyncpg.Connection, http: httpx.AsyncClient
) -> Dict[str, int]:
    """Scan memory dirs, embed new/changed files via `poindexter.memory.MemoryClient`.

    2026-04-12: Migrated from direct `store_embedding` / `needs_reembedding`
    calls to MemoryClient as part of Gitea #192 slice 3b. This means the
    `writer` column gets set correctly for every memory file (the `origin`
    label from MEMORY_DIRS becomes the writer value), content_hash dedup
    uses the same SHA-256 the rest of the pipeline does, and any future
    embeddings schema change only has to be made in MemoryClient.

    Falls back to the legacy path if MemoryClient fails to import — lets
    `auto-embed.py` keep working from oddball environments where the
    poindexter package isn't reachable.
    """
    del http  # legacy param kept for signature compat; MemoryClient owns HTTP
    stats = {"embedded": 0, "skipped": 0, "failed": 0}

    if not _HAS_MEMORY_CLIENT or MemoryClient is None:
        logger.error(
            "sync_memory_files: MemoryClient unavailable (%s). This should not happen "
            "inside the worker container or from the repo root — fix the import path "
            "in auto-embed.py.",
            _MEMORY_CLIENT_IMPORT_ERR,
        )
        stats["failed"] += 1
        return stats

    files = collect_files()
    logger.info(
        f"Memory: {len(files)} .md files found across {len(MEMORY_DIRS)} directories"
    )

    # One MemoryClient for the whole batch — it pools the DB connection and
    # the Ollama HTTP client, so per-file overhead is negligible. We pass
    # LOCAL_DSN / OLLAMA_URL explicitly so this script works from any
    # environment (the hardcoded fallbacks in MemoryClient default to
    # host.docker.internal / DATABASE_URL which may not be set here).
    async with MemoryClient(dsn=LOCAL_DSN, ollama_url=OLLAMA_URL) as mem:
        for filepath, origin in files:
            source_id = make_relative_id(filepath, origin)
            try:
                text = filepath.read_text(encoding="utf-8")
                if not text.strip():
                    stats["skipped"] += 1
                    continue

                # Use the DB-side content_hash dedup via store() — we pass
                # the hash explicitly so the stored row matches the text
                # we just read, and let MemoryClient's internal skip-logic
                # decide whether to re-embed. For minimal churn we still
                # compute the hash here and compare against the existing
                # row before calling store(), matching the old behavior.
                chash = content_hash(text)
                existing = await local_conn.fetchrow(
                    """
                    SELECT content_hash FROM embeddings
                    WHERE source_table = $1 AND source_id = $2
                      AND chunk_index = 0 AND embedding_model = $3
                    """,
                    MEMORY_SOURCE,
                    source_id,
                    EMBED_MODEL,
                )
                if existing and existing["content_hash"] == chash:
                    stats["skipped"] += 1
                    continue

                metadata = {
                    "filename": filepath.name,
                    "type": classify_file(filepath.name),
                    "chars": len(text),
                }
                await mem.store(
                    text=text,
                    writer=origin,
                    source_id=source_id,
                    source_table=MEMORY_SOURCE,
                    metadata=metadata,
                    content_hash=chash,
                    origin_path=str(filepath),
                )
                stats["embedded"] += 1
                logger.info(f"  [memory] Embedded: {source_id} ({len(text)} chars)")

            except Exception as e:
                stats["failed"] += 1
                logger.error(f"  [memory] FAIL {source_id}: {e}")

    return stats


# ---------------------------------------------------------------------------
# Phase 2: Published posts from cloud DB
# ---------------------------------------------------------------------------


async def fetch_published_posts(cloud_conn: asyncpg.Connection) -> List[asyncpg.Record]:
    """Fetch all published posts from the Railway cloud DB."""
    return await cloud_conn.fetch(
        """SELECT id, title, slug, content, excerpt, status, published_at, created_at, updated_at
           FROM posts
           WHERE status = 'published'
           ORDER BY published_at DESC NULLS LAST"""
    )


async def sync_published_posts(
    local_conn: asyncpg.Connection,
    cloud_conn: asyncpg.Connection,
    http: httpx.AsyncClient,
) -> Dict[str, int]:
    """Embed any published posts not yet in local pgvector. Returns stats dict."""
    stats = {"embedded": 0, "skipped": 0, "failed": 0}

    posts = await fetch_published_posts(cloud_conn)
    logger.info(f"Posts: {len(posts)} published posts found in cloud DB")

    for post in posts:
        post_id = str(post["id"])
        # #198 audit: unified on UUID source_id so auto-embed and the
        # publish-hook embedder (services/embedding_service.embed_post)
        # share one namespace. Historical `post/slug-hash` rows from
        # before 2026-04-16 get cleaned up by the backfill migration.
        source_id = post_id
        try:
            # Build the embeddable text: title + excerpt + content
            parts = []
            if post["title"]:
                parts.append(f"# {post['title']}")
            if post["excerpt"]:
                parts.append(post["excerpt"])
            if post["content"]:
                parts.append(post["content"])
            text = "\n\n".join(parts)

            if not text.strip():
                stats["skipped"] += 1
                continue

            chash = content_hash(text)
            if not await needs_reembedding(local_conn, POSTS_SOURCE, source_id, chash):
                stats["skipped"] += 1
                continue

            embedding = await embed_text(http, text)
            metadata = {
                "post_id": post_id,
                "slug": post["slug"],
                "title": post["title"],
                "status": post["status"],
                "published_at": post["published_at"].isoformat() if post["published_at"] else None,
                "chars": len(text),
            }
            preview = text[:500].replace("\n", " ").strip()
            await store_embedding(local_conn, POSTS_SOURCE, source_id, chash, preview, embedding, metadata)
            stats["embedded"] += 1
            logger.info(f"  [post] Embedded: {source_id} ({len(text)} chars)")

        except Exception as e:
            stats["failed"] += 1
            logger.error(f"  [post] FAIL {source_id}: {e}")

    return stats


# ---------------------------------------------------------------------------
# Phase 3: Gitea issues
# ---------------------------------------------------------------------------


async def sync_gitea_issues(
    local_conn: asyncpg.Connection, http: httpx.AsyncClient
) -> Dict[str, int]:
    """Embed new/changed Gitea issues."""
    stats = {"embedded": 0, "skipped": 0, "failed": 0}
    page = 1
    all_issues = []

    while True:
        try:
            resp = await http.get(
                f"{GITEA_URL}/api/v1/repos/{GITEA_REPO}/issues",
                params={"state": "all", "limit": 50, "page": page},
                auth=(GITEA_USER, GITEA_PASS),
                timeout=15,
            )
            if resp.status_code != 200:
                logger.warning(f"Gitea API returned {resp.status_code}")
                break
            batch = resp.json()
            if not batch:
                break
            all_issues.extend(batch)
            page += 1
            if len(batch) < 50:
                break
        except Exception as e:
            logger.warning(f"Gitea API error: {e}")
            break

    logger.info(f"  Found {len(all_issues)} Gitea issues")

    for issue in all_issues:
        try:
            num = issue["number"]
            title = issue.get("title", "")
            body = (issue.get("body") or "")[:3000]
            state = issue.get("state", "")
            labels = ", ".join(l["name"] for l in issue.get("labels", []))

            text = f"Issue #{num}: {title}\nState: {state}\nLabels: {labels}\n\n{body}"
            content_hash = hashlib.sha256(text.encode()).hexdigest()
            source_id = str(num)

            existing = await local_conn.fetchval(
                "SELECT content_hash FROM embeddings WHERE source_table = $1 AND source_id = $2 AND embedding_model = $3",
                ISSUES_SOURCE, source_id, EMBED_MODEL,
            )
            if existing == content_hash:
                stats["skipped"] += 1
                continue

            emb = await embed_text(http, text[:MAX_CHARS])

            await local_conn.execute(
                """INSERT INTO embeddings (source_table, source_id, content_hash, chunk_index,
                   text_preview, embedding_model, embedding, metadata)
                   VALUES ($1, $2, $3, 0, $4, $5, $6::vector, $7::jsonb)
                   ON CONFLICT (source_table, source_id, chunk_index, embedding_model)
                   DO UPDATE SET content_hash = $3, embedding = $6::vector,
                   text_preview = $4, metadata = $7::jsonb, updated_at = NOW()""",
                ISSUES_SOURCE, source_id, content_hash, text[:500], EMBED_MODEL,
                json.dumps(emb), json.dumps({"title": title, "state": state, "labels": labels}),
            )
            stats["embedded"] += 1
        except Exception as e:
            stats["failed"] += 1
            logger.error(f"  [issue] FAIL #{issue.get('number', '?')}: {e}")

    return stats


# ---------------------------------------------------------------------------
# Phase 4: Audit log entries (significant events only)
# ---------------------------------------------------------------------------


async def sync_audit_entries(
    local_conn: asyncpg.Connection, http: httpx.AsyncClient
) -> Dict[str, int]:
    """Embed recent audit log entries (errors + significant events)."""
    stats = {"embedded": 0, "skipped": 0, "failed": 0}

    rows = await local_conn.fetch(
        """SELECT id, event_type, source, task_id, details, severity, timestamp
           FROM audit_log
           WHERE severity IN ('error', 'warning')
           ORDER BY timestamp DESC LIMIT 200"""
    )
    logger.info(f"  Found {len(rows)} audit entries to check")

    for row in rows:
        try:
            audit_id = str(row["id"])
            details = json.loads(row["details"]) if row["details"] else {}
            text = (
                f"Audit: {row['event_type']} [{row['severity']}]\n"
                f"Source: {row['source']}\n"
                f"Task: {row['task_id'] or 'N/A'}\n"
                f"Time: {row['timestamp']}\n"
                f"Details: {json.dumps(details)[:2000]}"
            )
            content_hash = hashlib.sha256(text.encode()).hexdigest()

            existing = await local_conn.fetchval(
                "SELECT content_hash FROM embeddings WHERE source_table = $1 AND source_id = $2 AND embedding_model = $3",
                AUDIT_SOURCE, audit_id, EMBED_MODEL,
            )
            if existing == content_hash:
                stats["skipped"] += 1
                continue

            emb = await embed_text(http, text[:MAX_CHARS])

            # 2026-04-16 (#198 audit): include `writer` so audit rows
            # are attributed to 'auto-embed' in the /memory dashboard
            # instead of appearing with writer=NULL.
            await local_conn.execute(
                """INSERT INTO embeddings (source_table, source_id, content_hash, chunk_index,
                   text_preview, embedding_model, embedding, metadata, writer)
                   VALUES ($1, $2, $3, 0, $4, $5, $6::vector, $7::jsonb, $8)
                   ON CONFLICT (source_table, source_id, chunk_index, embedding_model)
                   DO UPDATE SET content_hash = $3, embedding = $6::vector,
                   text_preview = $4, metadata = $7::jsonb,
                   writer = COALESCE(EXCLUDED.writer, embeddings.writer),
                   updated_at = NOW()""",
                AUDIT_SOURCE, audit_id, content_hash, text[:500], EMBED_MODEL,
                json.dumps(emb), json.dumps({
                    "event_type": row["event_type"],
                    "severity": row["severity"],
                    "source": row["source"],
                }),
                "auto-embed",
            )
            stats["embedded"] += 1
        except Exception as e:
            stats["failed"] += 1
            logger.error(f"  [audit] FAIL #{row.get('id', '?')}: {e}")

    return stats


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Phase 5: Brain knowledge + decisions (defined here so main() can call it)
# ---------------------------------------------------------------------------

BRAIN_SOURCE = "brain"


async def sync_brain_tables(
    local_conn: asyncpg.Connection,
    http: httpx.AsyncClient,
) -> Dict[str, int]:
    """Embed brain_knowledge and brain_decisions into pgvector.

    This makes brain-daemon's decisions and knowledge searchable by all
    agents (Claude Code, OpenClaw, MCP tools).  Writer tag: 'brain-daemon'.
    """
    stats = {"embedded": 0, "skipped": 0, "failed": 0}

    # --- brain_knowledge: entity/attribute/value facts ---
    try:
        rows = await local_conn.fetch(
            "SELECT id, entity, attribute, value, source, confidence, updated_at "
            "FROM brain_knowledge ORDER BY updated_at DESC LIMIT 500"
        )
        logger.info(f"Brain knowledge: {len(rows)} facts found")
        for row in rows:
            source_id = f"brain_knowledge/{row['id']}"
            text = f"{row['entity']}: {row['attribute']} = {row['value']}"
            if row["source"]:
                text += f" (source: {row['source']})"
            if not text.strip() or len(text) < 10:
                stats["skipped"] += 1
                continue
            chash = content_hash(text)
            if not await needs_reembedding(local_conn, BRAIN_SOURCE, source_id, chash):
                stats["skipped"] += 1
                continue
            try:
                embedding = await embed_text(http, text)
                metadata = {
                    "entity": row["entity"],
                    "attribute": row["attribute"],
                    "source": row["source"],
                    "confidence": float(row["confidence"]) if row["confidence"] else None,
                }
                preview = text[:500].replace("\n", " ").strip()
                await store_embedding(
                    local_conn, BRAIN_SOURCE, source_id, chash,
                    preview, embedding, metadata, writer="brain-daemon",
                )
                stats["embedded"] += 1
            except Exception as e:
                logger.debug(f"Failed to embed brain_knowledge {row['id']}: {e}")
                stats["failed"] += 1
    except Exception as e:
        logger.error(f"brain_knowledge fetch failed: {e}")

    # --- brain_decisions: decision audit trail ---
    try:
        rows = await local_conn.fetch(
            "SELECT id, decision, reasoning, context, confidence, created_at "
            "FROM brain_decisions ORDER BY created_at DESC LIMIT 200"
        )
        logger.info(f"Brain decisions: {len(rows)} decisions found")
        for row in rows:
            source_id = f"brain_decisions/{row['id']}"
            parts = [f"Decision: {row['decision']}"]
            if row["reasoning"]:
                parts.append(f"Reasoning: {row['reasoning']}")
            if row["context"]:
                ctx = row["context"]
                if isinstance(ctx, str):
                    parts.append(f"Context: {ctx[:300]}")
                elif isinstance(ctx, dict):
                    parts.append(f"Context: {json.dumps(ctx)[:300]}")
            text = "\n".join(parts)
            if len(text) < 20:
                stats["skipped"] += 1
                continue
            chash = content_hash(text)
            if not await needs_reembedding(local_conn, BRAIN_SOURCE, source_id, chash):
                stats["skipped"] += 1
                continue
            try:
                embedding = await embed_text(http, text)
                metadata = {
                    "decision": row["decision"][:200],
                    "confidence": float(row["confidence"]) if row["confidence"] else None,
                    "created_at": row["created_at"].isoformat() if row["created_at"] else None,
                }
                preview = text[:500].replace("\n", " ").strip()
                await store_embedding(
                    local_conn, BRAIN_SOURCE, source_id, chash,
                    preview, embedding, metadata, writer="brain-daemon",
                )
                stats["embedded"] += 1
            except Exception as e:
                logger.debug(f"Failed to embed brain_decisions {row['id']}: {e}")
                stats["failed"] += 1
    except Exception as e:
        logger.error(f"brain_decisions fetch failed: {e}")

    return stats


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


async def main() -> None:
    """Run all phases. Restored 2026-04-16 after regression in 87adc204
    (#198 data-flow audit) — the previous main() silently returned
    right after the Ollama check, so every cron run was a no-op.
    """
    start = datetime.now(timezone.utc)
    logger.info("=" * 60)
    logger.info("Auto-Embed run started")
    logger.info("=" * 60)

    http = httpx.AsyncClient()

    # Pre-flight: check Ollama
    if not await check_ollama(http):
        logger.warning("Ollama is down or model unavailable — skipping this run.")
        await http.aclose()
        return

    local_conn: Optional[asyncpg.Connection] = None
    cloud_conn: Optional[asyncpg.Connection] = None

    try:
        # Connect to local pgvector
        logger.info("Connecting to local pgvector DB...")
        local_conn = await asyncpg.connect(LOCAL_DSN)

        # Load Gitea creds from DB, env takes precedence per-key. Needed because
        # the DB value of gitea_url is `http://localhost:3001` (correct for host
        # runs) but the container has to reach Gitea via `host.docker.internal`.
        global GITEA_URL, GITEA_USER, GITEA_PASS, GITEA_REPO
        try:
            _env_gitea_url = os.getenv("GITEA_URL")
            _env_gitea_user = os.getenv("GITEA_USER")
            _env_gitea_pass = os.getenv("GITEA_PASSWORD") or os.getenv("GITEA_PASS")
            _env_gitea_repo = os.getenv("GITEA_REPO")
            for key in ("gitea_url", "gitea_user", "gitea_password", "gitea_repo"):
                val = await local_conn.fetchval(
                    "SELECT value FROM app_settings WHERE key = $1", key
                )
                if not val:
                    continue
                if key == "gitea_url" and not _env_gitea_url:
                    GITEA_URL = localize_url(val)
                elif key == "gitea_user" and not _env_gitea_user:
                    GITEA_USER = val
                elif key == "gitea_password" and not _env_gitea_pass:
                    GITEA_PASS = val
                elif key == "gitea_repo" and not _env_gitea_repo:
                    GITEA_REPO = val
            if GITEA_PASS:
                logger.info("Loaded Gitea credentials from app_settings")
        except Exception as e:
            logger.debug(f"Could not load Gitea creds from DB: {e}")

        # Phase 1: memory files
        logger.info("--- Phase 1: Memory files ---")
        mem_stats = await sync_memory_files(local_conn, http)

        # Phase 1b: OpenClaw pre-embedded chunks from its SQLite store
        logger.info("--- Phase 1b: OpenClaw SQLite chunks ---")
        try:
            openclaw_stats = await sync_openclaw_sqlite(local_conn)
        except Exception as e:
            logger.error(f"OpenClaw SQLite sync failed: {e}")
            openclaw_stats = {"embedded": 0, "skipped": 0, "failed": 1}

        # Phase 2: published posts
        logger.info("--- Phase 2: Published posts ---")
        try:
            cloud_conn = await asyncpg.connect(CLOUD_DSN, timeout=15)
            post_stats = await sync_published_posts(local_conn, cloud_conn, http)
        except Exception as e:
            logger.error(f"Cloud DB connection failed: {e}")
            post_stats = {"embedded": 0, "skipped": 0, "failed": 0}

        # Phase 3: Gitea issues
        logger.info("--- Phase 3: Gitea issues ---")
        try:
            issue_stats = await sync_gitea_issues(local_conn, http)
        except Exception as e:
            logger.error(f"Gitea issues sync failed: {e}")
            issue_stats = {"embedded": 0, "skipped": 0, "failed": 0}

        # Phase 4: Audit log entries
        logger.info("--- Phase 4: Audit log ---")
        try:
            audit_stats = await sync_audit_entries(local_conn, http)
        except Exception as e:
            logger.error(f"Audit log sync failed: {e}")
            audit_stats = {"embedded": 0, "skipped": 0, "failed": 0}

        # Phase 5: Brain knowledge + decisions → pgvector
        logger.info("--- Phase 5: Brain knowledge & decisions ---")
        try:
            brain_stats = await sync_brain_tables(local_conn, http)
        except Exception as e:
            logger.error(f"Brain tables sync failed: {e}")
            brain_stats = {"embedded": 0, "skipped": 0, "failed": 0}

        # Summary
        total_embedded = (
            mem_stats["embedded"]
            + openclaw_stats["embedded"]
            + post_stats["embedded"]
            + issue_stats["embedded"]
            + audit_stats["embedded"]
            + brain_stats["embedded"]
        )
        elapsed = (datetime.now(timezone.utc) - start).total_seconds()
        logger.info("--- Summary ---")
        logger.info(f"  Memory:   {mem_stats['embedded']} embedded, {mem_stats['skipped']} skipped, {mem_stats['failed']} failed")
        logger.info(f"  OpenClaw: {openclaw_stats['embedded']} embedded, {openclaw_stats['skipped']} skipped, {openclaw_stats['failed']} failed")
        logger.info(f"  Posts:    {post_stats['embedded']} embedded, {post_stats['skipped']} skipped, {post_stats['failed']} failed")
        logger.info(f"  Issues:   {issue_stats['embedded']} embedded, {issue_stats['skipped']} skipped, {issue_stats['failed']} failed")
        logger.info(f"  Audit:    {audit_stats['embedded']} embedded, {audit_stats['skipped']} skipped, {audit_stats['failed']} failed")
        logger.info(f"  Brain:    {brain_stats['embedded']} embedded, {brain_stats['skipped']} skipped, {brain_stats['failed']} failed")
        logger.info(f"  Total embedded: {total_embedded}")
        logger.info(f"  Elapsed: {elapsed:.1f}s")
        logger.info("Auto-Embed run complete.\n")

    except Exception as e:
        logger.error(f"Fatal error: {e}")
    finally:
        if local_conn:
            await local_conn.close()
        if cloud_conn:
            await cloud_conn.close()
        await http.aclose()


if __name__ == "__main__":
    asyncio.run(main())
