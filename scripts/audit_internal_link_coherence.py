#!/usr/bin/env python3
"""Audit published posts for incoherent internal-link recommendations.

Addresses Glad-Labs/poindexter#88. Two-part audit:

  1. Static phrase scan — flag any post whose content contains the
     literal "Consider exploring CadQuery" template (with or without
     trailing content) or an internal link to the CadQuery post
     (``beyond-blocks-and-lines-how-cadquery``).
  2. Tag-coherence scan — for every internal link ``/posts/<slug>`` in
     every published post, require at least one tag overlap between the
     source post and the linked post. Print any mismatches.

Output is a markdown report on stdout (redirect to a file). Nothing is
written back to the DB — deciding whether to edit published content is
a human call per the issue.

Usage (from repo root):

    DATABASE_URL=postgres://... python scripts/audit_internal_link_coherence.py

or inside the stack container:

    docker compose exec worker python /app/scripts/audit_internal_link_coherence.py
"""

from __future__ import annotations

import asyncio
import os
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

try:
    import asyncpg  # type: ignore
except ImportError:  # pragma: no cover - dev environment without asyncpg
    print("asyncpg is required", file=sys.stderr)
    raise


# Make the cofounder_agent packages importable so we can reuse the
# coherence helpers rather than reimplementing them.
_REPO_ROOT = Path(__file__).resolve().parents[1]
for _candidate in (
    _REPO_ROOT / "src" / "cofounder_agent",
    _REPO_ROOT,
):
    if str(_candidate) not in sys.path and _candidate.exists():
        sys.path.insert(0, str(_candidate))


CADQUERY_PHRASE = re.compile(r"Consider exploring\s+[^.\n]*CadQuery", re.IGNORECASE)
CADQUERY_SLUG_RE = re.compile(
    r"/posts/beyond-blocks-and-lines-how-cadquery[^\s)\"'\]]*", re.IGNORECASE
)

INTERNAL_LINK_RE = re.compile(
    r"(?:\]\(|\(|\s|^)(?:https?://[^)\s]+)?/posts/([a-z0-9][a-z0-9\-]+?)(?=[/?#\"')\]\s]|$)",
    re.IGNORECASE,
)


async def _resolve_dsn() -> str:
    dsn = os.getenv("DATABASE_URL", "")
    if dsn:
        return dsn
    # Fall back to the bootstrap helper if we're inside the repo.
    try:
        from brain.bootstrap import resolve_database_url  # type: ignore

        maybe = resolve_database_url() or ""
        if maybe:
            return maybe
    except Exception:
        pass
    raise RuntimeError(
        "DATABASE_URL not set and brain.bootstrap.resolve_database_url returned empty"
    )


async def _fetch_published(conn) -> list[dict[str, Any]]:
    rows = await conn.fetch(
        """
        SELECT id, title, slug, content
        FROM posts
        WHERE status = 'published'
          AND content IS NOT NULL
        ORDER BY published_at NULLS LAST, created_at
        """
    )
    return [dict(r) for r in rows]


async def _fetch_tag_slugs_by_post(conn) -> dict[str, set[str]]:
    rows = await conn.fetch(
        """
        SELECT pt.post_id::text AS post_id, t.slug AS tag_slug
        FROM post_tags pt
        JOIN tags t ON t.id = pt.tag_id
        """
    )
    out: dict[str, set[str]] = defaultdict(set)
    for r in rows:
        out[r["post_id"]].add(r["tag_slug"])
    return out


def _find_internal_links(content: str) -> set[str]:
    """Return the set of internal post slugs linked from ``content``."""
    out: set[str] = set()
    for m in INTERNAL_LINK_RE.finditer(content):
        slug = m.group(1)
        if slug:
            out.add(slug.lower())
    return out


def _find_cadquery_phrases(content: str) -> list[str]:
    hits: list[str] = []
    for m in CADQUERY_PHRASE.finditer(content):
        start = max(0, m.start() - 40)
        end = min(len(content), m.end() + 80)
        snippet = content[start:end].replace("\n", " ").strip()
        hits.append(snippet)
    return hits


def _find_cadquery_urls(content: str) -> list[str]:
    return [m.group(0) for m in CADQUERY_SLUG_RE.finditer(content)]


