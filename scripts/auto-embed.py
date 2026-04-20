"""Auto-Embed — thin runner over the plugin registry.

Runs as a Docker sidecar (see ``docker-compose.local.yml`` → ``auto-embed``
service) or ad-hoc from the host. Iterates every registered Tap, fetches
Documents, stores embeddings in pgvector. The actual per-source logic
lives under ``services/taps/`` as plugin implementations.

Pre-Phase-B history: this file used to be 1157 lines with 6 hardcoded
phases. Phase B (GitHub #66) collapsed it onto the plugin framework.
See ``docs/architecture/plugin-architecture.md`` for the design.

## Running

    # From the repo root (poetry env):
    poetry run python scripts/auto-embed.py

    # From the Docker sidecar (runs hourly):
    docker compose -f docker-compose.local.yml up -d auto-embed

## Environment

Only ``DATABASE_URL`` + optional ``OLLAMA_URL`` / ``IN_DOCKER``. Every
other knob — enable/disable per Tap, per-Tap config — lives in
``app_settings`` under ``plugin.tap.<name>``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict

import asyncpg
import httpx

# ---------------------------------------------------------------------------
# Make `poindexter` + `plugins` + `services` importable from the worker
# repo layout. Docker image COPYs them into /app.
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parent.parent
for _candidate in (
    _REPO_ROOT / "src" / "cofounder_agent",
    Path("/app"),
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

# Resolve DB URL via bootstrap.toml first, env vars as fallback.
_dsn = os.getenv("LOCAL_DATABASE_URL") or os.getenv("DATABASE_URL", "")
if not _dsn:
    try:
        from pathlib import Path as _Path
        for _p in _Path(__file__).resolve().parents:
            if (_p / "brain" / "bootstrap.py").is_file():
                if str(_p) not in sys.path:
                    sys.path.insert(0, str(_p))
                break
        from brain.bootstrap import resolve_database_url
        _dsn = resolve_database_url() or ""
    except Exception:
        pass
LOCAL_DSN = _dsn or "postgresql://poindexter:poindexter-brain-local@localhost:15432/poindexter_brain"

# URL localization — same pattern used by brain.docker_utils and the
# plugins ecosystem. When IN_DOCKER=true, rewrites localhost URLs to
# host.docker.internal so DB values that are correct from the host
# also work from inside the container.
IN_DOCKER = os.getenv("IN_DOCKER", "").lower() in ("1", "true", "yes") \
    or Path("/.dockerenv").exists()


def _localize_url(url: str) -> str:
    if not url or not IN_DOCKER:
        return url
    return (
        url.replace("://localhost:", "://host.docker.internal:")
           .replace("://127.0.0.1:", "://host.docker.internal:")
    )


OLLAMA_URL = _localize_url(os.getenv("OLLAMA_URL") or "http://127.0.0.1:11434")
EMBED_MODEL = "nomic-embed-text"
OPENCLAW_SQLITE_PATH = Path.home() / ".openclaw" / "memory" / "main.sqlite"

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

LOG_DIR = Path.home() / ".gladlabs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = LOG_DIR / "auto-embed.log"

logger = logging.getLogger("auto-embed")
logger.setLevel(logging.INFO)

_file_handler = logging.FileHandler(LOG_FILE, encoding="utf-8")
_file_handler.setFormatter(logging.Formatter(
    "%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
))
logger.addHandler(_file_handler)

# Always log to stdout so ``docker logs poindexter-auto-embed`` is useful.
# (Previously gated on ``isatty()``, which made the container silent because
# Docker's captured stdout is not a TTY. Still writes to the file handler
# above regardless.)
_stdout = logging.StreamHandler(sys.stdout)
_stdout.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
logger.addHandler(_stdout)


# ---------------------------------------------------------------------------
# Pre-flight checks
# ---------------------------------------------------------------------------


async def check_ollama(client: httpx.AsyncClient) -> bool:
    """Return True if Ollama is reachable and the embed model is present."""
    try:
        resp = await client.get(f"{OLLAMA_URL}/api/tags", timeout=5.0)
        resp.raise_for_status()
        models = [m.get("name", "") for m in resp.json().get("models", [])]
        found = any(EMBED_MODEL in m for m in models)
        if not found:
            logger.warning(
                "Ollama is up but model %r not found. Available: %s",
                EMBED_MODEL, models,
            )
        return found
    except Exception as e:
        logger.warning("Ollama not reachable: %s", e)
        return False


# ---------------------------------------------------------------------------
# Legacy Phase: OpenClaw SQLite pre-embedded chunks
# ---------------------------------------------------------------------------
#
# OpenClaw stores its own pgvector-compatible embeddings in
# ~/.openclaw/memory/main.sqlite. We copy them verbatim (no re-embed,
# no Ollama call) into our embeddings table so OpenClaw's knowledge is
# queryable from this pool.
#
# Not yet migrated to a Tap because the Document contract assumes text
# that gets embedded in the runner — OpenClaw provides pre-computed
# vectors. Migration tracked separately; for now it stays inline.


async def sync_openclaw_sqlite(local_conn: asyncpg.Connection) -> Dict[str, int]:
    """Ingest pre-embedded chunks from OpenClaw's SQLite memory store."""
    stats = {"embedded": 0, "skipped": 0, "failed": 0}

    if not _HAS_MEMORY_CLIENT or MemoryClient is None:
        logger.error("sync_openclaw_sqlite: MemoryClient unavailable — skipping phase")
        stats["failed"] += 1
        return stats

    if not OPENCLAW_SQLITE_PATH.exists():
        logger.info(
            "OpenClaw SQLite not found at %s — skipping phase",
            OPENCLAW_SQLITE_PATH,
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
        logger.error("Could not read OpenClaw SQLite: %s", e)
        stats["failed"] += 1
        return stats

    logger.info("OpenClaw: %d pre-embedded chunks in main.sqlite", len(rows))

    chunks_by_path: Dict[str, list] = {}
    for row in rows:
        chunks_by_path.setdefault(row[1], []).append(row)

    async with MemoryClient(dsn=LOCAL_DSN, ollama_url=OLLAMA_URL) as mem:
        for path, chunks in chunks_by_path.items():
            source_id = f"openclaw/{path}"
            for chunk_index, (_cid, _path, _source, start_line, end_line, model, text, emb_str) in enumerate(chunks):
                try:
                    vec = json.loads(emb_str)
                    if not isinstance(vec, list) or len(vec) != 768:
                        logger.warning(
                            "  [openclaw] %s#%d: unexpected embedding shape",
                            source_id, chunk_index,
                        )
                        stats["failed"] += 1
                        continue
                    embed_model = (model or EMBED_MODEL).replace(":latest", "")

                    content_hash = __import__("hashlib").sha256(
                        (text or "").encode("utf-8")
                    ).hexdigest()

                    existing = await local_conn.fetchval(
                        """SELECT content_hash FROM embeddings
                           WHERE source_table = 'memory' AND source_id = $1
                             AND chunk_index = $2 AND embedding_model = $3""",
                        source_id, chunk_index, embed_model,
                    )
                    if existing == content_hash:
                        stats["skipped"] += 1
                        continue

                    await mem.store(
                        text=text or "",
                        writer="openclaw",
                        source_id=source_id,
                        source_table="memory",
                        chunk_index=chunk_index,
                        metadata={
                            "path": path,
                            "start_line": start_line,
                            "end_line": end_line,
                        },
                        content_hash=content_hash,
                        origin_path=path,
                        embedding=vec,
                    )
                    stats["embedded"] += 1
                except Exception as e:
                    logger.warning(
                        "  [openclaw] FAIL %s#%d: %s", source_id, chunk_index, e,
                    )
                    stats["failed"] += 1

    return stats


# ---------------------------------------------------------------------------
# Main — thin dispatcher over the plugin registry
# ---------------------------------------------------------------------------


async def main() -> None:
    """Run every registered Tap + the legacy OpenClaw SQLite phase.

    Every Tap lives under ``services/taps/`` and registers via
    ``importlib.metadata.entry_points``. Adding a new data source is
    ``pip install poindexter-tap-X`` + flipping the enabled flag in
    ``app_settings`` — no edits here.
    """
    start = datetime.now(timezone.utc)
    logger.info("=" * 60)
    logger.info("Auto-Embed run started (plugin runner)")
    logger.info("=" * 60)

    http = httpx.AsyncClient()
    if not await check_ollama(http):
        logger.warning("Ollama down or model missing — skipping this run.")
        await http.aclose()
        return

    try:
        pool = await asyncpg.create_pool(LOCAL_DSN, min_size=1, max_size=4)
    except Exception as e:
        logger.error("Could not connect to local pgvector DB: %s", e)
        await http.aclose()
        return

    local_conn = None
    try:
        from services.taps.runner import run_all

        if not _HAS_MEMORY_CLIENT or MemoryClient is None:
            logger.error(
                "poindexter.memory.MemoryClient unavailable (%s). "
                "Cannot run the Tap registry; exiting.",
                _MEMORY_CLIENT_IMPORT_ERR,
            )
            return

        async with MemoryClient(dsn=LOCAL_DSN, ollama_url=OLLAMA_URL) as mem:
            summary = await run_all(pool, mem)

        logger.info("--- Tap runner summary ---")
        for ts in summary.taps:
            status = "disabled" if not ts.enabled else "ok"
            logger.info(
                "  %-20s %s %d embedded, %d skipped, %d failed (%.2fs)",
                ts.name, status, ts.embedded, ts.skipped, ts.failed, ts.duration_s,
            )

        # Legacy OpenClaw SQLite phase. Will migrate to a Tap later.
        openclaw_stats = {"embedded": 0, "skipped": 0, "failed": 0}
        try:
            local_conn = await asyncpg.connect(LOCAL_DSN)
            openclaw_stats = await sync_openclaw_sqlite(local_conn)
            logger.info(
                "  %-20s ok %d embedded, %d skipped, %d failed",
                "openclaw_sqlite",
                openclaw_stats["embedded"],
                openclaw_stats["skipped"],
                openclaw_stats["failed"],
            )
        except Exception as e:
            logger.error("OpenClaw SQLite sync failed: %s", e)
            openclaw_stats = {"embedded": 0, "skipped": 0, "failed": 1}

        total_embedded = summary.total_embedded + openclaw_stats["embedded"]
        total_failed = summary.total_failed + openclaw_stats["failed"]
        elapsed = (datetime.now(timezone.utc) - start).total_seconds()

        logger.info("")
        logger.info(
            "  TOTAL: %d embedded, %d failed across %d tap(s) + 1 legacy phase in %.1fs",
            total_embedded, total_failed, len(summary.taps), elapsed,
        )
        logger.info("Auto-Embed run complete.\n")

    except Exception as e:
        logger.exception("Fatal error: %s", e)
    finally:
        if local_conn:
            await local_conn.close()
        await pool.close()
        await http.aclose()


if __name__ == "__main__":
    asyncio.run(main())
