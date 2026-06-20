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

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

from plugins.config import PluginConfig
from plugins.registry import get_core_samples, get_taps
from services.taps._chunking import chunk_text, content_hash

logger = logging.getLogger(__name__)

# Fallback embedding-model tag, used only when the MemoryClient doesn't
# expose its own ``embed_model``. The store/dedup/delete paths below prefer
# ``mem.embed_model`` so the stored ``embedding_model`` tag always matches
# the model that produced the vector (a mismatch silently re-embeds every
# doc — the dedup check would query the wrong tag).
EMBED_MODEL = "nomic-embed-text"

# Cap retained per-document store-failure samples so a tap that fails on
# thousands of docs can't balloon memory or the summary log. Enough to
# diagnose the usual handful (e.g. the 3 NUL-byte sessions) while bounded.
_MAX_FAILURE_SAMPLES = 25

# Per-tap wall-clock budget (seconds). The standalone auto-embed sidecar loops
# `while true; python auto-embed.py; sleep 3600` with NO outer deadline, so a
# single tap that wedges on a stalled Ollama embed call or a hung DB query would
# freeze the whole embedding pipeline until a human restarts the container.
# Wrapping each tap in this timeout bounds the blast radius to one tap. Generous
# by default — the point is to catch an *infinite* hang, not enforce a tight SLA
# (the slowest real tap, claude_code_sessions, runs ~50s and grows with the
# corpus). Tunable via app_settings.tap_run_timeout_seconds.
_DEFAULT_TAP_TIMEOUT_S = 300

# Documents buffered before a batched dedup pre-fetch + flush. The dedup
# chunk-0 hash lookup is fetched once per source_table per batch (one
# round-trip per source instead of one SELECT per document — poindexter#735),
# so this only bounds peak memory / round-trip granularity; it does NOT affect
# dedup output. Operator-tunable via ``app_settings.tap_dedup_batch_size``.
_DEFAULT_DEDUP_BATCH_SIZE = 256

# Sentinel so ``_store_document`` can distinguish "no hash supplied → run the
# per-document fallback query" from an explicit ``None`` ("no stored chunk-0
# row → treat as new"). The hot tap path always supplies a value (possibly
# ``None``) from the batch pre-fetch.
_UNSET: Any = object()


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
    # Per-document store-failure reasons ("<source_id>: <ExcType>: <msg>"),
    # capped at _MAX_FAILURE_SAMPLES. The runner logs the underlying
    # exception on its own logger, which does NOT propagate to the
    # auto-embed.py file handler — carrying the reason here lets the runner
    # entry point surface it into auto-embed.log (feedback_no_silent_defaults).
    failures: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "enabled": self.enabled,
            "embedded": self.embedded,
            "skipped": self.skipped,
            "failed": self.failed,
            "duration_s": round(self.duration_s, 3),
            "error": self.error,
            "failures": list(self.failures),
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


async def _batch_existing_chunk0_hashes_for(
    pool: Any, docs: list[Any], embedding_model: str
) -> dict[tuple[str, str], str]:
    """Pre-fetch chunk-0 content_hashes for a batch of documents.

    Runs ONE query per distinct ``source_table`` (``source_id = ANY($1)``)
    instead of one SELECT per document — the #735 fix for the 51k-call-per-window
    dedup hot path. Keyed by ``(source_table, source_id)``; a source with no
    stored chunk-0 row is simply absent from the map, so the caller threads
    ``None`` into ``_store_document`` and the document is treated as new.
    """
    by_table: dict[str, set[str]] = {}
    for doc in docs:
        by_table.setdefault(doc.source_table, set()).add(doc.source_id)

    result: dict[tuple[str, str], str] = {}
    async with pool.acquire() as conn:
        for source_table, ids in by_table.items():
            rows = await conn.fetch(
                """
                SELECT source_id, content_hash FROM embeddings
                 WHERE source_table = $1 AND source_id = ANY($2::text[])
                   AND chunk_index = 0 AND embedding_model = $3
                """,
                source_table, list(ids), embedding_model,
            )
            for r in rows:
                result[(source_table, r["source_id"])] = r["content_hash"]
    return result


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


