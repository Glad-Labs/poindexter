#!/usr/bin/env python3
"""Sync premium prompt templates from the glad-labs-prompts repo into the
running Postgres database's `prompt_templates` table.

Root cause: there are two sources of truth — the newer, richer
blog_generation.yaml in the glad-labs-prompts repo and an older
db_prompt_templates.json that gets imported via import-premium-prompts.py.
They drifted. Production was running the older version, which didn't ban
generic section titles like "Conclusion" / "Introduction" / "Summary",
so the LLM kept producing them.

This script reads the YAML directly and upserts each template by key.
It's intentionally narrow: it only writes, never deletes. Safe to run
repeatedly.

Usage:
    python scripts/sync-premium-prompts.py
    python scripts/sync-premium-prompts.py --dry-run
    python scripts/sync-premium-prompts.py --prompts-dir ../glad-labs-prompts
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    print("PyYAML is required: pip install pyyaml", file=sys.stderr)
    sys.exit(2)

try:
    import psycopg2
except ImportError:
    print("psycopg2 is required: pip install psycopg2-binary", file=sys.stderr)
    sys.exit(2)


DEFAULT_PROMPTS_DIR = Path(__file__).resolve().parent.parent.parent / "glad-labs-prompts"
DEFAULT_DB_URL = os.environ.get(
    "LOCAL_DATABASE_URL",
    "postgresql://poindexter:poindexter-brain-local@localhost:15432/poindexter_brain",
)

# The YAML files in the premium repo that contain prompt_templates-shaped
# entries. Each entry needs `key`, `category`, `template`. Description and
# notes are optional.
PROMPT_YAML_FILES = [
    "blog_generation.yaml",
    "content_qa.yaml",
    "image_generation.yaml",
    "research.yaml",
    "seo_metadata.yaml",
    "social_media.yaml",
    "system.yaml",
    "tasks.yaml",
]


def load_prompts(prompts_dir: Path) -> list[dict]:
    out: list[dict] = []
    for name in PROMPT_YAML_FILES:
        path = prompts_dir / name
        if not path.exists():
            print(f"  SKIP {name} (not found)")
            continue
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        if not isinstance(data, list):
            print(f"  SKIP {name} (not a list)")
            continue
        for entry in data:
            if not isinstance(entry, dict):
                continue
            if "key" not in entry or "template" not in entry:
                continue
            out.append(
                {
                    "key": entry["key"],
                    "category": entry.get("category", entry["key"].split(".", 1)[0]),
                    "description": entry.get("description", ""),
                    "template": entry["template"],
                    "version": entry.get("version", 2),
                }
            )
    return out


def upsert(cur, prompts: list[dict]) -> tuple[int, int]:
    """Upsert each prompt. Returns (updated, inserted)."""
    updated = 0
    inserted = 0
    for p in prompts:
        cur.execute(
            "SELECT template FROM prompt_templates WHERE key = %s",
            (p["key"],),
        )
        row = cur.fetchone()
        if row is None:
            cur.execute(
                """
                INSERT INTO prompt_templates (key, category, description, template, is_active, version, created_at, updated_at)
                VALUES (%s, %s, %s, %s, true, %s, NOW(), NOW())
                """,
                (p["key"], p["category"], p["description"], p["template"], p["version"]),
            )
            inserted += 1
            print(f"  INSERT {p['key']} ({len(p['template'])} chars)")
        elif row[0] != p["template"]:
            cur.execute(
                """
                UPDATE prompt_templates
                SET category = %s, description = %s, template = %s,
                    version = %s, updated_at = NOW()
                WHERE key = %s
                """,
                (p["category"], p["description"], p["template"], p["version"], p["key"]),
            )
            updated += 1
            old_len = len(row[0])
            new_len = len(p["template"])
            delta = new_len - old_len
            sign = "+" if delta >= 0 else ""
            print(f"  UPDATE {p['key']} ({old_len} -> {new_len} chars, {sign}{delta})")
        else:
            print(f"  SAME   {p['key']}")
    return updated, inserted


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--prompts-dir",
        type=Path,
        default=DEFAULT_PROMPTS_DIR,
        help=f"Path to glad-labs-prompts repo (default: {DEFAULT_PROMPTS_DIR})",
    )
    parser.add_argument(
        "--database-url",
        default=DEFAULT_DB_URL,
        help="Postgres connection URL",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show planned changes without writing",
    )
    args = parser.parse_args()

    if not args.prompts_dir.exists():
        print(f"ERROR: prompts dir not found: {args.prompts_dir}", file=sys.stderr)
        return 1

    print(f"Loading prompts from: {args.prompts_dir}")
    prompts = load_prompts(args.prompts_dir)
    print(f"Found {len(prompts)} prompt entries")

    if not prompts:
        print("Nothing to sync.")
        return 0

    conn = psycopg2.connect(args.database_url)
    try:
        cur = conn.cursor()
        print(f"\n{'DRY RUN — ' if args.dry_run else ''}Syncing to {args.database_url.split('@')[-1]}")
        print("-" * 60)
        updated, inserted = upsert(cur, prompts)
        print("-" * 60)
        print(f"Updated: {updated}  Inserted: {inserted}  Unchanged: {len(prompts) - updated - inserted}")

        if args.dry_run:
            print("Rolling back (--dry-run)")
            conn.rollback()
        else:
            conn.commit()
            print("Committed.")
    finally:
        conn.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())
