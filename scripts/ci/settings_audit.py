#!/usr/bin/env python3
"""Read-only app_settings lifecycle audit.

Classifies every live ``app_settings`` key against four declared sources of
truth so stale / dead keys surface without false-positiving the dynamically
constructed ones. No DB driver, no project imports -- it parses source files
with ast / regex and takes a TSV dump of the live table as input, so it is safe
to run in CI or against a prod dump.

Sources of truth
----------------
* DEFAULTS            settings_defaults.py        -- go-forward seeded defaults
* baseline.seeds      0000_baseline.seeds.sql     -- squashed historical seeds
* job registry        plugins/registry.py _SAMPLES + services/jobs/*.py on disk
* code corpus         literal + static-prefix references across the source tree

Buckets
-------
* LIVE-SEEDED      key is in DEFAULTS or baseline.seeds (the bulk; skip)
* LIVE-JOB-CONFIG  plugin.job.<job> for a job that still exists
* RUNTIME-STATE    plugin_job_last_*_<job> for a live job (state, not config)
* LIVE-DYNAMIC     constructed key whose static prefix appears in code
* LIVE-REFERENCED  literal key string appears in code
* SECRET-ORPHAN    is_secret + unseeded -- provisioned by `poindexter setup`
* DEAD             actionable: deleted-job config/state, or unreferenced
* UNSURE           orphan we could not confidently resolve -- eyeball it

Usage
-----
    # 1. dump the live table (key<TAB>category<TAB>is_secret<TAB>owner<TAB>
    #    value_type<TAB>deprecated<TAB>updated_at<TAB>value_preview)
    docker exec poindexter-postgres-local psql -U poindexter -d poindexter_brain \\
      -t -A -F $'\\t' -c "SELECT key, category, is_secret::int, COALESCE(owner,''), \\
      COALESCE(value_type,''), deprecated::int, (updated_at::date)::text, \\
      left(replace(value,E'\\n',' '),60) FROM app_settings ORDER BY key" > db_full.tsv
    # 2. classify
    python scripts/ci/settings_audit.py db_full.tsv [--json out.json]
"""
from __future__ import annotations

import argparse
import ast
import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SVC = REPO / "src" / "cofounder_agent" / "services"
DEFAULTS_PY = SVC / "settings_defaults.py"
BASELINE_SEEDS = SVC / "migrations" / "0000_baseline.seeds.sql"
REGISTRY_PY = REPO / "src" / "cofounder_agent" / "plugins" / "registry.py"
JOBS_DIR = SVC / "jobs"

