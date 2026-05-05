#!/usr/bin/env python3
"""Lint the migrations directory for collisions and naming-convention drift.

Glad-Labs/poindexter#378 — keep the migrations directory consistent
without hand-policing every PR.

CHECKS

1. **Filename pattern** — every file is either:
   - a legacy 4-digit prefix: ``NNNN_<slug>.py``
   - or a UTC-timestamp prefix: ``YYYYMMDD_HHMMSS_<slug>.py``
   Anything else is a hard fail (typos like ``015a_…``, missing slug,
   wrong extension, etc.)

2. **Prefix collisions among NEW migrations** — two timestamp-prefixed
   migrations sharing the exact same ``YYYYMMDD_HHMMSS`` prefix is a
   hard fail. (Two contributors would have to invoke the generator
   in the same UTC second to hit this; the lint exists to catch the
   case where someone copy-pastes a filename instead of re-running
   the generator.)

3. **Legacy-prefix collisions** — two ``NNNN_*`` files sharing the
   same integer prefix. Reported as a WARNING, not a hard fail —
   the directory has historical examples (``0093_*``, ``0125_*``,
   ``0158_*``) and renaming them would invalidate ``schema_migrations``
   rows on every operator's local DB.

4. **No new legacy migrations** — after the cutoff date
   ``LEGACY_PREFIX_CUTOFF_NUMBER``, NEW migrations using a 4-digit
   prefix are a hard fail. Use the timestamp generator instead.

5. **Runner interface** — every file must define ``up()`` OR
   ``run_migration()``. The runner silently skips files that have
   neither, which is a foot-gun for new contributors.

EXIT CODES
    0 — clean
    1 — at least one hard failure (collision / format / missing interface)
    2 — script error (missing migrations dir, etc.)

USAGE
    python scripts/ci/migrations_lint.py
    python scripts/ci/migrations_lint.py --strict   # treat WARNINGs as failures

The script is dependency-free (stdlib only) so it runs in CI without
the project's poetry environment.
"""

from __future__ import annotations

import argparse
import ast
import re
import sys
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS_DIR = REPO_ROOT / "src" / "cofounder_agent" / "services" / "migrations"

# After this 4-digit prefix, NEW migrations must use the timestamp
# format. Bump this whenever a legitimate legacy-numbered migration
# lands; the lint will refuse anything ABOVE it that isn't a timestamp.
# As of #378 the highest legacy prefix is 0159.
LEGACY_PREFIX_CUTOFF_NUMBER = 159

_RE_LEGACY = re.compile(r"^(?P<prefix>\d{4})_(?P<slug>[a-z0-9_]+)\.py$")
_RE_TIMESTAMP = re.compile(
    r"^(?P<prefix>\d{8}_\d{6})_(?P<slug>[a-z0-9_]+)\.py$"
)


def _classify(filename: str) -> tuple[str, str | None, str | None]:
    """Return (kind, prefix, slug). kind ∈ {'legacy','timestamp','invalid'}."""
    m = _RE_LEGACY.match(filename)
    if m:
        return "legacy", m.group("prefix"), m.group("slug")
    m = _RE_TIMESTAMP.match(filename)
    if m:
        return "timestamp", m.group("prefix"), m.group("slug")
    return "invalid", None, None


