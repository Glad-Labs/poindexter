"""Regenerate all featured images using the new 5-style SDXL palette."""
import json
import os
import asyncio
import random
import httpx

API_URL = "https://cofounder-production.up.railway.app"
TOKEN = os.environ.get("API_TOKEN", "")
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
SDXL_URL = os.environ.get("SDXL_URL", "http://localhost:9836")
HEADERS = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}

TAKEN_DOWN = {
    "359e94fa-2fbe-4227-8f85-76399098d4ea",
    "d3690cdf-2285-4ed2-ace7-fcaa53193097",
    "8f48976f-79eb-4d97-b9f1-8dbf81505339",
    "39234d1c-1fb1-4aa5-83cc-7f3a334baccc",
    "38694bf3-02ac-487a-9f90-5ca1ec8105ee",
}

# The 5 featured image styles Matt approved
FEATURED_STYLES = [
    ("photorealistic scene", "cinematic lighting, shallow depth of field, 4k"),
    ("dark moody editorial photograph", "dramatic side lighting, high contrast, film grain"),
    ("aerial drone photograph", "bird's eye view, golden hour lighting, wide angle"),
    ("macro close-up photograph", "extreme detail, bokeh background, studio lighting"),
    ("cyberpunk neon cityscape", "rain-slicked streets, neon reflections, atmospheric fog"),
]

OUTPUT_DIR = os.path.join(os.path.expanduser("~"), "Downloads", "glad-labs-featured-images")
os.makedirs(OUTPUT_DIR, exist_ok=True)


async def generate_sdxl_prompt(topic: str, content_excerpt: str, style_name: str, style_tags: str) -> str:
    """Use Ollama to create a specific SDXL prompt."""
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                f"{OLLAMA_URL}/api/generate",
                json={
                    "model": "llama3:latest",
                    "prompt": (
                        f"Write a Stable Diffusion XL image prompt for a blog featured image.\n"
                        f"Topic: {topic}\n"
                        f"Context: {content_excerpt[:200]}\n"
                        f"Style: {style_name}\n\n"
                        f"Requirements: {style_tags}, no people, no text, no faces, no hands. "
                        "Describe a specific concrete scene. 1-2 sentences only. "
                        "Output ONLY the prompt, nothing else."
                    ),
                    "stream": False,
                    "options": {"num_predict": 150, "temperature": 0.7},
                },
            )
            resp.raise_for_status()
            result = resp.json().get("response", "").strip().strip('"')
            if len(result) > 20:
                return result
    except Exception as e:
        print(f"    LLM prompt failed: {e}")

    return f"{style_name} of {topic}, {style_tags}, no people, no text"


async def generate_image(prompt: str, post_id: str) -> str | None:
    """Generate an image via SDXL server, return local path."""
    neg = "text, words, letters, watermark, face, person, hands, blurry, low quality, distorted, ugly, deformed"
    try:
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(
                f"{SDXL_URL}/generate",
                json={
                    "prompt": prompt,
                    "negative_prompt": neg,
                    "steps": 4,
                    "guidance_scale": 1.0,
                },
            )
            if resp.status_code == 200 and resp.headers.get("content-type", "").startswith("image/"):
                path = os.path.join(OUTPUT_DIR, f"{post_id}.png")
                with open(path, "wb") as f:
                    f.write(resp.content)
                return path
            else:
                print(f"    SDXL error: {resp.status_code}")
    except Exception as e:
        print(f"    SDXL failed: {e}")
    return None


