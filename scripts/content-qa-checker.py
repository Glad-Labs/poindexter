#!/usr/bin/env python3
"""
Content QA Checker — Periodic quality audit of all published posts.

Connects to the Railway cloud DB, fetches every published post,
runs quality checks (title, content, SEO, links, duplicates, red flags),
scores each post 0-100, and writes a report.

Usage:
    python scripts/content-qa-checker.py

Environment:
    DATABASE_URL — Railway PostgreSQL connection string
    (Falls back to hardcoded Railway URL if not set)

Output:
    ~/.gladlabs/content-qa-report.json  (full report)
    ~/.gladlabs/content-qa-checker.log  (log file)
    audit_log table entry               (summary row)

Designed to run weekly via Windows Scheduled Task.
"""

import asyncio
import json
import logging
import os
import re
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

import asyncpg
import httpx

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
GLADLABS_DIR = Path.home() / ".gladlabs"
REPORT_PATH = GLADLABS_DIR / "content-qa-report.json"
LOG_PATH = GLADLABS_DIR / "content-qa-checker.log"

# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------
DATABASE_URL = os.getenv("CLOUD_DATABASE_URL") or os.getenv("DATABASE_URL", "")

# ---------------------------------------------------------------------------
# Logging — file + console
# ---------------------------------------------------------------------------
GLADLABS_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(LOG_PATH, encoding="utf-8"),
    ],
)
logger = logging.getLogger("content-qa-checker")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
TITLE_MIN = 30
TITLE_MAX = 60
CONTENT_MIN_WORDS = 500
SEO_DESC_MIN = 120
SEO_DESC_MAX = 160
SIMILARITY_THRESHOLD = 0.85
LINK_TIMEOUT = 5.0
MAX_CONCURRENT_LINKS = 10

# Regex patterns
URL_PATTERN = re.compile(r'https?://[^\s\)\]"\'<>,]+')
PERCENT_PATTERN = re.compile(r'\b\d+(\.\d+)?%')
ACCORDING_PATTERN = re.compile(r'according to\b', re.IGNORECASE)
# Named person with title: "CEO John Smith", "Dr. Jane Doe", etc.
PERSON_TITLE_PATTERN = re.compile(
    r'\b(CEO|CTO|CFO|COO|VP|Director|Professor|Dr\.|President|Founder|Co-founder)'
    r'\s+[A-Z][a-z]+\s+[A-Z][a-z]+',
)
# "Company X reported/announced/claimed"
COMPANY_CLAIM_PATTERN = re.compile(
    r'\b[A-Z][a-z]+(?:\s[A-Z][a-z]+)?\s+(?:reported|announced|claimed|revealed|confirmed)\b'
)


# ===================================================================
# Helpers
# ===================================================================

def extract_urls(text: str) -> list[str]:
    """Extract all HTTP(S) URLs from markdown/HTML content."""
    urls = URL_PATTERN.findall(text)
    # Clean trailing punctuation
    cleaned = []
    for u in urls:
        u = u.rstrip(".,;:!?)")
        if u.endswith("'") or u.endswith('"'):
            u = u[:-1]
        cleaned.append(u)
    return list(dict.fromkeys(cleaned))  # dedupe, preserve order


def word_count(text: str) -> int:
    """Count words in text after stripping markdown/HTML."""
    stripped = re.sub(r'<[^>]+>', '', text)
    stripped = re.sub(r'!\[.*?\]\(.*?\)', '', stripped)  # images
    stripped = re.sub(r'\[([^\]]*)\]\([^\)]*\)', r'\1', stripped)  # links -> text
    stripped = re.sub(r'[#*_~`>]', '', stripped)
    return len(stripped.split())


def title_similarity(a: str, b: str) -> float:
    """SequenceMatcher ratio between two titles."""
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


def has_nearby_url(text: str, match_start: int, window: int = 300) -> bool:
    """Check if there is a URL within `window` chars of a match position."""
    region = text[max(0, match_start - window):match_start + window]
    return bool(URL_PATTERN.search(region))


# ===================================================================
# Individual checks — each returns list of issue dicts
# ===================================================================

