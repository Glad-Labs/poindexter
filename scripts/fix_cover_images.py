"""Copy featured_image_url to cover_image_url for all posts."""
import os
import asyncio
import httpx

API_URL = "https://cofounder-production.up.railway.app"
TOKEN = os.environ.get("API_TOKEN", "")
HEADERS = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}

TAKEN_DOWN = {
    "359e94fa-2fbe-4227-8f85-76399098d4ea",
    "d3690cdf-2285-4ed2-ace7-fcaa53193097",
    "8f48976f-79eb-4d97-b9f1-8dbf81505339",
    "39234d1c-1fb1-4aa5-83cc-7f3a334baccc",
    "38694bf3-02ac-487a-9f90-5ca1ec8105ee",
}


async def main():
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(
            f"{API_URL}/api/posts?limit=50&published_only=true",
            headers=HEADERS,
        )
        resp.raise_for_status()
        posts = resp.json().get("posts", [])

    posts = [p for p in posts if p["id"] not in TAKEN_DOWN]
    print(f"Updating cover_image_url for {len(posts)} posts")

    updated = 0
    for post in posts:
        new_url = post.get("featured_image_url", "")
        old_cover = post.get("cover_image_url", "")
        if new_url and new_url != old_cover:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.patch(
                    f"{API_URL}/api/posts/{post['id']}",
                    headers=HEADERS,
                    json={"cover_image_url": new_url},
                )
                if resp.status_code == 200:
                    updated += 1
                    print(f"  UPDATED: {post['title'][:50]}")
                else:
                    print(f"  ERROR: {post['title'][:50]} — {resp.status_code}")
        else:
            print(f"  SKIP: {post['title'][:50]}")

    print(f"\nDone: {updated} posts updated")


if __name__ == "__main__":
    asyncio.run(main())
