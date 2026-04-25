"""One-off: inject 3 inline SDXL images into the mem0 awaiting_approval task,
strip the broken /posts/the-architects-guide-... reference (links to a draft
post that never published), and write the cleaned content back.

Run inside the worker container:

    docker cp scripts/add_inline_images_mem0.py poindexter-worker:/tmp/
    docker exec poindexter-worker python /tmp/add_inline_images_mem0.py

Idempotent enough — re-running just regenerates the inline images (new R2 keys)
and re-strips the same dead link.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import sys
import tempfile
from pathlib import Path

import asyncpg
import httpx

logger = logging.getLogger("inline")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [inline] %(message)s")

TASK_ID = "346f4919-5a4f-4452-a500-09a1382534bb"
SDXL_URL = os.getenv("SDXL_SERVER_URL", "http://host.docker.internal:9836")

# Three image targets — one per architectural milestone in the post.
# The marker is the H3 line; the image goes immediately after it (with the
# blank line the writer already left in place). Captions are short and
# descriptive enough that the alt text doubles as a screen-reader summary.
INLINE_IMAGES = [
    {
        "after_heading": "### How Mem0 Bridges the Divide Between Models",
        "alt": "Layered architecture diagram with the model provider on top, a memory abstraction layer in the middle, and a vector store underneath.",
        "prompt": (
            "Layered architecture diagram of a memory layer for AI agents: "
            "model provider on top, abstraction layer in the middle, vector "
            "store at the bottom. Minimalist tech illustration, isometric, "
            "cyan and indigo gradient, geometric, no faces, no text, "
            "high quality."
        ),
    },
    {
        "after_heading": "### The Architecture of Persistent Intelligence",
        "alt": "Closed-loop diagram of input → retrieval → augmentation → execution → storage feeding back into retrieval.",
        "prompt": (
            "Closed-loop flow diagram with five abstract stages around a "
            "vector memory core. Minimalist editorial illustration, glowing "
            "circuit traces, deep blue and purple, geometric shapes, no "
            "faces, no text, no watermark."
        ),
    },
    {
        "after_heading": "### Productionizing Memory: Data Privacy and Scalability",
        "alt": "Self-hosted data vault with encryption shielding, suggesting on-premise control of memory storage.",
        "prompt": (
            "Self-hosted secure data vault with encryption shielding and "
            "isolated infrastructure. Abstract cybersecurity visual, glowing "
            "circuit traces, deep blue and purple, no faces, no text, "
            "high quality."
        ),
    },
]


async def _generate_png(prompt: str) -> bytes | None:
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=5.0)) as client:
            resp = await client.post(
                f"{SDXL_URL}/generate",
                json={"prompt": prompt, "steps": 4, "guidance_scale": 1.0},
            )
        ct = resp.headers.get("content-type", "")
        if resp.status_code == 200 and ct.startswith("image/"):
            return resp.content
        if resp.status_code == 200 and ct.startswith("application/json"):
            j = resp.json()
            local = j.get("image_path") or j.get("path")
            if local and Path(local).is_file():
                return Path(local).read_bytes()
    except Exception as exc:
        logger.warning("SDXL call failed: %s", exc)
    return None


async def _upload(png: bytes, key_suffix: str, site_config) -> str | None:
    sys.path.insert(0, "/app")
    from services.r2_upload_service import upload_to_r2  # type: ignore

    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
        tmp.write(png)
        path = tmp.name
    try:
        return await upload_to_r2(
            path,
            f"images/inline/{TASK_ID}-{key_suffix}.png",
            content_type="image/png",
            site_config=site_config,
        )
    finally:
        with __import__("contextlib").suppress(Exception):
            Path(path).unlink()


def _strip_broken_link(content: str) -> str:
    """Remove the reference to /posts/the-architects-guide-why-ai-dreams-die-in-the-file-b15d956a.

    The linked post exists in the DB but is still in `draft` status, so the
    public site renders a 404 for readers. Two appearances:
      1. Body sentence ending with "...As discussed in our guide on [why AI
         dreams die in the file system](/posts/...).". Soften to a plain
         sentence: drop the link clause entirely.
      2. External Resources bullet titled "The Architecture of AI:
         [The Architect's Guide...]". Drop the bullet wholesale.
    """
    # 1) Body sentence — find the leading "As discussed in our guide on" and
    # delete from there through the period at the end of the sentence.
    pattern_body = re.compile(
        r" As discussed in our guide on \[why AI dreams die in the file system\]"
        r"\(/posts/the-architects-guide-why-ai-dreams-die-in-the-file-b15d956a\),"
        r" the architecture must support the application's lifecycle, not just"
        r" the model's inference capabilities\.",
    )
    content = pattern_body.sub("", content)

    # 2) External-Resources bullet — match the line and any trailing newlines
    # so the section list closes cleanly.
    pattern_bullet = re.compile(
        r"\*\s+\*\*The Architecture of AI:\*\* \[The Architect's Guide: Why AI Dreams "
        r"Die in the File System\]\(/posts/the-architects-guide-why-ai-dreams-die-in-the-file-b15d956a\)"
        r" - Deep dive into infrastructure for AI applications\.\n",
    )
    content = pattern_bullet.sub("", content)
    return content


def _insert_image(content: str, after_heading: str, alt: str, url: str) -> str:
    """Insert ``![alt](url)`` immediately after the matching H3 line."""
    needle = after_heading + "\n"
    idx = content.find(needle)
    if idx < 0:
        logger.warning("heading not found: %s", after_heading[:60])
        return content
    insert_at = idx + len(needle)
    snippet = f"\n![{alt}]({url})\n"
    return content[:insert_at] + snippet + content[insert_at:]


async def main() -> int:
    dsn = os.getenv("DATABASE_URL")
    if not dsn:
        sys.path.insert(0, "/app")
        from brain.bootstrap import resolve_database_url  # type: ignore
        dsn = resolve_database_url()

    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=2)
    sys.path.insert(0, "/app")
    from services.site_config import SiteConfig  # type: ignore
    site_config = SiteConfig()
    await site_config.load(pool)

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT content FROM content_tasks WHERE task_id = $1",
            TASK_ID,
        )
        if not row:
            logger.error("task not found: %s", TASK_ID)
            return 1
        content: str = row["content"]

    # Generate + upload + insert each image.
    for i, spec in enumerate(INLINE_IMAGES, start=1):
        logger.info("generating image %d: %s", i, spec["after_heading"][:50])
        png = await _generate_png(spec["prompt"])
        if not png:
            logger.warning("  skipped image %d — SDXL returned nothing", i)
            continue
        url = await _upload(png, f"img{i}", site_config)
        if not url:
            logger.warning("  skipped image %d — upload failed", i)
            continue
        content = _insert_image(content, spec["after_heading"], spec["alt"], url)
        logger.info("  ✓ %s", url)

    # Strip the broken /posts/the-architects-guide-... link in two places.
    before = content
    content = _strip_broken_link(content)
    if content == before:
        logger.warning("broken-link strip did NOT match — content unchanged in that step")
    else:
        logger.info("✓ stripped broken /posts/the-architects-guide-... references")

    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE content_tasks SET content = $1, updated_at = NOW() "
            "WHERE task_id = $2",
            content, TASK_ID,
        )
    logger.info("done — task %s updated", TASK_ID)
    await pool.close()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
