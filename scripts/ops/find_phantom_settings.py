#!/usr/bin/env python3
"""Find phantom ``app_settings`` rows whose key contains ``/``.

Background
----------
The ``poindexter settings list`` output historically rendered each row
as ``{category}/{key} = {value}``. Operators copy-pasted that visible
form into ``poindexter settings set``, which silently UPSERTed a NEW
row with the literal key ``category/key`` instead of updating the
canonical bare-key row that consumers actually read.

The phantom-key guard (2026-05-27) blocked the upsert, and the proper
fix (2026-05-28) auto-strips the prefix AND reshapes the list output.
This script sweeps production to find any phantom rows already sitting
in the table before the proper fix landed.

Behaviour
---------
For every row whose ``key`` contains ``/``:

  1. Compute the canonical bare key as ``key.rsplit('/', 1)[-1]``.
  2. Check whether a canonical row exists at that bare key.
  3. Classify:
       - PHANTOM      → canonical row exists; the slash row is dead
                        weight no consumer reads. Safe to delete.
       - GENUINE-LIKE → no canonical counterpart. Probably a real
                        key that just happens to contain ``/``. LEAVE
                        ALONE — operator review required.

Prints a markdown report on stdout plus a copy-pasteable SQL DELETE
statement for the PHANTOM rows. Nothing is written back to the DB;
the operator decides whether to run the DELETE.

Usage
-----

    DATABASE_URL=postgres://... python scripts/ops/find_phantom_settings.py

Or, with bootstrap.toml on disk:

    python scripts/ops/find_phantom_settings.py

Add ``--dry-test`` to skip the DB call and exercise the classification
logic against a hardcoded fixture (sanity check for the script's
internal logic without touching prod).
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path
from typing import Any

try:
    import asyncpg  # type: ignore
except ImportError:  # pragma: no cover - dev environment without asyncpg
    print("asyncpg is required", file=sys.stderr)
    raise


# Make the cofounder_agent packages importable so we can reuse the
# bootstrap helpers rather than reimplementing DSN resolution.
_REPO_ROOT = Path(__file__).resolve().parents[2]
for _candidate in (
    _REPO_ROOT / "src" / "cofounder_agent",
    _REPO_ROOT,
):
    if _candidate.exists() and str(_candidate) not in sys.path:
        sys.path.insert(0, str(_candidate))


def _classify(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split slash-containing rows into (phantoms, genuine_like).

    A phantom row is one whose ``key.rsplit('/', 1)[-1]`` matches an
    existing bare canonical row in ``rows`` or in the broader table.
    The caller passes in the full universe (slash rows + their potential
    canonical counterparts) so we can compare without a second DB hit.
    """
    canonical_keys = {r["key"] for r in rows if "/" not in r["key"]}
    phantoms: list[dict[str, Any]] = []
    genuine: list[dict[str, Any]] = []
    for r in rows:
        if "/" not in r["key"]:
            continue
        canonical = r["key"].rsplit("/", 1)[-1]
        if canonical in canonical_keys:
            phantoms.append({**r, "_canonical": canonical})
        else:
            genuine.append({**r, "_canonical": canonical})
    return phantoms, genuine


