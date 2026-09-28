#!/usr/bin/env python3
# scan-floor-exempt: compares seed sources, needs no tree
"""CI guard: catch keys a migration DELETEs that a seed source still re-seeds.

The drift this prevents (found 2026-06-21): a one-shot migration DELETEs dead
``app_settings`` rows, but the keys are left in a seed source. Every seed source
inserts with ``ON CONFLICT DO NOTHING``, which re-inserts any *absent* row, so
the one-shot DELETE loses to the next seeding pass and the dead key resurrects.
See ``feedback_seed_data_in_baseline_not_new_migrations``: deletions of seeded
data must edit the seed source, not just run a migration.

Seed sources checked:
  * ``settings_defaults.DEFAULTS``   -- ``seed_all_defaults``, applied on EVERY boot
  * ``0000_baseline.seeds.sql``      -- squashed baseline, re-applied on boot
  * ``brain/seed_app_settings.json`` -- brain's first-boot (empty-table) seed

``DEFAULTS`` was exempt until 2026-09-28, as "maintained on purpose". That
exempted the one source that re-inserts on every boot, and it guarded nothing:
none of the 25 keys the migrations then deleted was still in it. It hid exactly
the case it should catch. ``rate_limit_video_generate_per_ip`` stayed in
``DEFAULTS`` from 2026-07-10, when its only route was deleted, to 2026-09-28,
and a migration dropping it would have passed here with that line left
behind. If a migration really does delete a row so ``seed_all_defaults``
re-inserts a corrected default, allowlist the key with that reason. An
``UPDATE`` is usually the better tool for that.

A key is flagged when it is BOTH deleted by a migration AND still in a seed
source AND not in ALLOWLIST. An ALLOWLIST entry for a key no current migration
deletes is flagged too: a squash folds its migration away, and a stale entry
would silently wave through the next migration to delete that key.

Static only -- no DB, no project imports -- so it runs in CI.
Exit 0 = clean; 1 = drift, a stale ALLOWLIST entry, or a seed source that
parsed to zero keys (a moved or reshaped source must not read as "nothing
re-seeded").
"""
from __future__ import annotations

import ast
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib_scan_floor import require_scanned  # noqa: E402

LINT = "settings-seed-drift"

REPO = Path(__file__).resolve().parents[2]
SVC = REPO / "src" / "cofounder_agent" / "poindexter" / "services"
MIGRATIONS = SVC / "migrations"
BASELINE_SEEDS = MIGRATIONS / "0000_baseline.seeds.sql"
DEFAULTS_PY = SVC / "settings_defaults.py"
BRAIN_SEED = REPO / "src" / "cofounder_agent" / "poindexter" / "brain" / "seed_app_settings.json"

# Known-OK exceptions: a key a migration deletes that a seed source deliberately
# keeps (rare -- a delete-to-reseed of a corrected DEFAULTS value, or a
# confirmed false positive of ``_deleted_keys_in``). Add it with a one-line
# reason. main() fails on an entry whose key no current migration deletes, so an
# entry leaves with its migration. The last three went stale when the Phase F/G
# squashes folded 20260618_003647 and 20260625_120000 into the baseline.
ALLOWLIST: dict[str, str] = {}

_SEED_KEY_RE = re.compile(r"INTO app_settings[^;]*?VALUES\s*\(\s*'([^']+)'", re.I)
_DELETE_MARKER = "DELETE FROM app_settings"
# Scope literal-key extraction to an actual DELETE statement (up to its
# terminating ``;``), so an UPDATE/SELECT touching a key elsewhere in the same
# migration is not misread as a deletion. ``_deleted_keys_in`` also runs this
# over one Python string literal at a time: SQL in a migration rarely ends in
# ``;``, and over the raw file text the window ran on past the end of
# ``"DELETE FROM app_settings WHERE key = $1"`` into the next statement's
# ``UPDATE ... WHERE key = 'operator_url_probe_skip_keys'``, which it then
# reported as deleted (20260625_120000, allowlisted until the squash).
_DELETE_STMT_RE = re.compile(r"DELETE\s+FROM\s+app_settings\b([^;]{0,300})", re.I | re.S)
_DELETE_NAME_RE = re.compile(r"dead|drop|remove|retire|orphan|prune|delete|stale", re.I)
_SQL_EQ_RE = re.compile(r"key\s*=\s*'([^']+)'", re.I)
_SQL_IN_RE = re.compile(r"key\s+IN\s*\(([^)]+)\)", re.I)
_KEY_SHAPE = re.compile(r"^[a-z][a-z0-9_.]{2,}$")


def _seed_sources() -> dict[str, tuple[Path, set[str]]]:
    """Map each seed-source label to ``(path, keys it seeds)``."""
    sources: dict[str, tuple[Path, set[str]]] = {
        "settings_defaults.DEFAULTS": (DEFAULTS_PY, _defaults_keys()),
        "baseline.seeds.sql": (
            BASELINE_SEEDS,
            set(_SEED_KEY_RE.findall(BASELINE_SEEDS.read_text(encoding="utf-8"))),
        ),
    }
    if BRAIN_SEED.exists():
        data = json.loads(BRAIN_SEED.read_text(encoding="utf-8"))
        sources["brain/seed_app_settings.json"] = (
            BRAIN_SEED,
            {
                s["key"]
                for s in data.get("settings", [])
                if isinstance(s, dict) and "key" in s
            },
        )
    return sources


