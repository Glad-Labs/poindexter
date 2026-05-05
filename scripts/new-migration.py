#!/usr/bin/env python3
"""Generate a new migration file with a UTC timestamp prefix.

Glad-Labs/poindexter#378 — adopt timestamp prefixes for new migrations
so parallel-PR contributors (humans, agents) can't collide on the same
integer prefix.

USAGE
    python scripts/new-migration.py "<slug describing the change>"
    python scripts/new-migration.py "add writer self review settings"

The generated filename looks like:
    20260505_124530_add_writer_self_review_settings.py

OPTIONS
    --interface {pool,conn}   Pick the runner interface (default: pool).
                              `pool` writes ``async def up(pool):`` (Convention A).
                              `conn` writes ``async def run_migration(conn):``
                              (Convention B, legacy).
    --dry-run                 Print the would-be path and template; don't write.
    --force                   Overwrite the target file if it already exists.

The script writes to ``src/cofounder_agent/services/migrations/`` and
prints the absolute path of the created file. After it returns, fill
in the SQL inside ``up()``/``down()``, run ``python scripts/ci/migrations_smoke.py``
locally against a fresh Postgres, and open a PR.

Refer to ``docs/operations/migrations.md`` for the full convention.
"""

from __future__ import annotations

import argparse
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS_DIR = REPO_ROOT / "src" / "cofounder_agent" / "services" / "migrations"

_SLUG_INVALID = re.compile(r"[^a-z0-9_]+")
_TEMPLATE_POOL = '''"""Migration {timestamp}: {description}

ISSUE: Glad-Labs/poindexter#TODO   (replace with the real issue)

Background — what is this migration for? Why is it being added?
What problem does it solve? Two or three sentences. Future readers
(humans + LLMs) will use this docstring to understand intent without
diffing every SQL statement.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def up(pool) -> None:
    """Apply the migration.

    Use ``IF NOT EXISTS`` / ``ON CONFLICT DO NOTHING`` so the body is
    safe to re-run even though the runner records each migration after
    a successful apply. See ``docs/operations/migrations.md``.
    """
    async with pool.acquire() as conn:
        await conn.execute(
            """
            -- TODO: write the SQL for this migration
            """
        )
        logger.info("Migration {slug}: applied")


async def down(pool) -> None:
    """Revert the migration. Optional but preferred for new migrations.

    Drop / remove only what ``up()`` added. If the change is one-way
    (e.g., a backfill), document that here and make this a no-op.
    """
    async with pool.acquire() as conn:
        await conn.execute(
            """
            -- TODO: write the rollback SQL, or leave a no-op + comment
            """
        )
        logger.info("Migration {slug} down: reverted")
'''

_TEMPLATE_CONN = '''"""Migration {timestamp}: {description}

ISSUE: Glad-Labs/poindexter#TODO   (replace with the real issue)

Background — what is this migration for? Why is it being added?
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def run_migration(conn) -> None:
    """Apply the migration (connection-based runner — legacy interface)."""
    await conn.execute(
        """
        -- TODO: write the SQL for this migration
        """
    )
    logger.info("Migration {slug}: applied")


async def rollback_migration(conn) -> None:
    """Revert the migration."""
    await conn.execute(
        """
        -- TODO: write the rollback SQL
        """
    )
    logger.info("Migration {slug} down: reverted")
'''


def _slugify(raw: str) -> str:
    """Normalise the user-supplied description into a filename slug.

    >>> _slugify("Add writer self-review settings!")
    'add_writer_self_review_settings'
    """
    lowered = raw.strip().lower()
    # Collapse separators (spaces, hyphens, etc.) to underscores; drop
    # punctuation we can't represent in a filename.
    underscored = re.sub(r"[\s\-]+", "_", lowered)
    cleaned = _SLUG_INVALID.sub("", underscored)
    # Collapse runs of underscores (e.g. "self--review" → "self__review" → "self_review").
    cleaned = re.sub(r"_+", "_", cleaned).strip("_")
    if not cleaned:
        raise ValueError(
            f"Slug {raw!r} produced an empty filename — supply at least one alphanumeric word."
        )
    return cleaned


def _utc_timestamp(now: datetime | None = None) -> str:
    """Return ``YYYYMMDD_HHMMSS`` in UTC."""
    now = now or datetime.now(timezone.utc)
    return now.strftime("%Y%m%d_%H%M%S")


def _build_filename(slug: str, when: datetime | None = None) -> str:
    return f"{_utc_timestamp(when)}_{slug}.py"


def _render_template(*, interface: str, slug: str, description: str, timestamp: str) -> str:
    template = _TEMPLATE_POOL if interface == "pool" else _TEMPLATE_CONN
    return template.format(
        timestamp=timestamp,
        slug=slug,
        description=description,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Generate a new migration with a UTC timestamp prefix.",
    )
    parser.add_argument(
        "description",
        help="Free-text description of the migration; converted to a filename slug.",
    )
    parser.add_argument(
        "--interface",
        choices=["pool", "conn"],
        default="pool",
        help="Runner interface (Convention A `pool` is preferred for new migrations).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the path and template; don't write a file.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite the target file if it already exists.",
    )
    args = parser.parse_args(argv)

    if not MIGRATIONS_DIR.is_dir():
        print(
            f"ERROR: migrations dir not found: {MIGRATIONS_DIR}",
            file=sys.stderr,
        )
        return 2

    try:
        slug = _slugify(args.description)
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2

    filename = _build_filename(slug)
    target = MIGRATIONS_DIR / filename

    body = _render_template(
        interface=args.interface,
        slug=slug,
        description=args.description.strip(),
        timestamp=filename.removesuffix(".py"),
    )

    if args.dry_run:
        print(f"[dry-run] would write {target}")
        print()
        print(body)
        return 0

    if target.exists() and not args.force:
        print(
            f"ERROR: {target} already exists — re-run with --force to overwrite",
            file=sys.stderr,
        )
        return 2

    target.write_text(body, encoding="utf-8")
    print(f"Created {target}")
    print()
    print("Next steps:")
    print(f"  1. Edit {filename} — fill in the SQL and update the docstring.")
    print("  2. python scripts/ci/migrations_smoke.py     # apply against a fresh DB")
    print("  3. python scripts/ci/migrations_lint.py      # check naming/collisions")
    print("  4. Open a PR.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
