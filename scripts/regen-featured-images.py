"""
One-shot script to regenerate featured images for live published posts that
were generated against the host SDXL server while it defaulted to Turbo.

Approach:
- Read all published posts whose featured_image_url points at our R2 bucket.
- For each post, build an SDXL prompt from the title + category-appropriate
  style (using Ollama if available, falling back to a category style template).
- Call the host SDXL server's /generate (now Lightning).
- Read the produced PNG and upload it back to R2 at the SAME object key, so
  the post's featured_image_url stays valid (cache-busting handled by R2).
- Print a one-line per-post status to stdout. Errors don't abort the loop.

Usage:
    python scripts/regen-featured-images.py            # regen all live R2 posts
    python scripts/regen-featured-images.py --dry-run  # list, don't touch
    python scripts/regen-featured-images.py --limit 1  # try one post
    python scripts/regen-featured-images.py --post-id <uuid>  # one specific post

Safe to re-run: each invocation overwrites the same R2 keys with fresh images.
"""
import argparse
import asyncio
import os
import random
import time
from typing import Optional
from urllib.parse import urlparse

import asyncpg
import boto3
import httpx

DB_URL = os.getenv(
    "POINDEXTER_BRAIN_URL",
    os.getenv(
        "GLADLABS_BRAIN_URL",
        "postgresql://poindexter:poindexter-brain-local@localhost:15432/poindexter_brain",
    ),
)
SDXL_URL = "http://localhost:9836"
OLLAMA_URL = "http://localhost:11434"
OLLAMA_MODEL = "llama3:latest"

# Editorial style menu — same set used by content_router_service.py for new posts.
IMAGE_STYLES = [
    ("editorial illustration of a busy futuristic workspace",
     "stylized, warm lighting, faceless figures, conceptual art"),
    ("dark atmospheric cityscape at night",
     "neon accents, rain-slicked streets, moody, cinematic"),
    ("stylized bird's-eye view of a sprawling tech campus",
     "golden hour, miniature tilt-shift effect, dreamy"),
    ("abstract tech prototype sketch",
     "blueprint style, glowing lines, futuristic engineering concept art"),
    ("conceptual art of a vast digital landscape",
     "flowing data streams, abstract geometric shapes, ethereal lighting"),
]


def extract_r2_key(url: str) -> Optional[str]:
    """Pull the object key out of an R2 public URL.

    Example:
        https://pub-xxx.r2.dev/images/featured/abc123.png
        -> images/featured/abc123.png
    """
    parsed = urlparse(url)
    if not parsed.netloc.endswith("r2.dev"):
        return None
    return parsed.path.lstrip("/")


async def fetch_settings(conn) -> dict:
    rows = await conn.fetch(
        "SELECT key, value FROM app_settings WHERE key = ANY($1::text[])",
        [
            "cloudflare_r2_access_key",
            "cloudflare_r2_secret_key",
            "cloudflare_r2_endpoint",
            "cloudflare_r2_bucket",
            "image_negative_prompt",
        ],
    )
    return {r["key"]: r["value"] for r in rows}


async def fetch_target_posts(conn, limit: Optional[int], one_post_id: Optional[str]):
    if one_post_id:
        rows = await conn.fetch(
            """
            SELECT p.id, p.title, p.featured_image_url, c.name AS category, p.metadata
            FROM posts p LEFT JOIN categories c ON p.category_id = c.id
            WHERE p.id = $1
            """,
            one_post_id,
        )
    else:
        sql = """
            SELECT p.id, p.title, p.featured_image_url, c.name AS category, p.metadata
            FROM posts p LEFT JOIN categories c ON p.category_id = c.id
            WHERE p.status = 'published'
              AND p.featured_image_url LIKE '%r2.dev%'
            ORDER BY p.published_at DESC
        """
        if limit:
            sql += f" LIMIT {int(limit)}"
        rows = await conn.fetch(sql)
    return rows