def _defaults_keys() -> set[str]:
    """Keys of the module-level ``DEFAULTS`` dict literal; empty if not found.

    Empty is never accepted as "seeds nothing": main() runs every source
    through ``require_scanned``, so a renamed or reshaped ``DEFAULTS`` fails
    the lint instead of silently disarming its check.
    """
    tree = ast.parse(DEFAULTS_PY.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign):
            value, named = node.value, any(
                isinstance(t, ast.Name) and t.id == "DEFAULTS" for t in node.targets
            )
        elif isinstance(node, ast.AnnAssign):
            value, named = node.value, (
                isinstance(node.target, ast.Name) and node.target.id == "DEFAULTS"
            )
        else:
            continue
        if named and isinstance(value, ast.Dict):
            return {
                k.value
                for k in value.keys
                if isinstance(k, ast.Constant) and isinstance(k.value, str)
            }
    return set()


def _sql_deleted_keys(sql: str) -> set[str]:
    """Keys named in the WHERE clause of each DELETE statement in ``sql``."""
    keys: set[str] = set()
    for stmt in _DELETE_STMT_RE.findall(sql):
        keys.update(_SQL_EQ_RE.findall(stmt))
        for inner in _SQL_IN_RE.findall(stmt):
            keys.update(re.findall(r"'([^']+)'", inner))
    return keys


def _code_strings(tree: ast.AST) -> list[str]:
    """Every string literal in ``tree`` except docstrings.

    The parser has already joined adjacent literals, so a DELETE split across
    lines (``"... key IN (" "'a', " "'b')"``) arrives as one string. Docstrings
    are prose: a migration explaining what an older one deleted is not a
    deletion.
    """
    docstrings: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            first = node.body[0] if node.body else None
            if (
                isinstance(first, ast.Expr)
                and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)
            ):
                docstrings.add(id(first.value))
    return [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstrings
    ]


def _deleted_keys_in(text: str) -> set[str]:
    """Best-effort extraction of keys a migration DELETEs from app_settings."""
    try:
        tree = ast.parse(text)
    except SyntaxError:
        # Unparseable: scan the raw text, accepting the cross-statement
        # over-match the per-literal scan exists to avoid.
        return {k for k in _sql_deleted_keys(text) if _KEY_SHAPE.match(k)}
    keys: set[str] = set()
    # 1. literals named in the WHERE clause of a DELETE statement, read one
    #    string literal at a time so the match cannot cross into the next one
    for literal in _code_strings(tree):
        keys |= _sql_deleted_keys(literal)
    # 2. deletion-named list/tuple literals (the ``_DEAD_KEYS = [...]`` pattern
    #    feeding ``WHERE key = ANY($1::text[])``), annotated or not
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            names = [t.id for t in node.targets if isinstance(t, ast.Name)]
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names = [node.target.id]
        else:
            continue
        if any(_DELETE_NAME_RE.search(n) for n in names) and isinstance(
            node.value, (ast.List, ast.Tuple)
        ):
            for el in node.value.elts:
                if isinstance(el, ast.Constant) and isinstance(el.value, str):
                    keys.add(el.value)
    return {k for k in keys if _KEY_SHAPE.match(k)}


def main() -> int:
    sources = _seed_sources()
    for label, (path, keys) in sources.items():
        require_scanned(len(keys), lint=LINT, what=f"keys in {label}", roots=(path,))
    seeded = set().union(*(keys for _, keys in sources.values()))

    deleted: dict[str, str] = {}  # key -> first migration that deletes it
    for mig in sorted(MIGRATIONS.glob("*.py")):
        if mig.name == "0000_baseline.py":
            continue
        text = mig.read_text(encoding="utf-8")
        if _DELETE_MARKER not in text:
            continue
        for k in _deleted_keys_in(text):
            deleted.setdefault(k, mig.name)

    drift = sorted(k for k in set(deleted) & seeded if k not in ALLOWLIST)
    stale = sorted(k for k in ALLOWLIST if k not in deleted)
    if not drift and not stale:
        print(
            f"{LINT}: OK ({len(deleted)} migration-deleted keys, "
            f"none re-seeded by {len(sources)} seed source(s))"
        )
        return 0

    if drift:
        print(f"{LINT}: DRIFT — keys DELETEd by a migration are still in a seed source")
        print(
            "(the next seeding pass resurrects them; remove each from every source "
            "listed, or allowlist it with a reason)\n"
        )
        for k in drift:
            in_sources = ", ".join(label for label, (_, keys) in sources.items() if k in keys)
            print(f"  - {k}   (deleted by {deleted[k]}; still seeded in {in_sources})")
    if stale:
        if drift:
            print()
        print(f"{LINT}: STALE ALLOWLIST — no current migration deletes these keys")
        print("(the migration each entry excused is gone; delete the entry)\n")
        for k in stale:
            print(f"  - {k}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
