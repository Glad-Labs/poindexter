#!/usr/bin/env python3
"""
Migration script to fix corrupted seo_keywords in the posts table.

The bug: seo_keywords were stored as character-separated JSON arrays instead of
comma-separated keyword strings.

Example of corrupted data:
  - Stored: '[,",w,h,a,l,e,",,, ,",h,u,m,a,n,"...]'
  - Should be: 'whale, human, whales, skill, internet'

This script:
1. Identifies all posts with corrupted seo_keywords (starting with '[' or containing ', ')
2. Attempts to parse the original JSON array to recover keywords
3. Converts to proper comma-separated format
4. Updates the database with corrected values
"""

import json
import re
import asyncio
import asyncpg
import sys
from typing import Optional


async def parse_corrupted_seo_keywords(corrupted: str) -> str:
    """
    Attempt to recover keywords from corrupted character-separated format.

    Args:
        corrupted: String like '[,",w,h,a,l,e,",,, ,",h,u,m,a,n,"...]'

    Returns:
        Corrected comma-separated string like 'whale, human, whales, skill'
    """
    if not corrupted or not isinstance(corrupted, str):
        return ""

    # If it looks like character-separated JSON array
    if corrupted.startswith("["):
        # Try to extract quoted strings
        # Pattern: ," (quotes after commas) indicate start of a keyword
        keywords = []
        i = 0
        current_keyword = []

        while i < len(corrupted):
            char = corrupted[i]

            # Found opening quote - this starts a keyword
            if char == '"' and i + 1 < len(corrupted):
                # Skip the opening quote
                i += 1
                # Collect characters until closing quote
                while i < len(corrupted) and corrupted[i] != '"':
                    if corrupted[i] not in [',', ' ', '[', ']']:
                        current_keyword.append(corrupted[i])
                    i += 1

                # If we collected a keyword, add it
                if current_keyword:
                    keyword = ''.join(current_keyword).strip()
                    if keyword:
                        keywords.append(keyword)
                    current_keyword = []

            i += 1

        if keywords:
            return ", ".join(keywords)

    # If it's already somewhat valid, try JSON parsing
    try:
        if corrupted.strip().startswith("["):
            parsed = json.loads(corrupted)
            if isinstance(parsed, list):
                return ", ".join(str(kw).strip() for kw in parsed if kw)
    except (json.JSONDecodeError, TypeError):
        pass

    # Return as-is if we can't repair it
    return corrupted.strip()


async def fix_seo_keywords(db_url: str, dry_run: bool = True):
    """
    Fix corrupted seo_keywords in the posts table.

    Args:
        db_url: PostgreSQL connection string
        dry_run: If True, show changes without applying them
    """
    try:
        conn = await asyncpg.connect(db_url)

        # Find posts with corrupted seo_keywords
        corrupted_posts = await conn.fetch("""
            SELECT id, title, slug, seo_keywords
            FROM posts
            WHERE seo_keywords IS NOT NULL
            AND seo_keywords != ''
            AND (
                seo_keywords LIKE '[,%' OR
                seo_keywords LIKE '%,",%' OR
                SUBSTRING(seo_keywords, 1, 1) = '['
            )
            ORDER BY created_at DESC
        """)

        print(f"Found {len(corrupted_posts)} posts with potentially corrupted seo_keywords")

        if not corrupted_posts:
            print("No corrupted entries found!")
            await conn.close()
            return

        fixed_count = 0

        for post in corrupted_posts:
            post_id = post["id"]
            title = post["title"][:50]
            corrupted = post["seo_keywords"]
            fixed = await parse_corrupted_seo_keywords(corrupted)

            if fixed != corrupted:
                print(f"\nPost ID: {post_id}")
                print(f"  Title: {title}")
                print(f"  Original: {corrupted[:80]}...")
                print(f"  Fixed: {fixed[:80]}")

                if not dry_run:
                    try:
                        await conn.execute(
                            "UPDATE posts SET seo_keywords = $1 WHERE id = $2",
                            fixed,
                            post_id,
                        )
                        fixed_count += 1
                        print(f"  [UPDATED]")
                    except Exception as e:
                        print(f"  [ERROR] Failed to update: {e}")

        await conn.close()

        if dry_run:
            print(f"\n[DRY RUN] Would fix {fixed_count} posts")
            print("Run with --apply flag to apply changes")
        else:
            print(f"\n[SUCCESS] Fixed {fixed_count} posts")

    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    import os

    db_url = os.getenv(
        "DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/glad_labs_dev"
    )

    # Check for --apply flag
    apply_changes = "--apply" in sys.argv

    if not apply_changes:
        print("[DRY RUN MODE] No changes will be applied")
        print("Run with --apply flag to fix the database")
        print()

    asyncio.run(fix_seo_keywords(db_url, dry_run=not apply_changes))
