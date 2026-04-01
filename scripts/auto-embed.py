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
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import asyncpg
import httpx

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

LOCAL_DSN = "postgresql://gladlabs:gladlabs-brain-local@localhost:5433/gladlabs_brain"
CLOUD_DSN = "postgresql://postgres:***REMOVED***@hopper.proxy.rlwy.net:32382/railway"
OLLAMA_URL = "http://127.0.0.1:11434"
EMBED_MODEL = "nomic-embed-text"
MAX_CHARS = 6000  # nomic-embed-text has 8192 token context; ~6k chars stays safe

# Source tables
MEMORY_SOURCE = "memory"
POSTS_SOURCE = "posts"

# Memory directories: (path, origin label)
MEMORY_DIRS: List[Tuple[Path, str]] = [
    (Path.home() / ".claude" / "projects" / "C--users-mattm-glad-labs-website" / "memory", "claude-code"),
    (Path.home() / "glad-labs-website" / ".shared-context", "shared-context"),
    (Path.home() / ".openclaw" / "workspace" / "memory", "openclaw"),
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
) -> None:
    """Upsert embedding row."""
    now = datetime.now(timezone.utc)
    vector_str = "[" + ",".join(str(v) for v in embedding) + "]"
    metadata_json = json.dumps(metadata)

    await conn.execute(
        """
        INSERT INTO embeddings (source_table, source_id, chunk_index, content_hash,
                                text_preview, embedding_model, embedding, metadata,
                                created_at, updated_at)
        VALUES ($1, $2, 0, $3, $4, $5, $6::vector, $7::jsonb, $8, $9)
        ON CONFLICT (source_table, source_id, chunk_index, embedding_model)
        DO UPDATE SET content_hash  = EXCLUDED.content_hash,
                      text_preview  = EXCLUDED.text_preview,
                      embedding     = EXCLUDED.embedding,
                      metadata      = EXCLUDED.metadata,
                      updated_at    = EXCLUDED.updated_at
        """,
        source_table,
        source_id,
        chash,
        text_preview[:500],
        EMBED_MODEL,
        vector_str,
        metadata_json,
        now,
        now,
    )


# ---------------------------------------------------------------------------
# Phase 1: Memory files (same logic as embed-memory.py)
# ---------------------------------------------------------------------------


async def sync_memory_files(
    local_conn: asyncpg.Connection, http: httpx.AsyncClient
) -> Dict[str, int]:
    """Scan memory dirs, embed new/changed files. Returns stats dict."""
    stats = {"embedded": 0, "skipped": 0, "failed": 0}
    files = collect_files()
    logger.info(f"Memory: {len(files)} .md files found across {len(MEMORY_DIRS)} directories")

    for filepath, origin in files:
        source_id = make_relative_id(filepath, origin)
        try:
            text = filepath.read_text(encoding="utf-8")
            if not text.strip():
                stats["skipped"] += 1
                continue

            chash = content_hash(text)
            if not await needs_reembedding(local_conn, MEMORY_SOURCE, source_id, chash):
                stats["skipped"] += 1
                continue

            embedding = await embed_text(http, text)
            metadata = {
                "origin": origin,
                "filename": filepath.name,
                "type": classify_file(filepath.name),
                "chars": len(text),
            }
            preview = text[:500].replace("\n", " ").strip()
            await store_embedding(local_conn, MEMORY_SOURCE, source_id, chash, preview, embedding, metadata)
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
        source_id = f"post/{post['slug']}"
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
# Main
# ---------------------------------------------------------------------------


async def main() -> None:
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

        # Phase 1: memory files
        logger.info("--- Phase 1: Memory files ---")
        mem_stats = await sync_memory_files(local_conn, http)

        # Phase 2: published posts
        logger.info("--- Phase 2: Published posts ---")
        try:
            cloud_conn = await asyncpg.connect(CLOUD_DSN, timeout=15)
            post_stats = await sync_published_posts(local_conn, cloud_conn, http)
        except Exception as e:
            logger.error(f"Cloud DB connection failed: {e}")
            post_stats = {"embedded": 0, "skipped": 0, "failed": 0}

        # Summary
        elapsed = (datetime.now(timezone.utc) - start).total_seconds()
        logger.info("--- Summary ---")
        logger.info(f"  Memory: {mem_stats['embedded']} embedded, {mem_stats['skipped']} skipped, {mem_stats['failed']} failed")
        logger.info(f"  Posts:  {post_stats['embedded']} embedded, {post_stats['skipped']} skipped, {post_stats['failed']} failed")
        logger.info(f"  Total embedded: {mem_stats['embedded'] + post_stats['embedded']}")
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