async def upload_to_cloudinary(image_path: str, post_id: str) -> str | None:
    """Upload image to Cloudinary and return URL."""
    try:
        import cloudinary
        import cloudinary.uploader

        # Try to get config from the running worker
        cloud_name = os.environ.get("CLOUDINARY_CLOUD_NAME")
        api_key = os.environ.get("CLOUDINARY_API_KEY")
        api_secret = os.environ.get("CLOUDINARY_API_SECRET")

        if not all([cloud_name, api_key, api_secret]):
            # Try to read from the DB settings via API
            async with httpx.AsyncClient(timeout=10) as client:
                for key in ["cloudinary_cloud_name", "cloudinary_api_key", "cloudinary_api_secret"]:
                    resp = await client.get(
                        f"{API_URL}/api/settings/{key}",
                        headers=HEADERS,
                    )
                    if resp.status_code == 200:
                        val = resp.json().get("value", "")
                        if key == "cloudinary_cloud_name":
                            cloud_name = val
                        elif key == "cloudinary_api_key":
                            api_key = val
                        elif key == "cloudinary_api_secret":
                            api_secret = val

        if not all([cloud_name, api_key, api_secret]):
            print("    Cloudinary config missing — using local path")
            return None

        cloudinary.config(cloud_name=cloud_name, api_key=api_key, api_secret=api_secret)
        result = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: cloudinary.uploader.upload(
                image_path, folder="generated/featured/", resource_type="image",
                public_id=f"featured-{post_id}",
            ),
        )
        return result.get("secure_url")
    except ImportError:
        print("    Cloudinary not installed — using local path")
        return None
    except Exception as e:
        print(f"    Cloudinary upload failed: {e}")
        return None


async def process_post(post: dict, style_idx: int) -> dict:
    """Generate new featured image for a post."""
    post_id = post["id"]
    title = post["title"]
    content = post.get("content", "")[:300]

    style_name, style_tags = FEATURED_STYLES[style_idx % len(FEATURED_STYLES)]
    print(f"\n  {title[:55]}")
    print(f"    Style: {style_name}")

    # Step 1: Generate SDXL prompt via Ollama
    sdxl_prompt = await generate_sdxl_prompt(title, content, style_name, style_tags)
    print(f"    Prompt: {sdxl_prompt[:70]}...")

    # Step 2: Generate image
    image_path = await generate_image(sdxl_prompt, post_id[:12])
    if not image_path:
        return {"title": title[:50], "status": "SDXL_FAILED"}

    size_kb = os.path.getsize(image_path) / 1024
    print(f"    Generated: {size_kb:.0f}KB at {image_path}")

    # Step 3: Upload to Cloudinary
    cloud_url = await upload_to_cloudinary(image_path, post_id[:12])

    if cloud_url:
        # Step 4: Update post with new image URL
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.patch(
                f"{API_URL}/api/posts/{post_id}",
                headers=HEADERS,
                json={"featured_image_url": cloud_url},
            )
            if resp.status_code == 200:
                print(f"    UPDATED: {cloud_url[:60]}...")
                return {"title": title[:50], "status": "UPDATED", "url": cloud_url}
            else:
                print(f"    API ERROR: {resp.status_code}")
                return {"title": title[:50], "status": "API_ERROR"}
    else:
        print(f"    Saved locally (no Cloudinary): {image_path}")
        return {"title": title[:50], "status": "LOCAL_ONLY", "path": image_path}


async def main():
    print("Fetching published posts...")
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(
            f"{API_URL}/api/posts?limit=50&published_only=true",
            headers=HEADERS,
        )
        resp.raise_for_status()
        data = resp.json()

    posts = data.get("posts", [])
    posts = [p for p in posts if p["id"] not in TAKEN_DOWN]
    print(f"Regenerating featured images for {len(posts)} posts")
    print(f"Styles: {[s[0] for s in FEATURED_STYLES]}")

    # Distribute styles evenly across posts
    random.shuffle(posts)  # Randomize order so style distribution is varied
    results = []
    for i, post in enumerate(posts):
        result = await process_post(post, i)
        results.append(result)

    # Summary
    print(f"\n{'='*60}")
    print("IMAGE REGENERATION SUMMARY")
    print(f"{'='*60}")
    by_status = {}
    for r in results:
        s = r["status"]
        by_status[s] = by_status.get(s, 0) + 1

    for status, count in by_status.items():
        print(f"  {status}: {count}")

    style_dist = {}
    for i in range(len(posts)):
        s = FEATURED_STYLES[i % len(FEATURED_STYLES)][0]
        style_dist[s] = style_dist.get(s, 0) + 1
    print(f"\nStyle distribution: {style_dist}")


if __name__ == "__main__":
    asyncio.run(main())