def check_title_diversity(post: dict, all_titles: list[str]) -> list[dict]:
    issues = []
    title = post["title"] or ""

    if title.startswith("The "):
        issues.append({
            "check": "title_diversity",
            "severity": "warning",
            "message": f"Title starts with 'The': \"{title[:60]}\"",
        })

    # Check duplicate prefix (first 3 words) with other posts
    prefix = " ".join(title.split()[:3]).lower()
    dupes = [t for t in all_titles if t != title and " ".join(t.split()[:3]).lower() == prefix]
    if dupes:
        issues.append({
            "check": "title_diversity",
            "severity": "warning",
            "message": f"Shares prefix \"{prefix}\" with {len(dupes)} other post(s)",
        })

    return issues


def check_title_length(post: dict) -> list[dict]:
    title = post["title"] or ""
    length = len(title)
    if length < TITLE_MIN:
        return [{"check": "title_length", "severity": "warning",
                 "message": f"Title too short ({length} chars, optimal {TITLE_MIN}-{TITLE_MAX})"}]
    if length > TITLE_MAX:
        return [{"check": "title_length", "severity": "info",
                 "message": f"Title long ({length} chars, optimal {TITLE_MIN}-{TITLE_MAX})"}]
    return []


def check_content_length(post: dict) -> list[dict]:
    content = post["content"] or ""
    wc = word_count(content)
    if wc < CONTENT_MIN_WORDS:
        return [{"check": "content_length", "severity": "error",
                 "message": f"Content too short ({wc} words, minimum {CONTENT_MIN_WORDS})"}]
    return []


def check_uncited_claims(post: dict) -> list[dict]:
    content = post["content"] or ""
    issues = []

    for m in PERCENT_PATTERN.finditer(content):
        if not has_nearby_url(content, m.start()):
            issues.append({
                "check": "uncited_claim",
                "severity": "warning",
                "message": f"Percentage \"{m.group()}\" without nearby citation",
            })

    for m in ACCORDING_PATTERN.finditer(content):
        if not has_nearby_url(content, m.start()):
            issues.append({
                "check": "uncited_claim",
                "severity": "warning",
                "message": f"\"according to\" without nearby link (pos {m.start()})",
            })

    return issues


def check_hallucination_flags(post: dict) -> list[dict]:
    content = post["content"] or ""
    issues = []

    for m in PERSON_TITLE_PATTERN.finditer(content):
        issues.append({
            "check": "hallucination_risk",
            "severity": "warning",
            "message": f"Named person with title: \"{m.group()}\" — verify real",
        })

    for m in COMPANY_CLAIM_PATTERN.finditer(content):
        issues.append({
            "check": "hallucination_risk",
            "severity": "info",
            "message": f"Specific company claim: \"{m.group()}\" — verify accuracy",
        })

    return issues


def check_seo(post: dict) -> list[dict]:
    issues = []

    if not post.get("seo_title"):
        issues.append({"check": "seo", "severity": "error",
                       "message": "Missing seo_title"})

    desc = post.get("seo_description") or ""
    if not desc:
        issues.append({"check": "seo", "severity": "error",
                       "message": "Missing seo_description"})
    elif len(desc) < SEO_DESC_MIN:
        issues.append({"check": "seo", "severity": "warning",
                       "message": f"seo_description too short ({len(desc)} chars, optimal {SEO_DESC_MIN}-{SEO_DESC_MAX})"})
    elif len(desc) > SEO_DESC_MAX:
        issues.append({"check": "seo", "severity": "warning",
                       "message": f"seo_description too long ({len(desc)} chars, optimal {SEO_DESC_MIN}-{SEO_DESC_MAX})"})

    if not post.get("seo_keywords"):
        issues.append({"check": "seo", "severity": "warning",
                       "message": "Missing seo_keywords"})

    return issues


