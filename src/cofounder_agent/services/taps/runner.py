"""Tap runner — iterate registered Taps and persist their Documents.

Single entry point for any code that wants to "run the pipeline's data
ingestion." ``scripts/auto-embed.py`` collapses down to calling
:func:`run_all` and logging the summary.

## Contract

Each Tap yields Documents with one per source item (file, row, record).
The runner is responsible for:

- Loading PluginConfig (enable/disable, per-Tap settings)
- Calling ``tap.extract(pool, config)``
- For each yielded Document:
  - Checking content_hash dedup against the existing chunk-0 row
  - Chunking on text size (``chunk_text`` in ``_chunking.py``)
  - Calling ``MemoryClient.store()`` for each chunk
  - Deleting stale chunks from previous larger versions
- Aggregating stats per Tap: ``embedded`` / ``skipped`` / ``failed``

This is also the place to add cross-cutting concerns later: per-Tap
timing metrics (for Prometheus), per-Tap error quarantine (disable
after N consecutive failures), etc.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from plugins.config import PluginConfig
from plugins.registry import get_core_samples, get_taps
from services.taps._chunking import chunk_text, content_hash

logger = logging.getLogger(__name__)

EMBED_MODEL = "nomic-embed-text"


@dataclass
class TapStats:
    """Per-Tap execution summary."""

    name: str
    enabled: bool = True
    embedded: int = 0
    skipped: int = 0
    failed: int = 0
    duration_s: float = 0.0
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "enabled": self.enabled,
            "embedded": self.embedded,
            "skipped": self.skipped,
            "failed": self.failed,
            "duration_s": round(self.duration_s, 3),
            "error": self.error,
        }


@dataclass
class RunSummary:
    """Top-level summary of a full runner pass."""

    taps: list[TapStats] = field(default_factory=list)
    total_embedded: int = 0
    total_skipped: int = 0
    total_failed: int = 0
    duration_s: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "taps": [t.to_dict() for t in self.taps],
            "total_embedded": self.total_embedded,
            "total_skipped": self.total_skipped,
            "total_failed": self.total_failed,
            "duration_s": round(self.duration_s, 3),
        }


async def _existing_chunk0_hash(
    conn: Any,
    source_table: str,
    source_id: str,
    embedding_model: str,
) -> str | None:
    """Return the content_hash of the chunk-0 row for this source, or None."""
    return await conn.fetchval(
        """
        SELECT content_hash FROM embeddings
         WHERE source_table = $1 AND source_id = $2
           AND chunk_index = 0 AND embedding_model = $3
        """,
        source_table, source_id, embedding_model,
    )


async def _delete_stale_chunks(
    conn: Any,
    source_table: str,
    source_id: str,
    embedding_model: str,
    keep_count: int,
) -> None:
    """Remove chunks with index >= keep_count — used when a document
    shrinks and now has fewer chunks than before.
    """
    await conn.execute(
        """
        DELETE FROM embeddings
         WHERE source_table = $1 AND source_id = $2
           AND embedding_model = $3 AND chunk_index >= $4
        """,
        source_table, source_id, embedding_model, keep_count,
    )


async def _store_document(mem: Any, pool: Any, doc: Any) -> str:
    """Persist one Document (with chunking + dedup). Returns one of:

    ``"embedded"``, ``"skipped"``, ``"failed"``.

    When the Document carries a ``precomputed_embedding``, skip the
    chunking pipeline entirely and store as a single row with the
    provided vector. Callers that ship their own embeddings (e.g.
    OpenClaw whose chunks are already nomic-embed-text-vectorized)
    use this path to avoid paying for Ollama twice.
    """
    text = doc.text
    if not text or not text.strip():
        return "skipped"

    full_hash = content_hash(text)

    async with pool.acquire() as conn:
        existing = await _existing_chunk0_hash(
            conn, doc.source_table, doc.source_id, EMBED_MODEL
        )
    if existing == full_hash:
        return "skipped"

    precomputed = getattr(doc, "precomputed_embedding", None)
    if precomputed is not None:
        # Single-row store path — the upstream source already chunked
        # the text when it generated the vector, so re-chunking here
        # would fracture the embedding.
        await mem.store(
            text=text,
            writer=doc.writer,
            source_id=doc.source_id,
            source_table=doc.source_table,
            chunk_index=0,
            metadata={
                **doc.metadata,
                "chars": len(text),
                "total_chunks": 1,
                "chunk_index": 0,
                "precomputed": True,
            },
            content_hash=full_hash,
            origin_path=doc.metadata.get("origin_path", ""),
            embedding=precomputed,
        )
        # Clean up any stale chunks from a prior re-chunked store.
        async with pool.acquire() as conn:
            await _delete_stale_chunks(
                conn, doc.source_table, doc.source_id, EMBED_MODEL, 1,
            )
        return "embedded"

    chunks = chunk_text(text)
    total_chunks = len(chunks)
    metadata_base = dict(doc.metadata)
    metadata_base.setdefault("chars", len(text))
    metadata_base["total_chunks"] = total_chunks

    for idx, chunk in enumerate(chunks):
        chunk_meta = {**metadata_base, "chunk_index": idx}
        await mem.store(
            text=chunk,
            writer=doc.writer,
            source_id=doc.source_id,
            source_table=doc.source_table,
            chunk_index=idx,
            metadata=chunk_meta,
            # chunk 0 uses the full-document hash so our dedup check keeps
            # working; other chunks hash their own content for per-chunk
            # content tracking.
            content_hash=full_hash if idx == 0 else content_hash(chunk),
            origin_path=doc.metadata.get("origin_path", ""),
        )

    # Clean up leftover chunks if the doc shrank.
    async with pool.acquire() as conn:
        await _delete_stale_chunks(
            conn, doc.source_table, doc.source_id, EMBED_MODEL, total_chunks,
        )

    return "embedded"


async def run_tap(tap: Any, pool: Any, mem: Any) -> TapStats:
    """Run one Tap end-to-end, returning a stats summary."""
    import time

    stats = TapStats(name=getattr(tap, "name", type(tap).__name__))

    # Per-install config from app_settings.
    cfg = await PluginConfig.load(pool, "tap", stats.name)
    if not cfg.enabled:
        stats.enabled = False
        logger.info("Tap %s disabled; skipping", stats.name)
        return stats

    start = time.monotonic()
    try:
        async for doc in tap.extract(pool, cfg.config):
            try:
                outcome = await _store_document(mem, pool, doc)
            except Exception as e:
                logger.exception("Tap %s: store failed for %s: %s", stats.name, doc.source_id, e)
                stats.failed += 1
                continue
            if outcome == "embedded":
                stats.embedded += 1
            elif outcome == "skipped":
                stats.skipped += 1
            else:
                stats.failed += 1
    except Exception as e:
        logger.exception("Tap %s: extract failed: %s", stats.name, e)
        stats.error = str(e)
        stats.failed += 1
    finally:
        stats.duration_s = time.monotonic() - start

    return stats


async def run_all(pool: Any, mem: Any) -> RunSummary:
    """Run every registered Tap in sequence and return an aggregated summary.

    Taps run sequentially (not in parallel) because they all share the
    same DB pool + Ollama endpoint; parallelism would mostly fight for
    the same resources. A future optimization can group by cost
    (network-bound taps can interleave with DB-bound taps).
    """
    import time

    summary = RunSummary()
    start = time.monotonic()

    # Entry_points discovery + core-sample imperative loads.
    # De-dup by name so a core sample that also ships as an entry_point
    # doesn't run twice.
    seen: set[str] = set()
    all_taps: list[Any] = []
    for tap in list(get_taps()) + list(get_core_samples().get("taps", [])):
        if tap.name in seen:
            continue
        seen.add(tap.name)
        all_taps.append(tap)

    for tap in all_taps:
        stats = await run_tap(tap, pool, mem)
        summary.taps.append(stats)
        summary.total_embedded += stats.embedded
        summary.total_skipped += stats.skipped
        summary.total_failed += stats.failed
        logger.info(
            "Tap %s: %d embedded, %d skipped, %d failed (%.2fs)",
            stats.name, stats.embedded, stats.skipped, stats.failed, stats.duration_s,
        )

    summary.duration_s = time.monotonic() - start
    return summary