def _render_report(
    phantoms: list[dict[str, Any]],
    genuine: list[dict[str, Any]],
) -> str:
    lines: list[str] = []
    lines.append("# Phantom app_settings sweep")
    lines.append("")
    lines.append(f"- Phantom rows (safe to delete): **{len(phantoms)}**")
    lines.append(f"- Genuine-like slash rows (LEAVE ALONE): **{len(genuine)}**")
    lines.append("")

    if phantoms:
        lines.append("## Phantom rows")
        lines.append("")
        lines.append("These rows have a canonical bare-key counterpart in the table. ")
        lines.append("No consumer reads the slash form — they read the canonical key. ")
        lines.append("Safe to delete after operator review.")
        lines.append("")
        lines.append("| Phantom key | Canonical key | Phantom value |")
        lines.append("|---|---|---|")
        for r in phantoms:
            v = (r.get("value") or "")[:60].replace("|", "\\|")
            lines.append(f"| `{r['key']}` | `{r['_canonical']}` | `{v}` |")
        lines.append("")
        lines.append("### SQL to delete the phantoms")
        lines.append("")
        lines.append("```sql")
        keys_sql = ", ".join(f"'{r['key']}'" for r in phantoms)
        lines.append(f"DELETE FROM app_settings WHERE key IN ({keys_sql});")
        lines.append("```")
        lines.append("")

    if genuine:
        lines.append("## Genuine-like slash rows")
        lines.append("")
        lines.append("These rows have no canonical counterpart, so they MAY be ")
        lines.append("legitimate keys that happen to contain '/'. Review each ")
        lines.append("by hand before doing anything.")
        lines.append("")
        lines.append("| Key | Category | Value |")
        lines.append("|---|---|---|")
        for r in genuine:
            v = (r.get("value") or "")[:60].replace("|", "\\|")
            cat = r.get("category") or "?"
            lines.append(f"| `{r['key']}` | `{cat}` | `{v}` |")
        lines.append("")

    if not phantoms and not genuine:
        lines.append("No slash-containing keys found. Nothing to do.")
        lines.append("")

    return "\n".join(lines)


async def _fetch_rows(dsn: str) -> list[dict[str, Any]]:
    """Fetch the universe needed for classification.

    Pulls every row whose ``key`` contains ``/`` PLUS every canonical
    bare-key row that might be the counterpart (i.e. ``key`` equal to
    the ``rsplit('/', 1)[-1]`` of any slash row). Two queries instead
    of one to keep the WHERE clause readable.
    """
    conn = await asyncpg.connect(dsn)
    try:
        slash_rows = await conn.fetch(
            "SELECT key, value, category FROM app_settings WHERE key LIKE '%/%'"
        )
        if not slash_rows:
            return []
        canonical_candidates = sorted({r["key"].rsplit("/", 1)[-1] for r in slash_rows})
        canonical_rows = await conn.fetch(
            "SELECT key, value, category FROM app_settings WHERE key = ANY($1::text[])",
            canonical_candidates,
        )
        # Merge — slash rows + their canonical counterparts.
        rows_by_key: dict[str, dict[str, Any]] = {r["key"]: dict(r) for r in slash_rows}
        for r in canonical_rows:
            rows_by_key.setdefault(r["key"], dict(r))
        return list(rows_by_key.values())
    finally:
        await conn.close()


def _dry_test_fixture() -> list[dict[str, Any]]:
    """Hardcoded fixture for ``--dry-test`` — exercises classifier paths."""
    return [
        # PHANTOM: canonical bare-key row exists for daily_post_limit.
        {"key": "pipeline/daily_post_limit", "value": "4", "category": "pipeline"},
        {"key": "daily_post_limit", "value": "1", "category": "pipeline"},
        # PHANTOM: canonical bare-key row exists for auto_publish_threshold.
        {"key": "quality/auto_publish_threshold", "value": "85", "category": "quality"},
        {"key": "auto_publish_threshold", "value": "0", "category": "quality"},
        # GENUINE-LIKE: no canonical bare-key counterpart for "rate-limit".
        {
            "key": "experimental/multi-tenant/rate-limit",
            "value": "100",
            "category": "experimental",
        },
    ]


async def _amain() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    parser.add_argument(
        "--dry-test",
        action="store_true",
        help="Skip the DB and run the classifier against a hardcoded fixture.",
    )
    args = parser.parse_args()

    if args.dry_test:
        rows = _dry_test_fixture()
        print("# Dry-test mode — fixture data, NOT real DB rows", file=sys.stderr)
    else:
        from poindexter.cli._bootstrap import resolve_dsn

        dsn = os.getenv("DATABASE_URL") or resolve_dsn()
        if not dsn:
            print("ERROR: no DATABASE_URL resolved", file=sys.stderr)
            return 2
        rows = await _fetch_rows(dsn)

    phantoms, genuine = _classify(rows)
    print(_render_report(phantoms, genuine))
    return 0


def main() -> None:
    raise SystemExit(asyncio.run(_amain()))


if __name__ == "__main__":
    main()