def check_duplicate_content(post: dict, all_posts: list[dict]) -> list[dict]:
    title = post["title"] or ""
    issues = []

    for other in all_posts:
        if other["id"] == post["id"]:
            continue
        other_title = other["title"] or ""
        sim = title_similarity(title, other_title)
        if sim >= SIMILARITY_THRESHOLD:
            issues.append({
                "check": "duplicate_content",
                "severity": "error",
                "message": f"Title similarity {sim:.0%} with \"{other_title[:50]}\"",
            })

    return issues


# ===================================================================
# Async checks (network)
# ===================================================================

async def check_featured_image(post: dict, client: httpx.AsyncClient) -> list[dict]:
    url = post.get("featured_image_url")
    if not url:
        return [{"check": "featured_image", "severity": "warning",
                 "message": "No featured_image_url set"}]
    try:
        r = await client.head(url, timeout=LINK_TIMEOUT, follow_redirects=True)
        if r.status_code != 200:
            return [{"check": "featured_image", "severity": "error",
                     "message": f"Featured image returned HTTP {r.status_code}: {url[:80]}"}]
    except Exception as e:
        return [{"check": "featured_image", "severity": "error",
                 "message": f"Featured image unreachable: {type(e).__name__}: {url[:80]}"}]
    return []


async def check_broken_links(post: dict, client: httpx.AsyncClient,
                              semaphore: asyncio.Semaphore) -> list[dict]:
    content = post["content"] or ""
    urls = extract_urls(content)
    if not urls:
        return []

    issues = []

    async def probe(url: str):
        async with semaphore:
            try:
                r = await client.head(url, timeout=LINK_TIMEOUT, follow_redirects=True)
                if r.status_code >= 400:
                    # Retry with GET — some servers reject HEAD
                    r2 = await client.get(url, timeout=LINK_TIMEOUT, follow_redirects=True)
                    if r2.status_code >= 400:
                        issues.append({
                            "check": "broken_link",
                            "severity": "error",
                            "message": f"HTTP {r2.status_code}: {url[:100]}",
                        })
            except Exception as e:
                issues.append({
                    "check": "broken_link",
                    "severity": "error",
                    "message": f"{type(e).__name__}: {url[:100]}",
                })

    await asyncio.gather(*(probe(u) for u in urls))
    return issues


# ===================================================================
# Scoring
# ===================================================================

SEVERITY_WEIGHTS = {"error": 10, "warning": 5, "info": 2}


def score_post(issues: list[dict]) -> int:
    """Score 0-100; starts at 100, deducts per issue."""
    penalty = sum(SEVERITY_WEIGHTS.get(i["severity"], 0) for i in issues)
    return max(0, 100 - penalty)


# ===================================================================
# Audit log
# ===================================================================

async def write_audit_log(pool: asyncpg.Pool, summary: dict):
    """Insert a summary entry into the audit_log table."""
    try:
        await pool.execute(
            """
            INSERT INTO audit_log (event_type, source, details, severity)
            VALUES ($1, $2, $3, $4)
            """,
            "content_qa_audit",
            "content-qa-checker",
            json.dumps(summary),
            "info",
        )
        logger.info("Audit log entry written.")
    except Exception as e:
        logger.warning("Failed to write audit_log (table may not exist on Railway): %s", e)


# ===================================================================
# Main
# ===================================================================

