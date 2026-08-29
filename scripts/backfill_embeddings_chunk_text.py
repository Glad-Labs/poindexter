#!/usr/bin/env python3
"""Backfill ``embeddings.chunk_text`` for rows written before poindexter#1033.

Why a script and not a migration
--------------------------------
The column cannot be filled by SQL alone — the full chunk text was never
stored, only its first 500 chars. It has to be re-derived from each source
document. And it cannot be left to the Taps: the runner dedups on chunk-0
``content_hash``, so an unchanged document is skipped forever and its
``chunk_text`` would stay NULL indefinitely, permanently pinning retrieval to
the ``text_preview`` fallback.

The safety interlock
--------------------
Re-deriving text is only sound if the re-derived chunking is byte-identical
to what produced the stored vector. This script proves that per document
rather than assuming it:

  1. Re-run the Tap's own ``extract()`` to rebuild the document text.
  2. Recompute ``content_hash(text)`` and require it to equal the stored
     chunk-0 hash. The runner writes the FULL-document hash on chunk 0, so a
     match proves the source text is unchanged since the vector was made.
  3. Require the recomputed chunk COUNT to equal the stored row count.
  4. Only then write each chunk's text.

Any document failing an interlock is skipped and reported — never guessed at.
Writing the wrong text against a real vector would be worse than NULL, since
the fallback at least degrades honestly.

No embedding calls are made. Vectors are never touched.

Usage
-----
Run it in the worker container — that is where the DSN and the importable
``services.*`` / ``plugins.*`` trees live. The container's ``/app`` is
``src/cofounder_agent``, NOT the repo root, so repo-root ``scripts/`` is not
mounted: copy the file in first.

    docker cp scripts/backfill_embeddings_chunk_text.py \
        poindexter-worker:/tmp/backfill_chunk_text.py
    docker exec poindexter-worker python /tmp/backfill_chunk_text.py --dry-run
    docker exec poindexter-worker python /tmp/backfill_chunk_text.py --apply
    docker exec poindexter-worker python /tmp/backfill_chunk_text.py --apply --tap posts
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src", "cofounder_agent"))


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="report only; write nothing")
    mode.add_argument("--apply", action="store_true", help="write chunk_text")
    ap.add_argument("--tap", default="", help="limit to one tap name (e.g. posts)")
    args = ap.parse_args()

    import asyncpg
    from plugins.registry import get_core_samples, get_taps
    from services.taps._chunking import chunk_text, content_hash

    dsn = (
        os.getenv("DATABASE_URL")
        or os.getenv("LOCAL_DATABASE_URL")
        or os.getenv("POINDEXTER_MEMORY_DSN")
    )
    if not dsn:
        print("FATAL: no DSN (DATABASE_URL / LOCAL_DATABASE_URL).", file=sys.stderr)
        return 2

    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=4)
    assert pool is not None

    # Chunk size must match what the runner used, not the module default.
    max_chars = int(
        await pool.fetchval(
            "SELECT value FROM app_settings WHERE key = 'tap_chunk_max_chars'"
        )
        or 6000
    )
    print(f"chunk max_chars = {max_chars}  (app_settings.tap_chunk_max_chars)")

    # Mirror how services/taps/runner.py::run_all enumerates taps — entry-point
    # taps plus the core samples — so this backfill covers exactly the set that
    # writes the rows. Dedup by name: the two sources overlap, and unlike the
    # runner (whose per-doc content_hash dedup makes a second pass a no-op) this
    # script would process every document twice and double-count the report.
    seen: set[str] = set()
    taps = []
    for t in list(get_taps()) + list(get_core_samples().get("taps", [])):
        n = getattr(t, "name", t.__class__.__name__)
        if n in seen:
            continue
        seen.add(n)
        taps.append(t)
    if args.tap:
        taps = [t for t in taps if getattr(t, "name", "") == args.tap]
        if not taps:
            print(f"FATAL: no tap named {args.tap!r}", file=sys.stderr)
            return 2

    tally: Counter[str] = Counter()
    updated_rows = 0

    for tap in taps:
        name = getattr(tap, "name", tap.__class__.__name__)
        try:
            docs = [d async for d in tap.extract(pool, {})]
        except Exception as e:  # noqa: BLE001
            print(f"[{name}] extract failed, skipping: {type(e).__name__}: {e}")
            tally["tap_extract_failed"] += 1
            continue

        for doc in docs:
            text = doc.text or ""
            if not text.strip():
                tally["empty_doc"] += 1
                continue

            rows = await pool.fetch(
                """
                SELECT chunk_index, content_hash, embedding_model, chunk_text
                  FROM embeddings
                 WHERE source_table = $1 AND source_id = $2
                 ORDER BY chunk_index
                """,
                doc.source_table,
                doc.source_id,
            )
            if not rows:
                tally["not_indexed"] += 1
                continue
            if all(r["chunk_text"] for r in rows):
                tally["already_filled"] += 1
                continue

            # Interlock 1 — chunk 0 carries the FULL-document hash.
            chunk0 = next((r for r in rows if r["chunk_index"] == 0), None)
            if chunk0 is None or chunk0["content_hash"] != content_hash(text):
                tally["skipped_source_changed"] += 1
                continue

            chunks = chunk_text(text, max_chars=max_chars)

            # Interlock 2 — chunk count must match the stored row count.
            if len(chunks) != len(rows):
                tally["skipped_chunk_count_mismatch"] += 1
                continue

            if args.dry_run:
                tally["would_update"] += 1
                updated_rows += len(chunks)
                continue

            async with pool.acquire() as conn:
                async with conn.transaction():
                    for idx, chunk in enumerate(chunks):
                        await conn.execute(
                            """
                            UPDATE embeddings SET chunk_text = $1
                             WHERE source_table = $2 AND source_id = $3
                               AND chunk_index = $4 AND chunk_text IS NULL
                            """,
                            chunk,
                            doc.source_table,
                            doc.source_id,
                            idx,
                        )
            tally["updated"] += 1
            updated_rows += len(chunks)

    print("\n--- result ---")
    for k, v in sorted(tally.items()):
        print(f"  {k:32} {v}")
    print(f"  {'chunk rows touched':32} {updated_rows}")

    remaining = await pool.fetchval(
        "SELECT count(*) FROM embeddings WHERE chunk_text IS NULL"
    )
    total = await pool.fetchval("SELECT count(*) FROM embeddings")
    print(f"\nembeddings still NULL: {remaining:,} / {total:,}")
    if remaining:
        print("  (these fall back to text_preview — re-tap or re-embed to fill)")

    await pool.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