async def _store_document(
    mem: Any,
    pool: Any,
    doc: Any,
    *,
    max_chars: int | None = None,
    existing_hash: Any = _UNSET,
) -> str:
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
    # Tag rows with the model the embedder actually uses, so the dedup check
    # and stale-chunk delete below query the same ``embedding_model`` value
    # that ``mem.store()`` writes (see EMBED_MODEL note above).
    embed_model = getattr(mem, "embed_model", None) or EMBED_MODEL

    # Dedup against the stored chunk-0 hash. The hot tap path supplies a
    # batch-fetched ``existing_hash`` (one query per source for the whole batch,
    # #735); direct callers that omit it fall back to a per-document SELECT.
    if existing_hash is _UNSET:
        async with pool.acquire() as conn:
            existing = await _existing_chunk0_hash(
                conn, doc.source_table, doc.source_id, embed_model
            )
    else:
        existing = existing_hash
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
                conn, doc.source_table, doc.source_id, embed_model, 1,
            )
        return "embedded"

    chunks = (
        chunk_text(text) if max_chars is None else chunk_text(text, max_chars=max_chars)
    )
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
            conn, doc.source_table, doc.source_id, embed_model, total_chunks,
        )

    return "embedded"


async def run_tap(
    tap: Any,
    pool: Any,
    mem: Any,
    *,
    max_chars: int | None = None,
    dedup_batch_size: int = _DEFAULT_DEDUP_BATCH_SIZE,
) -> TapStats:
    """Run one Tap end-to-end, returning a stats summary.

    ``max_chars`` overrides the chunk size for this run (sourced from
    ``app_settings.tap_chunk_max_chars`` by :func:`run_all`); ``None`` uses
    the ``chunk_text`` default.

    Documents are processed in batches of ``dedup_batch_size`` (sourced from
    ``app_settings.tap_dedup_batch_size``): each batch's existing chunk-0 hashes
    are pre-fetched in one query per source_table, so the dedup check costs one
    round-trip per source instead of one SELECT per document (#735).
    """
    import time

    stats = TapStats(name=getattr(tap, "name", type(tap).__name__))

    # Per-install config from app_settings.
    cfg = await PluginConfig.load(pool, "tap", stats.name)
    if not cfg.enabled:
        stats.enabled = False
        logger.info("Tap %s disabled; skipping", stats.name)
        return stats

    embed_model = getattr(mem, "embed_model", None) or EMBED_MODEL

    async def _process_batch(batch_docs: list[Any]) -> None:
        if not batch_docs:
            return
        # One dedup round-trip per source_table for the whole batch (#735),
        # then store each document with its pre-fetched chunk-0 hash.
        existing = await _batch_existing_chunk0_hashes_for(
            pool, batch_docs, embed_model
        )
        for doc in batch_docs:
            try:
                outcome = await _store_document(
                    mem,
                    pool,
                    doc,
                    max_chars=max_chars,
                    existing_hash=existing.get((doc.source_table, doc.source_id)),
                )
            except Exception as e:
                logger.exception(
                    "Tap %s: store failed for %s: %s", stats.name, doc.source_id, e
                )
                stats.failed += 1
                if len(stats.failures) < _MAX_FAILURE_SAMPLES:
                    stats.failures.append(f"{doc.source_id}: {type(e).__name__}: {e}")
                continue
            if outcome == "embedded":
                stats.embedded += 1
            elif outcome == "skipped":
                stats.skipped += 1
            else:
                stats.failed += 1

    start = time.monotonic()
    try:
        batch: list[Any] = []
        async for doc in tap.extract(pool, cfg.config):
            batch.append(doc)
            if len(batch) >= dedup_batch_size:
                await _process_batch(batch)
                batch = []
        await _process_batch(batch)  # flush the remainder
    except Exception as e:
        logger.exception("Tap %s: extract failed: %s", stats.name, e)
        stats.error = str(e)
        stats.failed += 1
    finally:
        stats.duration_s = time.monotonic() - start

    return stats


