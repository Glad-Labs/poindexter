#!/usr/bin/env python3
"""CI guard: catch keys a migration DELETEs that a seed file still re-seeds.

The drift this prevents (found 2026-06-21): a one-shot migration DELETEs dead
``app_settings`` rows, but the keys are left in a seed source. Because the
baseline seeds re-apply on boot (``INSERT ... ON CONFLICT DO NOTHING`` re-inserts
any *absent* row), the one-shot DELETE loses to the every-boot INSERT and the
dead key resurrects. See ``feedback_seed_data_in_baseline_not_new_migrations``:
deletions of seeded data must edit the seed source, not just run a migration.

Seed sources checked:
  * ``0000_baseline.seeds.sql``      -- squashed baseline, re-applied on boot
  * ``brain/seed_app_settings.json`` -- brain's first-boot (empty-table) seed

A key is flagged only when it is BOTH deleted by a migration AND still in a seed
file AND not intentionally re-seeded by ``settings_defaults.DEFAULTS`` (those are
maintained on purpose) AND not in ALLOWLIST.

Static only -- no DB, no project imports -- so it runs in CI.
Exit 0 = clean, 1 = drift found.
"""
from __future__ import annotations

import ast
import json
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SVC = REPO / "src" / "cofounder_agent" / "services"
MIGRATIONS = SVC / "migrations"
BASELINE_SEEDS = MIGRATIONS / "0000_baseline.seeds.sql"
DEFAULTS_PY = SVC / "settings_defaults.py"
BRAIN_SEED = REPO / "brain" / "seed_app_settings.json"

# Known-OK exceptions: a deleted key deliberately left in a seed file (rare).
# Add a key here with a one-line reason if a flag is a confirmed false positive.
ALLOWLIST: dict[str, str] = {
    "embedding_model": (
        "migration 20260618_003647 over-pruned this; it has live callers "
        "(rag_engine/publish_service/ragas_eval, all with safe fallbacks) — "
        "intentionally retained in baseline.seeds"
    ),
    "image_generation_model": (
        "migration 20260618_003647 over-pruned this; read by the content image "
        "stages (replace_inline_images/source_featured_image) — intentionally "
        "retained in the seed files"
    ),
    "operator_url_probe_skip_keys": (
        "false positive in 20260625_120000_rename_sdxl_settings_to_image_gen: "
        "the migration only UPDATEs this key (replaces sdxl_server_url with "
        "image_gen_server_url in the value); the DELETE regex captures 300 chars "
        "and bleeds into the subsequent UPDATE WHERE clause, mis-tagging this key "
        "as deleted"
    ),
}

_SEED_KEY_RE = re.compile(r"INTO app_settings[^;]*?VALUES\s*\(\s*'([^']+)'", re.I)
_DELETE_MARKER = "DELETE FROM app_settings"
# Scope literal-key extraction to an actual DELETE statement (up to its
# terminating ``;``), so an UPDATE/SELECT touching a key elsewhere in the same
# migration is not misread as a deletion.
_DELETE_STMT_RE = re.compile(r"DELETE\s+FROM\s+app_settings\b([^;]{0,300})", re.I | re.S)
_DELETE_NAME_RE = re.compile(r"dead|drop|remove|retire|orphan|prune|delete|stale", re.I)
_SQL_EQ_RE = re.compile(r"key\s*=\s*'([^']+)'", re.I)
_SQL_IN_RE = re.compile(r"key\s+IN\s*\(([^)]+)\)", re.I)
_KEY_SHAPE = re.compile(r"^[a-z][a-z0-9_.]{2,}$")


def _seed_sources() -> dict[str, set[str]]:
    """Map each seed-file label to the set of app_settings keys it seeds."""
    sources: dict[str, set[str]] = {
        "baseline.seeds.sql": set(_SEED_KEY_RE.findall(BASELINE_SEEDS.read_text(encoding="utf-8"))),
    }
    if BRAIN_SEED.exists():
        data = json.loads(BRAIN_SEED.read_text(encoding="utf-8"))
        sources["brain/seed_app_settings.json"] = {
            s["key"]
            for s in data.get("settings", [])
            if isinstance(s, dict) and "key" in s
        }
    return sources


def _defaults_keys() -> set[str]:
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


def _deleted_keys_in(text: str) -> set[str]:
    """Best-effort extraction of keys a migration DELETEs from app_settings."""
    keys: set[str] = set()
    # 1. literals named in the WHERE clause of an actual DELETE statement
    for stmt in _DELETE_STMT_RE.findall(text):
        keys.update(_SQL_EQ_RE.findall(stmt))
        for inner in _SQL_IN_RE.findall(stmt):
            keys.update(re.findall(r"'([^']+)'", inner))
    # 2. deletion-named list/tuple literals (the ``_DEAD_KEYS = [...]`` pattern
    #    feeding ``WHERE key = ANY($1::text[])``)
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return {k for k in keys if _KEY_SHAPE.match(k)}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            names = [t.id for t in node.targets if isinstance(t, ast.Name)]
            if any(_DELETE_NAME_RE.search(n) for n in names) and isinstance(
                node.value, (ast.List, ast.Tuple)
            ):
                for el in node.value.elts:
                    if isinstance(el, ast.Constant) and isinstance(el.value, str):
                        keys.add(el.value)
    return {k for k in keys if _KEY_SHAPE.match(k)}


def main() -> int:
    sources = _seed_sources()
    seeded = set().union(*sources.values())
    defaults = _defaults_keys()

    deleted: dict[str, str] = {}  # key -> first migration that deletes it
    for mig in sorted(MIGRATIONS.glob("*.py")):
        if mig.name == "0000_baseline.py":
            continue
        text = mig.read_text(encoding="utf-8")
        if _DELETE_MARKER not in text:
            continue
        for k in _deleted_keys_in(text):
            deleted.setdefault(k, mig.name)

    drift = sorted(
        k for k in (set(deleted) & seeded) - defaults if k not in ALLOWLIST
    )
    if not drift:
        print(
            f"settings-seed-drift: OK ({len(deleted)} migration-deleted keys, "
            f"none re-seeded by {len(sources)} seed source(s))"
        )
        return 0

    print("settings-seed-drift: DRIFT — keys DELETEd by a migration are still in a seed file")
    print("(they will resurrect on boot; remove them from the seed file, or allowlist with a reason)\n")
    for k in drift:
        in_files = ", ".join(label for label, keys in sources.items() if k in keys)
        print(f"  - {k}   (deleted by {deleted[k]}; still seeded in {in_files})")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