async def main():
    logger.info("Content QA Checker starting...")
    logger.info("Database: %s...%s", DATABASE_URL[:30], DATABASE_URL[-15:])

    pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=3)
    start_time = time.time()

    try:
        # ---- Fetch posts ----
        rows = await pool.fetch(
            """
            SELECT id, title, slug, content, excerpt,
                   featured_image_url, seo_title, seo_description, seo_keywords,
                   status, published_at
            FROM posts
            WHERE status = 'published'
            ORDER BY published_at DESC
            """
        )
        logger.info("Found %d published posts.", len(rows))

        if not rows:
            logger.info("Nothing to audit. Exiting.")
            return

        posts: list[dict[str, Any]] = [dict(r) for r in rows]
        all_titles = [p["title"] or "" for p in posts]

        # ---- Run checks ----
        semaphore = asyncio.Semaphore(MAX_CONCURRENT_LINKS)
        results = []

        async with httpx.AsyncClient(
            headers={"User-Agent": "GladLabs-ContentQA/1.0"},
            follow_redirects=True,
        ) as client:
            for i, post in enumerate(posts):
                post_id = str(post["id"])
                title = (post["title"] or "(untitled)")[:70]
                logger.info("  [%d/%d] %s — %s", i + 1, len(posts), post_id[:8], title)

                issues: list[dict] = []

                # Sync checks
                issues.extend(check_title_diversity(post, all_titles))
                issues.extend(check_title_length(post))
                issues.extend(check_content_length(post))
                issues.extend(check_uncited_claims(post))
                issues.extend(check_hallucination_flags(post))
                issues.extend(check_seo(post))
                issues.extend(check_duplicate_content(post, posts))

                # Async checks
                issues.extend(await check_featured_image(post, client))
                issues.extend(await check_broken_links(post, client, semaphore))

                post_score = score_post(issues)

                results.append({
                    "post_id": post_id,
                    "title": post["title"],
                    "slug": post["slug"],
                    "published_at": post["published_at"].isoformat() if post.get("published_at") else None,
                    "score": post_score,
                    "issue_count": len(issues),
                    "issues": issues,
                })

                if issues:
                    for iss in issues:
                        lvl = {"error": logging.ERROR, "warning": logging.WARNING}.get(
                            iss["severity"], logging.INFO
                        )
                        logger.log(lvl, "    [%s] %s", iss["check"], iss["message"])
                else:
                    logger.info("    No issues found.")

        # ---- Build report ----
        elapsed = time.time() - start_time
        total_issues = sum(r["issue_count"] for r in results)
        avg_score = sum(r["score"] for r in results) / len(results) if results else 0

        error_count = sum(
            1 for r in results for i in r["issues"] if i["severity"] == "error"
        )
        warning_count = sum(
            1 for r in results for i in r["issues"] if i["severity"] == "warning"
        )
        info_count = sum(
            1 for r in results for i in r["issues"] if i["severity"] == "info"
        )

        # Issue breakdown by check type
        check_counts = Counter(
            i["check"] for r in results for i in r["issues"]
        )

        summary = {
            "total_posts": len(posts),
            "average_score": round(avg_score, 1),
            "total_issues": total_issues,
            "errors": error_count,
            "warnings": warning_count,
            "info": info_count,
            "elapsed_seconds": round(elapsed, 2),
            "check_breakdown": dict(check_counts.most_common()),
        }

        report = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "summary": summary,
            "posts": sorted(results, key=lambda r: r["score"]),  # worst first
        }

        # ---- Save report ----
        GLADLABS_DIR.mkdir(parents=True, exist_ok=True)
        REPORT_PATH.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
        logger.info("Report saved to %s", REPORT_PATH)

        # ---- Write audit log ----
        await write_audit_log(pool, summary)

        # ---- Print summary ----
        logger.info("")
        logger.info("=" * 64)
        logger.info("  CONTENT QA REPORT")
        logger.info("=" * 64)
        logger.info("  Posts audited:     %d", len(posts))
        logger.info("  Average score:     %.1f / 100", avg_score)
        logger.info("  Total issues:      %d", total_issues)
        logger.info("    Errors:          %d", error_count)
        logger.info("    Warnings:        %d", warning_count)
        logger.info("    Info:            %d", info_count)
        logger.info("  Scan time:         %.1fs", elapsed)
        logger.info("  Report:            %s", REPORT_PATH)
        logger.info("  Log:               %s", LOG_PATH)
        logger.info("-" * 64)

        if check_counts:
            logger.info("  Issue breakdown:")
            for check_name, count in check_counts.most_common():
                logger.info("    %-25s %d", check_name, count)

        logger.info("-" * 64)

        # Bottom 5 posts
        worst = sorted(results, key=lambda r: r["score"])[:5]
        if worst:
            logger.info("  Lowest-scoring posts:")
            for r in worst:
                logger.info(
                    "    %3d/100  %s",
                    r["score"],
                    (r["title"] or "(untitled)")[:55],
                )

        logger.info("=" * 64)

    finally:
        await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