def _has_runner_interface(path: Path) -> bool:
    """Parse the file and check whether it defines `up` or `run_migration`."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError:
        return False  # treat unparseable as missing-interface; will surface
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name in ("up", "run_migration"):
                return True
    return False


def _is_timestamp_prefix_valid(prefix: str) -> bool:
    """Verify YYYYMMDD_HHMMSS parses as a real datetime."""
    from datetime import datetime
    try:
        datetime.strptime(prefix, "%Y%m%d_%H%M%S")
        return True
    except ValueError:
        return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Lint Poindexter migration filenames for collisions / convention drift.",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Treat warnings (legacy-prefix collisions) as hard failures.",
    )
    args = parser.parse_args(argv)

    if not MIGRATIONS_DIR.is_dir():
        print(f"ERROR: migrations dir not found: {MIGRATIONS_DIR}", file=sys.stderr)
        return 2

    files = sorted(
        p for p in MIGRATIONS_DIR.glob("*.py") if p.name != "__init__.py"
    )
    if not files:
        print("[lint] no migration files found — nothing to check")
        return 0

    errors: list[str] = []
    warnings: list[str] = []

    legacy_buckets: dict[str, list[str]] = defaultdict(list)
    ts_buckets: dict[str, list[str]] = defaultdict(list)

    for path in files:
        kind, prefix, slug = _classify(path.name)
        if kind == "invalid":
            errors.append(
                f"INVALID FILENAME: {path.name} — must match either "
                f"NNNN_<slug>.py (legacy) or YYYYMMDD_HHMMSS_<slug>.py (timestamp)"
            )
            continue

        # Check 5 — interface present
        if not _has_runner_interface(path):
            errors.append(
                f"MISSING INTERFACE: {path.name} — must define `up(pool)` or "
                f"`run_migration(conn)` (the runner silently skips files lacking both)"
            )

        if kind == "legacy":
            legacy_buckets[prefix].append(path.name)  # type: ignore[arg-type]
            # Check 4 — no NEW legacy migrations above the cutoff
            try:
                if int(prefix) > LEGACY_PREFIX_CUTOFF_NUMBER:  # type: ignore[arg-type]
                    errors.append(
                        f"LEGACY PREFIX ABOVE CUTOFF: {path.name} — legacy "
                        f"4-digit prefix {prefix} is above the cutoff "
                        f"({LEGACY_PREFIX_CUTOFF_NUMBER}). Use the timestamp "
                        "generator: python scripts/new-migration.py \"<slug>\""
                    )
            except ValueError:
                # impossible (regex constrains to 4 digits) but defensively continue
                pass
        else:  # timestamp
            ts_buckets[prefix].append(path.name)  # type: ignore[arg-type]
            if not _is_timestamp_prefix_valid(prefix):  # type: ignore[arg-type]
                errors.append(
                    f"INVALID TIMESTAMP: {path.name} — prefix {prefix} is not a "
                    "valid UTC datetime in YYYYMMDD_HHMMSS format"
                )

    # Check 2 — timestamp collisions
    for prefix, names in ts_buckets.items():
        if len(names) > 1:
            errors.append(
                f"TIMESTAMP COLLISION: prefix {prefix} used by:\n  - "
                + "\n  - ".join(names)
                + "\n  Re-run `python scripts/new-migration.py` for one of "
                "them so each gets a unique UTC second."
            )

    # Check 3 — legacy collisions (warning by default)
    for prefix, names in legacy_buckets.items():
        if len(names) > 1:
            warnings.append(
                f"LEGACY PREFIX COLLISION: {prefix} used by:\n  - "
                + "\n  - ".join(names)
                + "\n  These are accepted historical warts (renaming would "
                "invalidate schema_migrations rows). Listed for visibility."
            )

    # ----- Report -----
    print(f"[lint] checked {len(files)} migration file(s)")
    print(f"[lint] legacy: {sum(len(v) for v in legacy_buckets.values())}, "
          f"timestamp: {sum(len(v) for v in ts_buckets.values())}")

    for w in warnings:
        print(f"[lint] WARNING: {w}", file=sys.stderr)
    for e in errors:
        print(f"[lint] ERROR: {e}", file=sys.stderr)

    failed = bool(errors) or (args.strict and bool(warnings))
    if failed:
        print(
            f"[lint] FAIL — {len(errors)} error(s), {len(warnings)} warning(s)"
            + (" (--strict)" if args.strict and warnings else ""),
            file=sys.stderr,
        )
        return 1
    print(
        f"[lint] OK — {len(errors)} error(s), {len(warnings)} warning(s)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
