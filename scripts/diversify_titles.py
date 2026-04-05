"""Diversify post titles — reduce 'The X' pattern via Ollama rewrites."""
import json
import os
import asyncio
import httpx

API_URL = "https://cofounder-production.up.railway.app"
TOKEN = os.environ.get("API_TOKEN", "")
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
HEADERS = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}

TAKEN_DOWN = {
    "359e94fa-2fbe-4227-8f85-76399098d4ea",
    "d3690cdf-2285-4ed2-ace7-fcaa53193097",
    "8f48976f-79eb-4d97-b9f1-8dbf81505339",
    "39234d1c-1fb1-4aa5-83cc-7f3a334baccc",
    "38694bf3-02ac-487a-9f90-5ca1ec8105ee",
}


async def rewrite_title(old_title: str, content_excerpt: str) -> str:
    """Use Ollama to suggest a better title."""
    prompt = f"""You are a headline editor for a technology news site.

Rewrite this blog post title. Rules:
- DO NOT start with "The" — use a different structure
- Keep it punchy, specific, and under 70 characters
- Use action verbs, numbers, "How to", "Why", questions, or direct statements
- Keep the core topic intact
- Output ONLY the new title, nothing else

Original title: {old_title}
Article excerpt: {content_excerpt[:300]}

New title:"""

    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                f"{OLLAMA_URL}/api/generate",
                json={
                    "model": "llama3:latest",
                    "prompt": prompt,
                    "stream": False,
                    "options": {"num_predict": 50, "temperature": 0.7},
                },
            )
            resp.raise_for_status()
            new_title = resp.json().get("response", "").strip().strip('"').strip("'")
            # Sanity checks
            if not new_title or len(new_title) < 10 or len(new_title) > 100:
                return old_title
            # Remove any leading "Title:" or similar
            for prefix in ["Title:", "New title:", "Rewritten:"]:
                if new_title.startswith(prefix):
                    new_title = new_title[len(prefix):].strip()
            return new_title
    except Exception as e:
        print(f"    LLM failed: {e}")
        return old_title


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

    the_posts = [p for p in posts if p.get("title", "").startswith("The ")]
    non_the = [p for p in posts if not p.get("title", "").startswith("The ")]

    print(f"Total: {len(posts)} posts, {len(the_posts)} start with 'The', {len(non_the)} already diverse")
    print()

    updated = 0
    for p in the_posts:
        old_title = p["title"]
        content = p.get("content", "")[:400]
        print(f"  OLD: {old_title[:65]}")

        new_title = await rewrite_title(old_title, content)

        if new_title != old_title and not new_title.startswith("The "):
            # Update title only (NOT slug — preserve URLs)
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.patch(
                    f"{API_URL}/api/posts/{p['id']}",
                    headers=HEADERS,
                    json={"title": new_title},
                )
                if resp.status_code == 200:
                    print(f"  NEW: {new_title[:65]}")
                    updated += 1
                else:
                    print(f"  ERROR: {resp.status_code}")
        else:
            print(f"  SKIP: LLM returned 'The' title or same title")
        print()

    print(f"\nDone: {updated}/{len(the_posts)} titles diversified")


if __name__ == "__main__":
    asyncio.run(main())
