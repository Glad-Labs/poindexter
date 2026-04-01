"""
Embed Memory Files into pgvector

Standalone script that scans Claude Code memory, shared context, and OpenClaw
memory directories for .md files, embeds them with nomic-embed-text via Ollama,
and stores them in pgvector with SHA-256 deduplication.

Usage:
    python scripts/embed-memory.py
"""

import asyncio
import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List, Tuple
from datetime import datetime, timezone

import asyncpg
import httpx

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DB_DSN = "postgresql://gladlabs:gladlabs-brain-local@localhost:5433/gladlabs_brain"
OLLAMA_URL = "http://localhost:11434"
EMBED_MODEL = "nomic-embed-text"
SOURCE_TABLE = "memory"

# Memory directories: (path, origin label)
MEMORY_DIRS: List[Tuple[Path, str]] = [
    (Path.home() / ".claude" / "projects" / "C--users-mattm-glad-labs-website" / "memory", "claude-code"),
    (Path.home() / "glad-labs-website" / ".shared-context", "shared-context"),
    (Path.home() / ".openclaw" / "workspace" / "memory", "openclaw"),
]

# ---------------------------------------------------------------------------
# Helpers
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
            print(f"  [skip] Directory not found: {dir_path}")
            continue
        md_files = sorted(dir_path.rglob("*.md"))
        print(f"  [{origin}] Found {len(md_files)} .md files in {dir_path}")
        for f in md_files:
            files.append((f, origin))
    return files


MAX_CHARS = 6000  # nomic-embed-text has 8192 token context; ~6k chars stays safe


async def embed_text(client: httpx.AsyncClient, text: str) -> List[float]:
    """Call Ollama embed API for a single text, truncating if needed."""
    # Truncate to fit model context window
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


async def needs_reembedding(
    conn: asyncpg.Connection, source_id: str, new_hash: str
) -> bool:
    """Check if content hash has changed (or row doesn't exist)."""
    row = await conn.fetchrow(
        """SELECT content_hash FROM embeddings
           WHERE source_table = $1 AND source_id = $2
             AND chunk_index = 0 AND embedding_model = $3""",
        SOURCE_TABLE,
        source_id,
        EMBED_MODEL,
    )
    if row is None:
        return True
    return row["content_hash"] != new_hash


async def store_embedding(
    conn: asyncpg.Connection,
    source_id: str,
    chash: str,
    text_preview: str,
    embedding: List[float],
    metadata: Dict[str, Any],
) -> None:
    """Upsert embedding row using the actual table schema."""
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
        SOURCE_TABLE,
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
# Main
# ---------------------------------------------------------------------------


async def main() -> None:
    print("=== Memory Embedding Script ===\n")
    print("Scanning memory directories...")
    files = collect_files()
    total = len(files)
    print(f"\nTotal files to process: {total}\n")

    if total == 0:
        print("Nothing to embed.")
        return

    conn = await asyncpg.connect(DB_DSN)
    http = httpx.AsyncClient()

    embedded = 0
    skipped = 0
    failed = 0

    try:
        for filepath, origin in files:
            source_id = make_relative_id(filepath, origin)
            try:
                text = filepath.read_text(encoding="utf-8")
                if not text.strip():
                    print(f"  [skip] Empty file: {source_id}")
                    skipped += 1
                    continue

                chash = content_hash(text)

                if not await needs_reembedding(conn, source_id, chash):
                    print(f"  [skip] Unchanged: {source_id}")
                    skipped += 1
                    continue

                embedding = await embed_text(http, text)

                metadata = {
                    "origin": origin,
                    "filename": filepath.name,
                    "type": classify_file(filepath.name),
                    "chars": len(text),
                }

                # Use first 500 chars as text_preview
                preview = text[:500].replace("\n", " ").strip()
                await store_embedding(conn, source_id, chash, preview, embedding, metadata)
                embedded += 1
                print(f"  [embed] {source_id} ({len(text)} chars, {metadata['type']})")

            except Exception as e:
                failed += 1
                print(f"  [FAIL] {source_id}: {e}")

    finally:
        await conn.close()
        await http.aclose()

    print(f"\n=== Done ===")
    print(f"  Embedded: {embedded}")
    print(f"  Skipped:  {skipped} (unchanged)")
    print(f"  Failed:   {failed}")
    print(f"  Total:    {total}")


if __name__ == "__main__":
    asyncio.run(main())
