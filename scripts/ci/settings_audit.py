#!/usr/bin/env python3
# scan-floor-exempt: audit-only reporter, not wired into CI as a gate
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
# Shell / SQL count as code here (poindexter#915). scripts/backup-offsite/run.sh
# reads five `offsite_backup_*` keys through its own `read_setting` psql helper
# — a reader that touches neither SiteConfig nor Python. With .sh excluded those
# keys looked dead to BOTH detectors at once: no literal in the scanned corpus,
# and no `last_read_at` because nothing went through SiteConfig.
CODE_EXTS = {
    ".py", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs",
    ".sh", ".bash", ".ps1", ".sql",
}
PRUNE_DIRS = {"node_modules", ".next", ".git", "dist", "build", "__pycache__", ".venv", "coverage"}
# Files that merely *declare/seed* keys must not count as "referenced".
CORPUS_EXCLUDE = {DEFAULTS_PY.resolve(), BASELINE_SEEDS.resolve()}

_SEED_KEY_RE = re.compile(r"INTO app_settings[^;]*?VALUES\s*\(\s*'([^']+)'", re.I)
_JOB_SAMPLE_RE = re.compile(r'\(\s*"jobs"\s*,\s*"services\.jobs\.([a-z0-9_]+)"')
_JOB_STATE_RE = re.compile(r"^plugin_job_last_(?:run|status)_(.+)$")
# Bulk raw-SQL readers (poindexter#915). `findings_alert_router.py` does
#   WHERE key LIKE 'findings.%.%'
# and `findings_read.py` does LIKE 'findings.%.delivery'. Those read 59 live
# keys that no literal in the corpus mentions, and raw SQL never stamps
# `last_read_at` — so both detectors called them dead at once. Deleting them
# would have silently dropped operator alert routing to defaults.
_SQL_LIKE_RE = re.compile(r"LIKE\s+'([^']*%[^']*)'", re.I)


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
    """Static prefixes a constructed key could share with its f-string.

    Every separator boundary, longest first — not just the last one
    (poindexter#915). `memory_stale_threshold_seconds_collapse_job` is built as
    f"memory_stale_threshold_seconds_{writer}", and splitting on the LAST `_`
    yields `memory_stale_threshold_seconds_collapse_`, which appears nowhere.
    A two-word dynamic suffix evaded the heuristic entirely, and read telemetry
    proved the key live the same day it was reported dead.

    Longest-first matters for the reported reason: the narrowest prefix that
    matches is the most informative one to show a human.
    """
    out: list[str] = []
    for sep in (".", "_"):
        parts = key.split(sep)
        # Drop one trailing segment at a time: a-b-c -> "a-b-", "a-".
        for cut in range(len(parts) - 1, 0, -1):
            out.append(sep.join(parts[:cut]) + sep)
    # Longest first, de-duplicated, and long enough not to match everything.
    return sorted({p for p in out if len(p) >= 8}, key=len, reverse=True)


def _sql_like_patterns(corpus: str) -> list[str]:
    """SQL LIKE patterns in the corpus that could address app_settings keys."""
    out = set()
    for pat in _SQL_LIKE_RE.findall(corpus):
        # Anchor on something specific: a bare '%' would swallow every key.
        if len(pat.replace("%", "").replace("_", "")) >= 6:
            out.add(pat)
    return sorted(out)


def _matches_sql_like(key: str, pattern: str) -> bool:
    """SQL LIKE semantics: % = any run, _ = any single char."""
    rx = "".join(
        ".*" if ch == "%" else ("." if ch == "_" else re.escape(ch))
        for ch in pattern
    )
    return re.fullmatch(rx, key) is not None


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
    sql_likes = _sql_like_patterns(corpus)

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
        # NOTE: being seeded is deliberately NOT a short-circuit any more
        # (poindexter#915). The tool used to bucket any seeded key as
        # LIVE-SEEDED without ever computing a reference — which is exactly the
        # class #913 lived in, so it structurally could not find another
        # `daily_spend_limit`. Seeded keys now fall through the same reference
        # checks and land in SEEDED-UNREFERENCED when nothing reads them.
        if k.startswith("plugin.job."):
            # `plugin.job.<name>` AND `plugin.job.<name>.<attr>` — the suffix
            # form is real config (`plugin.job.sync_affiliate_clicks.enabled`,
            # `.interval_seconds`). Matching the whole remainder against the
            # job set reported LIVE jobs as removed: a FALSE DEAD, which is
            # worse than the false-live misses in poindexter#915 because
            # acting on it deletes working config. Longest first, so a job
            # whose own name contains a dot would still win.
            rest = k[len("plugin.job."):]
            parts = rest.split(".")
            job = next(
                (".".join(parts[:n]) for n in range(len(parts), 0, -1)
                 if ".".join(parts[:n]) in jobs),
                None,
            )
            if job is not None:
                r.bucket, r.reason = "LIVE-JOB-CONFIG", f"job '{job}' registered"
            else:
                r.bucket, r.reason = "DEAD", f"config for removed job '{rest}'"
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
        elif (sql_hit := next(
            (pat for pat in sql_likes if _matches_sql_like(k, pat)), None
        )) is not None:
            r.bucket, r.reason = "LIVE-SQL-LIKE", f"read in bulk by LIKE '{sql_hit}'"
        elif r.is_secret:
            r.bucket, r.reason = "SECRET-ORPHAN", "secret; provisioned by setup"
        elif k in blessed:
            # Seeded, documented, and nothing reads it. Not proof of death —
            # a shell or raw-SQL reader outside the scanned roots would look
            # the same — but it is the bucket worth reviewing by hand.
            r.bucket, r.reason = "SEEDED-UNREFERENCED", "seeded but no reader found"
        else:
            r.bucket, r.reason = "DEAD", "no literal/prefix reference in code"
        rows.append(r)
    return rows


def report(rows: list[Row]) -> None:
    buckets = Counter(r.bucket for r in rows)
    order = ["LIVE-SEEDED", "LIVE-JOB-CONFIG", "RUNTIME-STATE", "LIVE-DYNAMIC",
             "LIVE-SQL-LIKE", "SEEDED-UNREFERENCED",
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

    # The bucket this tool previously could not produce at all (poindexter#915):
    # seeded keys used to short-circuit to LIVE-SEEDED without a reference
    # check, which is the class #913 lived in. Listed in full, because a
    # count alone is not reviewable and this is the bucket a human must read.
    seeded = [r for r in rows if r.bucket == "SEEDED-UNREFERENCED"]
    if seeded:
        print(f"\n=== SEEDED-UNREFERENCED ({len(seeded)}) — seeded, no reader found ===")
        print("  (NOT proof of death: a reader outside the scanned roots looks")
        print("   identical. Verify each against shell/raw-SQL/dynamic use.)")
        for r in sorted(seeded, key=lambda r: r.key):
            print(f"    - {r.key}  [{r.category}]  upd={r.updated_at}")

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