async def craft_prompt(title: str, _category: Optional[str]) -> tuple[str, str]:
    """Use Ollama to write an editorial SDXL prompt for this post.

    Falls back to a templated prompt if Ollama is unreachable.
    Returns (prompt, chosen_style_name) for logging.
    """
    style_name, style_tags = random.choice(IMAGE_STYLES)
    fallback = f"{style_name}, {style_tags}, no text, no faces, related to {title[:60]}"

    instruction = (
        f"Write a Stable Diffusion XL image prompt for a magazine-style editorial "
        f"cover image.\nThe article is about: {title}\n"
        f"DO NOT depict the topic literally. Instead, create an atmospheric scene "
        f"that evokes the FEELING of the topic.\nStyle direction: {style_name}\n\n"
        f"Requirements: {style_tags}, faceless silhouettes OK but no identifiable "
        f"faces, no text or words in the image, no hands. "
        f"Think editorial magazine art - mood, atmosphere, imagination. "
        f"1-2 sentences only. Output ONLY the prompt, nothing else."
    )

    try:
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.post(
                f"{OLLAMA_URL}/api/generate",
                json={
                    "model": OLLAMA_MODEL,
                    "prompt": instruction,
                    "stream": False,
                    "options": {"num_predict": 150, "temperature": 0.7, "num_ctx": 4096},
                },
            )
            r.raise_for_status()
            generated = (r.json().get("response") or "").strip().strip('"')
            if len(generated) > 20:
                return generated, style_name
    except Exception as e:
        print(f"  ! ollama prompt failed ({e}), using fallback", flush=True)

    return fallback, style_name


async def generate_image(prompt: str, negative: str) -> Optional[str]:
    """Call the host SDXL server. Returns local image_path or None on failure."""
    async with httpx.AsyncClient(timeout=180) as c:
        r = await c.post(
            f"{SDXL_URL}/generate",
            json={
                "prompt": prompt,
                "negative_prompt": negative,
                "width": 1024,
                "height": 1024,
            },
        )
        if r.status_code != 200:
            print(f"  ! sdxl returned {r.status_code}: {r.text[:200]}", flush=True)
            return None
        return r.json().get("image_path")


def upload_to_r2(s3, bucket: str, local_path: str, key: str) -> bool:
    try:
        s3.upload_file(
            local_path, bucket, key,
            ExtraArgs={"ContentType": "image/png", "CacheControl": "public, max-age=300"},
        )
        return True
    except Exception as e:
        print(f"  ! r2 upload failed: {e}", flush=True)
        return False


async def main():
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--post-id", default=None)
    args = p.parse_args()

    conn = await asyncpg.connect(DB_URL)
    try:
        settings = await fetch_settings(conn)
        posts = await fetch_target_posts(conn, args.limit, args.post_id)
    finally:
        await conn.close()

    if not posts:
        print("no matching posts", flush=True)
        return

    print(f"found {len(posts)} target post(s)", flush=True)

    if args.dry_run:
        for p_ in posts:
            key = extract_r2_key(p_["featured_image_url"]) or "?"
            print(f"  would regen {p_['id']} [{p_['category'] or '?'}] key={key}",
                  flush=True)
        return

    s3 = boto3.client(
        "s3",
        endpoint_url=settings["cloudflare_r2_endpoint"],
        aws_access_key_id=settings["cloudflare_r2_access_key"],
        aws_secret_access_key=settings["cloudflare_r2_secret_key"],
        region_name="auto",
    )
    bucket = settings.get("cloudflare_r2_bucket", "poindexter-media")
    negative = settings.get("image_negative_prompt", "")

    success = 0
    failed = 0
    for i, post in enumerate(posts, 1):
        post_id = str(post["id"])
        title = post["title"] or "(untitled)"
        category = post["category"]
        key = extract_r2_key(post["featured_image_url"])
        if not key:
            print(f"[{i}/{len(posts)}] {post_id} SKIP — no R2 key in url", flush=True)
            continue

        print(f"[{i}/{len(posts)}] {post_id} [{category or '?'}] {title[:60]}",
              flush=True)
        t0 = time.time()
        prompt, style = await craft_prompt(title, category)
        print(f"  style: {style} | prompt: {prompt[:90]}", flush=True)

        try:
            image_path = await generate_image(prompt, negative)
        except Exception as e:
            print(f"  ! sdxl call raised: {e}", flush=True)
            failed += 1
            continue

        if not image_path or not os.path.exists(image_path):
            print("  ! no image produced", flush=True)
            failed += 1
            continue

        if upload_to_r2(s3, bucket, image_path, key):
            elapsed = time.time() - t0
            print(f"  OK r2://{bucket}/{key} ({elapsed:.1f}s)", flush=True)
            success += 1
            try:
                os.remove(image_path)
            except OSError:
                pass
        else:
            failed += 1

    print(f"\ndone — {success} regenerated, {failed} failed", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