async def run_all(
    pool: Any, mem: Any, *, tap_timeout_s: float | None = None
) -> RunSummary:
    """Run every registered Tap in sequence and return an aggregated summary.

    Taps run sequentially (not in parallel) because they all share the
    same DB pool + Ollama endpoint; parallelism would mostly fight for
    the same resources. A future optimization can group by cost
    (network-bound taps can interleave with DB-bound taps).

    Each tap is bounded by ``tap_timeout_s`` (a per-tap wall-clock budget). A
    tap that exceeds it is cancelled and recorded as a failed tap with a
    timeout reason; the run then continues with the remaining taps — so one
    wedged tap (stalled Ollama, hung query) can't freeze the whole sidecar,
    which has no outer deadline. When ``None`` (the production default) the
    budget is read from ``app_settings.tap_run_timeout_seconds`` (falling back
    to ``_DEFAULT_TAP_TIMEOUT_S``); callers/tests may pin it explicitly.
    """
    import time

    summary = RunSummary()
    start = time.monotonic()

    # Global ingest chunk-size tunable, read once per run (not per doc) and
    # threaded into each tap. Falls back to the chunk_text default if the
    # SiteConfig load fails (best-effort — ingest must not hard-fail here).
    # The per-tap timeout is resolved in the same pass unless the caller pinned
    # it (so a config-read hiccup still leaves a sane bound in place).
    chunk_max_chars: int | None = None
    _timeout_pinned = tap_timeout_s is not None
    if tap_timeout_s is None:
        tap_timeout_s = float(_DEFAULT_TAP_TIMEOUT_S)
    dedup_batch_size = _DEFAULT_DEDUP_BATCH_SIZE
    try:
        from services.site_config import SiteConfig

        _sc = SiteConfig(pool=pool)
        await _sc.load(pool)
        chunk_max_chars = _sc.get_int("tap_chunk_max_chars", 6000)
        if not _timeout_pinned:
            tap_timeout_s = float(
                _sc.get_int("tap_run_timeout_seconds", _DEFAULT_TAP_TIMEOUT_S)
            )
        dedup_batch_size = _sc.get_int("tap_dedup_batch_size", _DEFAULT_DEDUP_BATCH_SIZE)
    except Exception:  # noqa: BLE001 — config read is best-effort
        # Visible (warning, not debug): a failed settings read means the DB
        # read path hiccuped, which is worth surfacing even though ingest
        # safely falls back to the chunk_text default. Keeps the handler out
        # of the silent-except lint baseline (audit H2).
        logger.warning(
            "[TAP_RUNNER] tap_chunk_max_chars / tap_run_timeout_seconds / "
            "tap_dedup_batch_size read failed; using defaults",
            exc_info=True,
        )

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
        tap_name = getattr(tap, "name", type(tap).__name__)
        try:
            stats = await asyncio.wait_for(
                run_tap(
                    tap,
                    pool,
                    mem,
                    max_chars=chunk_max_chars,
                    dedup_batch_size=dedup_batch_size,
                ),
                timeout=tap_timeout_s,
            )
        except asyncio.TimeoutError:
            # Bound a wedged tap (stalled Ollama embed / hung query) instead of
            # letting it freeze the whole run. Recorded as a failed tap WITH a
            # reason so the summary + auto-embed.log surface why
            # (feedback_no_silent_defaults). Best-effort: wait_for can only
            # cancel at an await point, so a tap blocked in pure sync code won't
            # interrupt until it next yields — every real tap awaits DB/HTTP.
            stats = TapStats(
                name=tap_name,
                failed=1,
                error=f"exceeded {tap_timeout_s:g}s tap timeout — cancelled",
            )
            logger.warning(
                "[TAP_RUNNER] tap %s exceeded %ss — cancelled to bound the run",
                tap_name, tap_timeout_s,
            )
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
