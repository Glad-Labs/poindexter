#!/usr/bin/env python3
"""
Import Premium Prompts — loads prompt templates from JSON into the database.

Usage:
    python scripts/import-premium-prompts.py prompts.json
    python scripts/import-premium-prompts.py --database-url postgresql://... prompts.json
    python scripts/import-premium-prompts.py --validate-only prompts.json

The JSON file should be an array of objects with keys:
    key, category, description, template, variables, version

Prompts are upserted — existing prompts with the same key are updated.
New prompts are inserted. No prompts are deleted.
"""

import argparse
import json
import os
import sys


def main():
    parser = argparse.ArgumentParser(description="Import premium prompt templates into the database")
    parser.add_argument("json_file", help="Path to the prompt templates JSON file")
    parser.add_argument("--database-url", default=None, help="PostgreSQL connection URL")
    parser.add_argument("--validate-only", action="store_true", help="Check JSON format without importing")
    parser.add_argument("--quiet", action="store_true", help="Minimal output")
    args = parser.parse_args()

    # Load JSON
    try:
        with open(args.json_file, "r", encoding="utf-8") as f:
            prompts = json.load(f)
    except (json.JSONDecodeError, FileNotFoundError) as e:
        print(f"Error reading {args.json_file}: {e}", file=sys.stderr)
        sys.exit(1)

    if not isinstance(prompts, list):
        print("Error: JSON must be an array of prompt objects", file=sys.stderr)
        sys.exit(1)

    # Validate structure
    required_fields = {"key", "template"}
    errors = []
    for i, p in enumerate(prompts):
        missing = required_fields - set(p.keys())
        if missing:
            errors.append(f"  Prompt {i}: missing {missing}")
        if not p.get("template", "").strip():
            errors.append(f"  Prompt {i} ({p.get('key', '?')}): empty template")

    if errors:
        print(f"Validation errors in {args.json_file}:")
        for e in errors:
            print(e)
        sys.exit(1)

    # Summary
    categories = {}
    for p in prompts:
        cat = p.get("category", "uncategorized")
        categories[cat] = categories.get(cat, 0) + 1

    if not args.quiet:
        print(f"Loaded {len(prompts)} prompts from {args.json_file}")
        for cat, count in sorted(categories.items()):
            print(f"  {cat}: {count}")

    if args.validate_only:
        print("Validation passed. Use without --validate-only to import.")
        sys.exit(0)

    # Determine database URL
    db_url = args.database_url or os.getenv("DATABASE_URL")
    if not db_url:
        # Default to local brain DB
        pg_pass = os.getenv("LOCAL_POSTGRES_PASSWORD", "gladlabs-brain-local")
        pg_user = os.getenv("LOCAL_POSTGRES_USER", "gladlabs")
        pg_db = os.getenv("LOCAL_POSTGRES_DB", "gladlabs_brain")
        db_url = f"postgresql://{pg_user}:{pg_pass}@localhost:5433/{pg_db}"

    # Try psycopg2 first, fall back to psql CLI
    try:
        import psycopg2
        _import_with_psycopg2(db_url, prompts, args.quiet)
    except ImportError:
        if not args.quiet:
            print("psycopg2 not available, falling back to psql CLI...")
        _import_with_psql(db_url, prompts, args.quiet)


def _import_with_psycopg2(db_url, prompts, quiet):
    import psycopg2

    # Redact password for display
    display_url = db_url.split("@")[-1] if "@" in db_url else db_url

    conn = psycopg2.connect(db_url)
    cur = conn.cursor()

    inserted = 0
    updated = 0

    for p in prompts:
        cur.execute("""
            INSERT INTO prompt_templates (key, category, description, template, variables, version, is_active)
            VALUES (%s, %s, %s, %s, %s, %s, true)
            ON CONFLICT (key) DO UPDATE SET
                template = EXCLUDED.template,
                description = EXCLUDED.description,
                category = EXCLUDED.category,
                variables = EXCLUDED.variables,
                version = EXCLUDED.version,
                updated_at = NOW()
            RETURNING (xmax = 0) as is_insert
        """, (
            p["key"],
            p.get("category", "general"),
            p.get("description", ""),
            p["template"],
            p.get("variables", ""),
            p.get("version", 1),
        ))
        row = cur.fetchone()
        if row and row[0]:
            inserted += 1
        else:
            updated += 1

    conn.commit()
    cur.close()
    conn.close()

    if not quiet:
        print(f"\nImported to {display_url}:")
        print(f"  {inserted} new prompts inserted")
        print(f"  {updated} existing prompts updated")
        print(f"  {len(prompts)} total")
        print("\nRestart the worker to load new prompts:")
        print("  docker compose -f docker-compose.local.yml restart worker")


def _import_with_psql(db_url, prompts, quiet):
    import subprocess
    import tempfile

    # Build SQL
    sql_lines = []
    for p in prompts:
        key = p["key"].replace("'", "''")
        cat = p.get("category", "general").replace("'", "''")
        desc = p.get("description", "").replace("'", "''")
        tpl = p["template"].replace("'", "''")
        variables = p.get("variables", "").replace("'", "''")
        version = p.get("version", 1)

        sql_lines.append(f"""
INSERT INTO prompt_templates (key, category, description, template, variables, version, is_active)
VALUES ('{key}', '{cat}', '{desc}', '{tpl}', '{variables}', {version}, true)
ON CONFLICT (key) DO UPDATE SET
    template = EXCLUDED.template,
    description = EXCLUDED.description,
    category = EXCLUDED.category,
    variables = EXCLUDED.variables,
    version = EXCLUDED.version,
    updated_at = NOW();""")

    sql = "\n".join(sql_lines)

    with tempfile.NamedTemporaryFile(mode="w", suffix=".sql", delete=False, encoding="utf-8") as f:
        f.write(sql)
        tmp_path = f.name

    try:
        result = subprocess.run(
            ["psql", db_url, "-f", tmp_path],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            print(f"psql error: {result.stderr}", file=sys.stderr)
            sys.exit(1)
        if not quiet:
            print(f"Imported {len(prompts)} prompts via psql")
            print("\nRestart the worker to load new prompts:")
            print("  docker compose -f docker-compose.local.yml restart worker")
    finally:
        os.unlink(tmp_path)


if __name__ == "__main__":
    main()
