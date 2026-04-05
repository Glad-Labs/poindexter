"""Batch upload generated featured images to Cloudinary and update posts."""
import os
import asyncio
import httpx
import cloudinary
import cloudinary.uploader

API_URL = "https://cofounder-production.up.railway.app"
TOKEN = os.environ.get("API_TOKEN", "")
HEADERS = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}
IMAGE_DIR = os.path.join(os.path.expanduser("~"), "Downloads", "glad-labs-featured-images")

# Configure Cloudinary
cloudinary.config(
    cloud_name="dujk7kdhd",
    api_key="432885452812599",
    api_secret="w6VvLuIgxNSTvsxjD9GW9NGTm4I",
)

TAKEN_DOWN = {
    "359e94fa-2fbe-4227-8f85-76399098d4ea",
    "d3690cdf-2285-4ed2-ace7-fcaa53193097",
    "8f48976f-79eb-4d97-b9f1-8dbf81505339",
    "39234d1c-1fb1-4aa5-83cc-7f3a334baccc",
    "38694bf3-02ac-487a-9f90-5ca1ec8105ee",
}


async def main():
    # Get all published posts
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(
            f"{API_URL}/api/posts?limit=50&published_only=true",
            headers=HEADERS,
        )
        resp.raise_for_status()
        posts = resp.json().get("posts", [])

    posts = [p for p in posts if p["id"] not in TAKEN_DOWN]
    print(f"Found {len(posts)} published posts")

    # Map post IDs to image files
    images = {}
    for f in os.listdir(IMAGE_DIR):
        if f.endswith(".png"):
            # Filename is first 12 chars of post ID
            prefix = f.replace(".png", "")
            images[prefix] = os.path.join(IMAGE_DIR, f)

    print(f"Found {len(images)} generated images")

    uploaded = 0
    updated = 0

    for post in posts:
        post_id = post["id"]
        title = post["title"][:50]
        # Match by first 11 chars of ID (files are truncated)
        prefix = post_id[:11]
        matching = [k for k in images if k.startswith(prefix)]

        if not matching:
            print(f"  SKIP (no image): {title}")
            continue

        image_path = images[matching[0]]
        size_kb = os.path.getsize(image_path) / 1024

        # Upload to Cloudinary
        try:
            result = cloudinary.uploader.upload(
                image_path,
                folder="generated/featured/",
                resource_type="image",
                public_id=f"featured-{post_id[:12]}",
                overwrite=True,
            )
            cloud_url = result.get("secure_url", "")
            uploaded += 1
            print(f"  UPLOADED ({size_kb:.0f}KB): {title}")
        except Exception as e:
            print(f"  UPLOAD FAILED: {title} — {e}")
            continue

        # Update post with new image URL
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.patch(
                    f"{API_URL}/api/posts/{post_id}",
                    headers=HEADERS,
                    json={"featured_image_url": cloud_url},
                )
                if resp.status_code == 200:
                    updated += 1
                else:
                    print(f"    UPDATE FAILED: {resp.status_code}")
        except Exception as e:
            print(f"    UPDATE FAILED: {e}")

    print(f"\nDone: {uploaded} uploaded, {updated} posts updated")


if __name__ == "__main__":
    asyncio.run(main())
