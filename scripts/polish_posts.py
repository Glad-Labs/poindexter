"""Polish published posts — fix bare URLs, clean writing, iterate twice."""
import json
import re
import os
import sys
import httpx
import asyncio

API_URL = "https://cofounder-production.up.railway.app"
TOKEN = os.environ.get("API_TOKEN", "")
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")

HEADERS = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}


def fix_bare_urls(content: str) -> str:
    """Wrap bare URLs in markdown links."""
    # Match URLs that aren't already in markdown link syntax
    # Don't touch URLs inside () or already in [text](url) format
    def replacer(m):
        url = m.group(0)
        # Extract domain for link text
        domain = re.sub(r"https?://(?:www\.)?", "", url).split("/")[0]
        return f"[{domain}]({url})"

    # Only match bare URLs not preceded by ( or ](
    result = re.sub(
        r"(?<!\()(?<!\]\()https?://[^\s\)>\]]+",
        replacer,
        content,
    )
    return result


def fix_title(title: str, all_titles: list) -> str:
    """Reduce 'The X' title pattern — suggest alternatives."""
    if not title.startswith("The "):
        return title
    # Don't change all of them, just reduce the pattern
    return title


async def polish_content_with_llm(title: str, content: str, iteration: int) -> str:
    """Use Ollama to polish content — newsroom voice, clarity, flow."""
    prompt = f"""You are a professional editor for a technology news site called Glad Labs.

Edit the following blog post to improve quality. This is iteration {iteration}/2.

Rules:
- Newsroom journalism voice: report facts, cite sources, never use "we/our/us" unless specifically discussing Glad Labs
- Remove any filler phrases like "In this article", "Let's dive in", "In conclusion"
- Fix awkward sentences, improve flow
- Keep the same structure and length — don't add or remove sections
- Keep all markdown formatting intact
- Don't change code blocks or technical details
- Remove any self-referential meta-commentary about the article itself
- Make sure links are properly formatted as markdown [text](url)
- Don't wrap your response in any extra commentary — output ONLY the edited content

Title: {title}

Content:
{content}"""

    async with httpx.AsyncClient(timeout=120) as client:
        resp = await client.post(
            f"{OLLAMA_URL}/api/generate",
            json={
                "model": "llama3:latest",
                "prompt": prompt,
                "stream": False,
                "options": {"num_predict": 4096, "temperature": 0.3},
            },
        )
        resp.raise_for_status()
        result = resp.json().get("response", "").strip()

        # Sanity check — don't return if it's way too short or looks wrong
        if len(result) < len(content) * 0.5:
            print(f"  WARNING: LLM output too short ({len(result)} vs {len(content)}), keeping original")
            return content
        if len(result) < 200:
            print(f"  WARNING: LLM output suspiciously short, keeping original")
            return content

        return result


async def process_post(post: dict, all_titles: list) -> dict:
    """Process a single post — fix URLs, polish content twice."""
    post_id = post["id"]
    title = post["title"]
    content = post.get("content", "")
    changes = []

    print(f"\n{'='*60}")
    print(f"Processing: {title[:60]}")
    print(f"  ID: {post_id}")
    print(f"  Words: {len(content.split())}")

    # Step 1: Fix bare URLs
    fixed_content = fix_bare_urls(content)
    url_fixes = content.count("http") - fixed_content.count("](http")
    bare_before = len(re.findall(r"(?<!\()(?<!\]\()https?://[^\s\)>\]]+", content))
    bare_after = len(re.findall(r"(?<!\()(?<!\]\()https?://[^\s\)>\]]+", fixed_content))
    if bare_before != bare_after:
        changes.append(f"Fixed {bare_before - bare_after} bare URLs")
        print(f"  Fixed {bare_before - bare_after} bare URLs")
    content = fixed_content

    # Step 2: LLM polish — iteration 1
    print(f"  Polishing iteration 1...")
    polished1 = await polish_content_with_llm(title, content, 1)
    if polished1 != content:
        changes.append("LLM polish iteration 1")
        content = polished1
        print(f"  Iteration 1 done ({len(content.split())} words)")

    # Step 3: LLM polish — iteration 2
    print(f"  Polishing iteration 2...")
    polished2 = await polish_content_with_llm(title, content, 2)
    if polished2 != content:
        changes.append("LLM polish iteration 2")
        content = polished2
        print(f"  Iteration 2 done ({len(content.split())} words)")

    # Step 4: Final bare URL check (LLM might have introduced some)
    content = fix_bare_urls(content)

    # Step 5: Push update to production
    if changes:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.patch(
                f"{API_URL}/api/posts/{post_id}",
                headers=HEADERS,
                json={"content": content},
            )
            if resp.status_code == 200:
                print(f"  UPDATED: {', '.join(changes)}")
            else:
                print(f"  ERROR updating: {resp.status_code} {resp.text[:100]}")
    else:
        print(f"  No changes needed")

    return {"title": title[:50], "changes": changes}


async def main():
    # Load posts
    with open(os.path.join(os.path.dirname(__file__), "posts_audit.json")) as f:
        data = json.load(f)

    posts = data.get("posts", [])
    all_titles = [p.get("title", "") for p in posts]

    # Filter to only published posts (skip the 5 we took down)
    taken_down = {
        "359e94fa-2fbe-4227-8f85-76399098d4ea",
        "d3690cdf-2285-4ed2-ace7-fcaa53193097",
        "8f48976f-79eb-4d97-b9f1-8dbf81505339",
        "39234d1c-1fb1-4aa5-83cc-7f3a334baccc",
        "38694bf3-02ac-487a-9f90-5ca1ec8105ee",
    }
    posts = [p for p in posts if p["id"] not in taken_down]
    print(f"Processing {len(posts)} posts (5 taken down)")

    results = []
    for post in posts:
        result = await process_post(post, all_titles)
        results.append(result)

    # Summary
    print(f"\n{'='*60}")
    print(f"SUMMARY: {len(results)} posts processed")
    updated = [r for r in results if r["changes"]]
    print(f"  Updated: {len(updated)}")
    print(f"  Unchanged: {len(results) - len(updated)}")
    for r in updated:
        print(f"    {r['title']}: {', '.join(r['changes'])}")


if __name__ == "__main__":
    asyncio.run(main())
