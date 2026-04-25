"""One-shot backfill: generate featured images for awaiting_approval tasks
that missed the source_featured_image stage.

Built 2026-04-25 as a manual recovery tool — the 17 awaiting_approval
tasks generated before the SDXL service was healed today have
``featured_image_url IS NULL``, so the operator's review-page previews
show no thumbnails. This script regenerates featured images for them
by calling the same SDXL sidecar + R2 upload path the live pipeline
uses.

Run from inside the worker container so it inherits DB access, the
SDXL host route, and the R2 creds:

    docker exec poindexter-worker python /app/scripts/backfill_awaiting_images.py

Idempotent: skips any task that already has a non-empty
``featured_image_url``. Defaults to the awaiting_approval queue but
accepts ``--task-ids id1,id2,...`` to target specific rows.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
import tempfile
import uuid
from pathlib import Path

import asyncpg
import httpx

logger = logging.getLogger("backfill")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [backfill] %(message)s")


SDXL_URL = os.getenv("SDXL_SERVER_URL", "http://host.docker.internal:9836")
R2_BASE = os.getenv(
    "R2_PUBLIC_BASE_URL",
    "https://pub-1432fdefa18e47ad98f213a8a2bf14d5.r2.dev",
)


def _build_prompt(topic: str, category: str | None) -> str:
    """Turn a topic into a featured-image SDXL prompt.

    Mirrors the production prompt-building shape (no faces, no text,
    abstract scene tied to the topic). Kept simple — no LLM round-trip
    here, just topic-as-seed plus a high-leverage style suffix.
    """
    cat = (category or "technology").lower()
    style_by_category = {
        "technology": "minimalist tech illustration, geometric shapes, cyan and indigo gradient, clean composition",
        "security": "abstract cybersecurity visual, glowing circuit traces, deep blue and purple, no faces",
        "startup": "bold modern editorial illustration, isometric perspective, warm amber accents",
        "engineering": "blueprint-style technical diagram, pencil schematic look, monochrome with one accent color",
        "business": "abstract financial chart art, ascending trend lines, dark background with neon highlights",
        "hardware": "macro photography of clean hardware, GPU dies, cinematic studio lighting, no people",
        "gaming": "vibrant game-art style, neon-lit scene, atmospheric depth, painterly detail",
    }
    style = style_by_category.get(cat, style_by_category["technology"])
    return f"{topic}. {style}. high quality, no text, no watermark, no faces, no hands."


async def _generate_png(prompt: str) -> bytes | None:
    """POST the SDXL sidecar and return raw PNG bytes (or None on failure)."""
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=5.0)) as client:
            resp = await client.post(
                f"{SDXL_URL}/generate",
                json={
                    "prompt": prompt,
                    "steps": 4,
                    "guidance_scale": 1.0,
                },
            )
        ct = resp.headers.get("content-type", "")
        if resp.status_code == 200 and ct.startswith("image/"):
            return resp.content
        if resp.status_code == 200 and ct.startswith("application/json"):
            j = resp.json()
            local_path = j.get("image_path") or j.get("path")
            if local_path and Path(local_path).is_file():
                return Path(local_path).read_bytes()
        logger.warning(
            "SDXL returned %s (ct=%r) body=%s",
            resp.status_code, ct, resp.text[:200],
        )
    except Exception as exc:
        logger.warning("SDXL call failed: %s", exc)
    return None


async def _upload_to_r2(png_bytes: bytes, task_id: str, site_config) -> str | None:
    """Upload via the worker's existing r2_upload_service.

    Reusing the production service rather than reinventing the wheel
    keeps the storage path + key naming + cache-control identical to
    what the live pipeline produces. Saves a tmp file because the
    upload helper takes a path.
    """
    try:
        # Late import — these only resolve cleanly inside the worker
        # container (where /app is on PYTHONPATH).
        sys.path.insert(0, "/app")
        from services.r2_upload_service import upload_to_r2  # type: ignore
    except Exception as exc:
        logger.warning("r2_upload_service import failed: %s", exc)
        return None

    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
        tmp.write(png_bytes)
        tmp_path = tmp.name
    try:
        # Match the live `images/featured/<task_uuid>.jpg` shape so
        # downstream readers don't have to special-case backfilled rows.
        key = f"images/featured/{task_id}.png"
        url = await upload_to_r2(
            tmp_path,
            key,
            content_type="image/png",
            site_config=site_config,
        )
        return url
    except Exception as exc:
        logger.warning("R2 upload failed for %s: %s", task_id, exc)
        return None
    finally:
        with __import__("contextlib").suppress(Exception):
            Path(tmp_path).unlink()


async def _resolve_db_url() -> str | None:
    dsn = os.getenv("DATABASE_URL")
    if dsn:
        return dsn
    sys.path.insert(0, "/app")
    try:
        from brain.bootstrap import resolve_database_url  # type: ignore
    except Exception:
        return None
    return resolve_database_url()


async def main(task_ids: list[str] | None) -> int:
    dsn = await _resolve_db_url()
    if not dsn:
        logger.error("no DATABASE_URL — aborting")
        return 1

    # Construct a SiteConfig populated from the DB so r2_upload_service
    # can fetch storage credentials. The live worker does this in main.py
    # lifespan; we replicate the dance so this script doesn't need a
    # running FastAPI process.
    sys.path.insert(0, "/app")
    from services.site_config import SiteConfig  # type: ignore

    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=2)
    site_config = SiteConfig()
    await site_config.load(pool)

    conn = await asyncpg.connect(dsn)
    try:
        if task_ids:
            rows = await conn.fetch(
                """
                SELECT task_id, topic, category
                  FROM content_tasks
                 WHERE task_id = ANY($1::text[])
                   AND (featured_image_url IS NULL OR featured_image_url = '')
                """,
                task_ids,
            )
        else:
            rows = await conn.fetch(
                """
                SELECT task_id, topic, category
                  FROM content_tasks
                 WHERE status = 'awaiting_approval'
                   AND (featured_image_url IS NULL OR featured_image_url = '')
                 ORDER BY created_at DESC
                """,
            )

        logger.info("found %d task(s) needing featured images", len(rows))
        success = 0
        for r in rows:
            tid = r["task_id"]
            topic = r["topic"] or ""
            category = r["category"] or "technology"
            prompt = _build_prompt(topic, category)
            logger.info("generating for %s — %s", tid[:8], topic[:60])
            png = await _generate_png(prompt)
            if not png:
                logger.warning("  skipped: SDXL returned no image")
                continue
            url = await _upload_to_r2(png, tid, site_config)
            if not url:
                # fall back to constructing the public URL from R2_BASE
                url = f"{R2_BASE}/images/featured/{tid}.png"
            await conn.execute(
                """
                UPDATE content_tasks
                   SET featured_image_url = $1,
                       updated_at = NOW()
                 WHERE task_id = $2
                """,
                url, tid,
            )
            logger.info("  ✓ %s", url)
            success += 1

        logger.info("done: %d/%d images backfilled", success, len(rows))
        return 0 if success == len(rows) else 1
    finally:
        await conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--task-ids",
        help="Comma-separated list of task_ids; defaults to all awaiting_approval rows with NULL featured_image_url",
    )
    args = parser.parse_args()
    ids = [t.strip() for t in args.task_ids.split(",") if t.strip()] if args.task_ids else None
    sys.exit(asyncio.run(main(ids)))
