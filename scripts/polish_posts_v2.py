"""Polish published posts v2 — programmatic fixes + targeted LLM polish."""
import json
import re
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

# Filler phrases to remove
FILLER_PATTERNS = [
    r"(?i)\bIn this (?:article|post|piece|guide),?\s*",
    r"(?i)\bLet'?s dive (?:in|into|deeper)\.?\s*",
    r"(?i)\bIn conclusion,?\s*",
    r"(?i)\bTo (?:sum up|summarize|wrap up),?\s*",
    r"(?i)\bAs we'?ve? (?:seen|discussed|explored),?\s*",
    r"(?i)\bWithout further ado,?\s*",
    r"(?i)\bBuckle up\.?\s*",
    r"(?i)\bSo,? let'?s get started\.?\s*",
    r"(?i)\bReady\? Let'?s (?:go|begin|start)\.?\s*",
    r"(?i)\bHere'?s (?:the thing|what you need to know):?\s*",
]

# Voice fixes — generic "we/our" to passive/third-person
VOICE_FIXES = [
    (r"\bWe need to\b", "Developers need to"),
    (r"\bwe need to\b", "developers need to"),
    (r"\bWe can\b", "Teams can"),
    (r"\bwe can\b", "teams can"),
    (r"\bWe should\b", "Organizations should"),
    (r"\bwe should\b", "organizations should"),
    (r"\bour codebase\b", "the codebase"),
    (r"\bOur codebase\b", "The codebase"),
    (r"\bour code\b", "the code"),
    (r"\bOur code\b", "The code"),
    (r"\bour team\b", "the team"),
    (r"\bOur team\b", "The team"),
    (r"\bour application\b", "the application"),
    (r"\bOur application\b", "The application"),
    (r"\bour infrastructure\b", "the infrastructure"),
    (r"\bOur infrastructure\b", "The infrastructure"),
    (r"\bour system\b", "the system"),
    (r"\bOur system\b", "The system"),
    (r"\bour stack\b", "the stack"),
    (r"\bOur stack\b", "The stack"),
    (r"\bour database\b", "the database"),
    (r"\bOur database\b", "The database"),
    (r"\bour pipeline\b", "the pipeline"),
    (r"\bOur pipeline\b", "The pipeline"),
    (r"\bour deployment\b", "the deployment"),
    (r"\bOur deployment\b", "The deployment"),
    (r"\bWe're seeing\b", "The industry is seeing"),
    (r"\bwe're seeing\b", "the industry is seeing"),
    (r"\bwe'll\b", "developers will"),
    (r"\bWe'll\b", "Developers will"),
    (r"\bwe've\b", "developers have"),
    (r"\bWe've\b", "Developers have"),
]


def fix_bare_urls(content: str) -> str:
    """Wrap bare URLs in markdown links, skip image URLs."""
    def replacer(m):
        url = m.group(0)
        # Skip image URLs (pexels, cloudinary, etc.)
        if any(x in url.lower() for x in ["pexels.com/photos", "cloudinary", ".png", ".jpg", ".jpeg", ".gif", ".webp"]):
            return url
        domain = re.sub(r"https?://(?:www\.)?", "", url).split("/")[0]
        return f"[{domain}]({url})"
    return re.sub(r"(?<!\()(?<!\]\()https?://[^\s\)>\]\"]+", replacer, content)


def fix_voice(content: str) -> tuple[str, int]:
    """Replace generic we/our with third-person alternatives."""
    count = 0
    for pattern, replacement in VOICE_FIXES:
        new_content, n = re.subn(pattern, replacement, content)
        count += n
        content = new_content
    return content, count


def remove_filler(content: str) -> tuple[str, int]:
    """Remove filler phrases."""
    count = 0
    for pattern in FILLER_PATTERNS:
        matches = len(re.findall(pattern, content))
        if matches:
            content = re.sub(pattern, "", content)
            count += matches
    return content, count


def clean_whitespace(content: str) -> str:
    """Collapse excessive blank lines and trailing whitespace."""
    content = re.sub(r"\n{3,}", "\n\n", content)
    content = re.sub(r" +\n", "\n", content)
    content = re.sub(r"^\s+$", "", content, flags=re.MULTILINE)
    return content.strip()