async def audit() -> int:
    dsn = await _resolve_dsn()
    conn = await asyncpg.connect(dsn)
    try:
        posts = await _fetch_published(conn)
        tags_by_post = await _fetch_tag_slugs_by_post(conn)

        # slug -> post_id (need both directions)
        slug_to_id: dict[str, str] = {p["slug"]: str(p["id"]) for p in posts}
    finally:
        await conn.close()

    cadquery_phrase_hits: list[dict[str, Any]] = []
    cadquery_url_hits: list[dict[str, Any]] = []
    incoherent_links: list[dict[str, Any]] = []
    target_counter: Counter[str] = Counter()

    for post in posts:
        content = post["content"] or ""
        post_id = str(post["id"])
        source_tags = tags_by_post.get(post_id, set())

        for snippet in _find_cadquery_phrases(content):
            cadquery_phrase_hits.append(
                {
                    "post_id": post_id,
                    "slug": post["slug"],
                    "title": post["title"],
                    "snippet": snippet,
                }
            )

        for url in _find_cadquery_urls(content):
            cadquery_url_hits.append(
                {
                    "post_id": post_id,
                    "slug": post["slug"],
                    "title": post["title"],
                    "url": url,
                }
            )

        for linked_slug in _find_internal_links(content):
            if linked_slug == post["slug"]:
                continue  # self-link
            target_counter[linked_slug] += 1
            target_id = slug_to_id.get(linked_slug)
            if not target_id:
                # Broken link — the target isn't a published post.
                incoherent_links.append(
                    {
                        "source_slug": post["slug"],
                        "source_title": post["title"],
                        "target_slug": linked_slug,
                        "reason": "target_not_published",
                        "source_tags": sorted(source_tags),
                        "target_tags": [],
                    }
                )
                continue

            target_tags = tags_by_post.get(target_id, set())
            if not source_tags or not target_tags:
                incoherent_links.append(
                    {
                        "source_slug": post["slug"],
                        "source_title": post["title"],
                        "target_slug": linked_slug,
                        "reason": "missing_tags",
                        "source_tags": sorted(source_tags),
                        "target_tags": sorted(target_tags),
                    }
                )
                continue

            if source_tags.isdisjoint(target_tags):
                incoherent_links.append(
                    {
                        "source_slug": post["slug"],
                        "source_title": post["title"],
                        "target_slug": linked_slug,
                        "target_title": next(
                            (
                                p["title"]
                                for p in posts
                                if p["slug"] == linked_slug
                            ),
                            "",
                        ),
                        "reason": "no_tag_overlap",
                        "source_tags": sorted(source_tags),
                        "target_tags": sorted(target_tags),
                    }
                )

    _emit_report(
        posts=posts,
        cadquery_phrase_hits=cadquery_phrase_hits,
        cadquery_url_hits=cadquery_url_hits,
        incoherent_links=incoherent_links,
        target_counter=target_counter,
    )
    return 0


def _emit_report(
    *,
    posts: list[dict[str, Any]],
    cadquery_phrase_hits: list[dict[str, Any]],
    cadquery_url_hits: list[dict[str, Any]],
    incoherent_links: list[dict[str, Any]],
    target_counter: Counter[str],
) -> None:
    print("# Internal-link coherence audit (GH-88)")
    print()
    print(f"- Published posts scanned: **{len(posts)}**")
    print(f"- \"Consider exploring CadQuery\" phrase hits: **{len(cadquery_phrase_hits)}**")
    print(f"- CadQuery-URL hits: **{len(cadquery_url_hits)}**")
    print(f"- Tag-incoherent internal links: **{len(incoherent_links)}**")
    print()

    print("## Top link targets (corpus-wide)")
    print()
    print("| target slug | inbound links |")
    print("| --- | --- |")
    for slug, count in target_counter.most_common(15):
        print(f"| `{slug}` | {count} |")
    print()

    print("## \"Consider exploring CadQuery\" phrase hits")
    print()
    if not cadquery_phrase_hits:
        print("_None._")
    else:
        for hit in cadquery_phrase_hits:
            print(f"- **{hit['title']}** (`{hit['slug']}`, id `{hit['post_id']}`)")
            print(f"    > {hit['snippet']}")
    print()

    print("## CadQuery internal-link hits (by URL)")
    print()
    if not cadquery_url_hits:
        print("_None._")
    else:
        for hit in cadquery_url_hits:
            print(f"- **{hit['title']}** (`{hit['slug']}`): `{hit['url']}`")
    print()

    print("## Tag-incoherent internal links")
    print()
    if not incoherent_links:
        print("_None._")
    else:
        print("| source post | target slug | reason | source tags | target tags |")
        print("| --- | --- | --- | --- | --- |")
        for row in incoherent_links:
            st = ", ".join(row["source_tags"]) or "-"
            tt = ", ".join(row["target_tags"]) or "-"
            print(
                f"| {row['source_title']} (`{row['source_slug']}`) "
                f"| `{row['target_slug']}` | {row['reason']} | {st} | {tt} |"
            )
    print()


if __name__ == "__main__":
    sys.exit(asyncio.run(audit()))