# Roots scanned for code references. Heavy/build dirs are pruned in _walk.
CODE_ROOTS = [
    REPO / "src" / "cofounder_agent",
    REPO / "mcp-server",
    REPO / "mcp-server-gladlabs",
    REPO / "brain",
    REPO / "scripts",
    REPO / "web" / "public-site",
]
CODE_EXTS = {".py", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"}
PRUNE_DIRS = {"node_modules", ".next", ".git", "dist", "build", "__pycache__", ".venv", "coverage"}
# Files that merely *declare/seed* keys must not count as "referenced".
CORPUS_EXCLUDE = {DEFAULTS_PY.resolve(), BASELINE_SEEDS.resolve()}

_SEED_KEY_RE = re.compile(r"INTO app_settings[^;]*?VALUES\s*\(\s*'([^']+)'", re.I)
_JOB_SAMPLE_RE = re.compile(r'\(\s*"jobs"\s*,\s*"services\.jobs\.([a-z0-9_]+)"')
_JOB_STATE_RE = re.compile(r"^plugin_job_last_(?:run|status)_(.+)$")


def _dict_keys(name: str, tree: ast.Module) -> set[str]:
    for node in tree.body:
        if isinstance(node, ast.Assign):
            value, named = node.value, any(
                isinstance(t, ast.Name) and t.id == name for t in node.targets
            )
        elif isinstance(node, ast.AnnAssign):
            value, named = node.value, (
                isinstance(node.target, ast.Name) and node.target.id == name
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


def _walk(roots: list[Path]) -> str:
    chunks: list[str] = []
    for root in roots:
        if not root.exists():
            continue
        for p in root.rglob("*"):
            if p.suffix not in CODE_EXTS or not p.is_file():
                continue
            if any(part in PRUNE_DIRS for part in p.parts):
                continue
            if p.resolve() in CORPUS_EXCLUDE:
                continue
            try:
                chunks.append(p.read_text(encoding="utf-8", errors="ignore"))
            except OSError:
                continue
    return "\n".join(chunks)


def _valid_job_names() -> set[str]:
    names = set(_JOB_SAMPLE_RE.findall(REGISTRY_PY.read_text(encoding="utf-8")))
    if JOBS_DIR.exists():
        names |= {p.stem for p in JOBS_DIR.glob("*.py") if p.stem != "__init__"}
    return names


def _static_prefixes(key: str) -> list[str]:
    """Static prefixes a constructed key would share with its f-string."""
    out = []
    if "." in key:
        out.append(key.rsplit(".", 1)[0] + ".")
    if "_" in key:
        out.append(key.rsplit("_", 1)[0] + "_")
    return [p for p in out if len(p) >= 8]


@dataclass
class Row:
    key: str
    category: str = ""
    is_secret: bool = False
    owner: str = ""
    value_type: str = ""
    deprecated: bool = False
    updated_at: str = ""
    value_preview: str = ""
    bucket: str = ""
    reason: str = field(default="")


def classify(tsv: Path) -> list[Row]:
    tree = ast.parse(DEFAULTS_PY.read_text(encoding="utf-8"))
    defaults = _dict_keys("DEFAULTS", tree)
    baseline = set(_SEED_KEY_RE.findall(BASELINE_SEEDS.read_text(encoding="utf-8")))
    blessed = defaults | baseline
    jobs = _valid_job_names()
    corpus = _walk(CODE_ROOTS)

    rows: list[Row] = []
    for line in tsv.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        f = (line.split("\t") + [""] * 8)[:8]
        r = Row(
            key=f[0], category=f[1], is_secret=f[2] == "1", owner=f[3],
            value_type=f[4], deprecated=f[5] == "1", updated_at=f[6], value_preview=f[7],
        )
        k = r.key
        m_state = _JOB_STATE_RE.match(k)
        if k in blessed:
            r.bucket, r.reason = "LIVE-SEEDED", "in DEFAULTS/baseline"
        elif k.startswith("plugin.job."):
            job = k[len("plugin.job."):]
            if job in jobs:
                r.bucket, r.reason = "LIVE-JOB-CONFIG", f"job '{job}' registered"
            else:
                r.bucket, r.reason = "DEAD", f"config for removed job '{job}'"
        elif m_state:
            job = m_state.group(1)
            if job in jobs:
                r.bucket, r.reason = "RUNTIME-STATE", f"live job '{job}' run/status state"
            else:
                r.bucket, r.reason = "DEAD", f"orphan run/status state for removed job '{job}'"
        elif k in corpus:
            r.bucket, r.reason = "LIVE-REFERENCED", "literal appears in code"
        elif any(p in corpus for p in _static_prefixes(k)):
            hit = next(p for p in _static_prefixes(k) if p in corpus)
            r.bucket, r.reason = "LIVE-DYNAMIC", f"constructed; prefix '{hit}' in code"
        elif r.is_secret:
            r.bucket, r.reason = "SECRET-ORPHAN", "secret; provisioned by setup"
        else:
            r.bucket, r.reason = "DEAD", "no literal/prefix reference in code"
        rows.append(r)
    return rows


def report(rows: list[Row]) -> None:
    buckets = Counter(r.bucket for r in rows)
    order = ["LIVE-SEEDED", "LIVE-JOB-CONFIG", "RUNTIME-STATE", "LIVE-DYNAMIC",
             "LIVE-REFERENCED", "SECRET-ORPHAN", "UNSURE", "DEAD"]
    print(f"Total keys: {len(rows)}\n")
    for b in order:
        if buckets.get(b):
            print(f"  {buckets[b]:4d}  {b}")
    print()

    dead = [r for r in rows if r.bucket == "DEAD"]
    print(f"=== DEAD ({len(dead)}) — deprecation candidates ===")
    by_reason: dict[str, list[Row]] = defaultdict(list)
    for r in dead:
        kind = r.reason.split(" for ")[0].split(" '")[0]
        by_reason[kind].append(r)
    for kind, rs in sorted(by_reason.items(), key=lambda kv: -len(kv[1])):
        print(f"\n  [{len(rs)}] {kind}:")
        for r in sorted(rs, key=lambda r: r.key):
            print(f"    - {r.key}  [{r.category}]  upd={r.updated_at}  ({r.reason})")

    state = [r for r in rows if r.bucket == "RUNTIME-STATE"]
    if state:
        print(f"\n=== RUNTIME-STATE ({len(state)}) — live, but config-table pollution ===")
        print("  (run/status rows the scheduler writes; candidates to relocate out of app_settings)")
        for r in sorted(state, key=lambda r: r.key)[:6]:
            print(f"    - {r.key}")
        if len(state) > 6:
            print(f"    ... +{len(state) - 6} more")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("tsv", type=Path)
    ap.add_argument("--json", type=Path, default=None)
    args = ap.parse_args()
    rows = classify(args.tsv)
    report(rows)
    if args.json:
        args.json.write_text(
            json.dumps([r.__dict__ for r in rows], indent=2), encoding="utf-8"
        )
        print(f"\nwrote {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