async def llm_polish_excerpt(title: str, content: str) -> str:
    """Use LLM to generate a better excerpt if needed."""
    # Just get the first ~200 words as context
    words = content.split()[:200]
    excerpt_text = " ".join(words)

    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                f"{OLLAMA_URL}/api/generate",
                json={
                    "model": "llama3:latest",
                    "prompt": (
                        f"Write a 1-2 sentence excerpt/summary for this blog post. "
                        f"Newsroom journalism voice. No filler. Output ONLY the excerpt.\n\n"
                        f"Title: {title}\n"
                        f"Content start: {excerpt_text}"
                    ),
                    "stream": False,
                    "options": {"num_predict": 100, "temperature": 0.3},
                },
            )
            resp.raise_for_status()
            return resp.json().get("response", "").strip().strip('"')
    except Exception as e:
        print(f"    LLM excerpt failed: {e}")
        return ""


async def process_post(post: dict, iteration: int) -> dict:
    """Process a single post with programmatic fixes."""
    post_id = post["id"]
    title = post["title"]
    content = post.get("content", "") or ""
    original_content = content
    changes = []

    print(f"\n  [{iteration}/2] {title[:55]}")

    # Fix bare URLs
    new_content = fix_bare_urls(content)
    bare_before = len(re.findall(r"(?<!\()(?<!\]\()https?://[^\s\)>\]\"]+", content))
    bare_after = len(re.findall(r"(?<!\()(?<!\]\()https?://[^\s\)>\]\"]+", new_content))
    url_fixes = bare_before - bare_after
    if url_fixes > 0:
        changes.append(f"{url_fixes} URLs linked")
    content = new_content

    # Fix voice
    content, voice_fixes = fix_voice(content)
    if voice_fixes:
        changes.append(f"{voice_fixes} voice fixes")

    # Remove filler
    content, filler_fixes = remove_filler(content)
    if filler_fixes:
        changes.append(f"{filler_fixes} filler removed")

    # Clean whitespace
    content = clean_whitespace(content)

    # Check if content changed
    if content != original_content:
        # Push update
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.patch(
                f"{API_URL}/api/posts/{post_id}",
                headers=HEADERS,
                json={"content": content},
            )
            if resp.status_code == 200:
                status = "UPDATED"
            else:
                status = f"ERROR:{resp.status_code}"
                changes.append(f"update failed: {resp.text[:50]}")
    else:
        status = "NO_CHANGE"

    change_str = ", ".join(changes) if changes else "no changes"
    print(f"    {status}: {change_str}")

    return {
        "id": post_id,
        "title": title[:50],
        "changes": changes,
        "status": status,
        "content": content,  # Keep for iteration 2
    }


async def main():
    # Fetch fresh posts from production
    print("Fetching published posts from production...")
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(
            f"{API_URL}/api/posts?limit=50&published_only=true",
            headers=HEADERS,
        )
        resp.raise_for_status()
        data = resp.json()

    posts = data.get("posts", [])
    posts = [p for p in posts if p["id"] not in TAKEN_DOWN]
    print(f"Processing {len(posts)} posts\n")

    # ITERATION 1
    print("=" * 60)
    print("ITERATION 1: Bare URLs, voice fixes, filler removal")
    print("=" * 60)
    results1 = []
    for post in posts:
        r = await process_post(post, 1)
        results1.append(r)

    updated1 = sum(1 for r in results1 if r["status"] == "UPDATED")
    print(f"\nIteration 1 complete: {updated1}/{len(posts)} updated")

    # Re-fetch for iteration 2 (get the updated content)
    print("\nRe-fetching posts for iteration 2...")
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(
            f"{API_URL}/api/posts?limit=50&published_only=true",
            headers=HEADERS,
        )
        resp.raise_for_status()
        data = resp.json()

    posts2 = data.get("posts", [])
    posts2 = [p for p in posts2 if p["id"] not in TAKEN_DOWN]

    # ITERATION 2 — second pass catches anything missed
    print("\n" + "=" * 60)
    print("ITERATION 2: Second pass cleanup")
    print("=" * 60)
    results2 = []
    for post in posts2:
        r = await process_post(post, 2)
        results2.append(r)

    updated2 = sum(1 for r in results2 if r["status"] == "UPDATED")
    print(f"\nIteration 2 complete: {updated2}/{len(posts2)} updated")

    # SUMMARY
    print("\n" + "=" * 60)
    print("FINAL SUMMARY")
    print("=" * 60)
    all_changes = {}
    for r in results1 + results2:
        tid = r["title"]
        if tid not in all_changes:
            all_changes[tid] = []
        all_changes[tid].extend(r["changes"])

    for title, changes in all_changes.items():
        if changes:
            print(f"  {title}: {', '.join(changes)}")

    total_updates = sum(1 for v in all_changes.values() if v)
    print(f"\n  Total posts modified: {total_updates}")
    print(f"  Total posts unchanged: {len(all_changes) - total_updates}")


if __name__ == "__main__":
    asyncio.run(main())
