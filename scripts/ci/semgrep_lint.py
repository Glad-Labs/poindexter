#!/usr/bin/env python3
"""Semgrep ratchet: no NET-NEW security findings, including in the overlay.

This is the compensating control for a real gap. GitHub code scanning has been
disabled on every Glad-Labs private repo since 2026-06-19 — not by a repo
setting, but by an org-level security configuration ("Private Repos", id
246930) that has Code Security, secret scanning and push protection all off.
The public mirror sits on a different configuration with everything on, which
is what made the gap invisible: the half of the tree you can see is scanned,
and the half you cannot is not.

The private half is exactly the half that matters here. `scripts/sync-to-
github.sh` strips `modules/finance/`, `operator_overrides.py`,
`operator_leak_patterns.py` and the Claude-sessions tap before publishing, so
the mirror's CodeQL has never seen them either. Until this lint, that code was
scanned by nothing. Enabling GitHub's own scanning bills per active committer
(`purchased_advanced_security_committers: null`), so this runs Semgrep from the
repo instead — no seats, no network, no per-run cost.

RATCHET, NOT AN ISSUE-FILER
---------------------------
Same doctrine as `bandit_lint.py`, and for the same reason. Semgrep has no
project context: at vendoring time its 47 findings were 44 hits of one noisy
rule (`python-logger-credential-disclosure`, which matched a log line reading
"requires login") plus a `sql-injection-db-cursor-execute` on a fully
parameterized asyncpg call — the sanctioned pattern this codebase uses
everywhere. Left to file issues, that reproduces the bandit wave that opened 91
GitHub issues, every one a false positive, burying 18 genuine ones three pages
deep.

So the baseline grandfathers what exists and the gate blocks what is NEW,
keyed per-file-per-rule (a new `sql-injection` cannot ride in behind a deleted
`insecure-hash-algorithm`). Findings are mostly false positives: read the line
and baseline it. If one is genuinely unsafe, FIX it rather than baselining it.

Rules are vendored under `infrastructure/semgrep/` rather than pulled from the
registry — see the README there for why.

Usage::

    python scripts/ci/semgrep_lint.py                    # exit 0 clean, 1 regression
    python scripts/ci/semgrep_lint.py --update-baseline  # re-baseline (after triage)
"""

# scan-floor-exempt: floors in-script on files-scanned; needs the semgrep binary

from __future__ import annotations

import argparse
import json
import shutil
import subprocess  # nosec B404 - invoking our own pinned semgrep, no user input
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
BASELINE_PATH = Path(__file__).resolve().parent / "semgrep_baseline.json"
RULES_DIR = REPO_ROOT / "infrastructure" / "semgrep"

# Scan roots. The overlay lives under src/cofounder_agent/modules/finance and
# services/operator_*.py, so scanning src/ covers it — that is the point.
SCAN_TARGETS = ("src/cofounder_agent", "brain", "scripts")

# Below this, assume the scan did not really happen. The tree scanned ~1,520
# files when this was written; a collapse to a handful means a moved root or a
# broken invocation, and reporting "clean" off that is how a gate silently
# dies. See scripts/ci/lib_scan_floor.py for the same idea applied to the
# pure-AST lints.
MIN_FILES_SCANNED = 400


def _rules_args() -> list[str]:
    configs = sorted(RULES_DIR.glob("*.yaml"))
    if not configs:
        raise RuntimeError(
            f"no vendored rulesets under {RULES_DIR} — refusing to scan with no "
            "rules, which would report a clean tree from a scan that checked "
            "nothing. Restore them per infrastructure/semgrep/README.md."
        )
    args: list[str] = []
    for cfg in configs:
        args += ["--config", str(cfg)]
    return args


def _normalize_path(path: str) -> str:
    """Repo-relative, forward-slashed, so baseline keys are portable."""
    p = Path(path)
    if p.is_absolute():
        try:
            p = p.relative_to(REPO_ROOT)
        except ValueError:
            pass
    return p.as_posix()


def _rule_id(check_id: str) -> str:
    """Short, stable rule key — the registry prefixes the full ruleset path."""
    return check_id.rsplit(".", 1)[-1]


def counts_from_findings(findings: list[dict]) -> dict[str, dict[str, int]]:
    counts: dict[str, dict[str, int]] = {}
    for finding in findings:
        rel = _normalize_path(finding["path"])
        rule = _rule_id(finding["check_id"])
        counts.setdefault(rel, {})
        counts[rel][rule] = counts[rel].get(rule, 0) + 1
    return {rel: dict(sorted(rules.items())) for rel, rules in sorted(counts.items())}


def run_semgrep() -> tuple[list[dict], int, list[str]]:
    """Run vendored semgrep. Returns (results, files_scanned, soft_errors).

    Semgrep exits non-zero when it finds anything, so the return code is not an
    error signal — parse stdout. Absent/broken semgrep yields no stdout and
    raises, rather than silently reporting a clean tree.
    """
    if shutil.which("semgrep") is None:
        raise RuntimeError(
            "semgrep is not on PATH — refusing to report a clean tree from a "
            "scan that did not happen. Install the pinned version "
            "(`pip install semgrep==1.175.0`) and re-run."
        )
    proc = subprocess.run(  # nosec B603 - fixed argv, no shell, no user input
        [
            "semgrep",
            *_rules_args(),
            "--metrics",
            "off",
            "--no-git-ignore",
            "--quiet",
            "--json",
            *SCAN_TARGETS,
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if not (proc.stdout or "").strip():
        raise RuntimeError(
            "semgrep produced no output — the scan did not run.\n"
            f"  rc={proc.returncode}\n"
            f"  stderr={(proc.stderr or '').strip()[:500]}"
        )
    payload = json.loads(proc.stdout)
    scanned = len(payload.get("paths", {}).get("scanned", []))

    # Per-file timeouts are normal on large generated files and must not fail
    # the build, but they DO mean reduced coverage, so they are surfaced rather
    # than swallowed. A hard/config error is different: results are unreliable.
    soft: list[str] = []
    for err in payload.get("errors", []):
        etype = str(err.get("type", "")) or str(err.get("level", ""))
        if "timeout" in etype.lower():
            soft.append(f"{etype}: {err.get('path', '?')}")
            continue
        raise RuntimeError(f"semgrep reported a scan error (results unreliable): {err}")
    return payload.get("results", []), scanned, soft


def load_baseline() -> dict[str, dict[str, int]]:
    if not BASELINE_PATH.exists():
        # An absent baseline allows ZERO findings, which fails loud rather than
        # permitting everything — the safe direction for a missing file.
        return {}
    return json.loads(BASELINE_PATH.read_text(encoding="utf-8"))


def find_regressions(
    counts: dict[str, dict[str, int]],
    baseline: dict[str, dict[str, int]],
) -> list[tuple[str, str, int, int]]:
    """``(relpath, rule, found, allowed)`` per rule exceeding its baseline.

    The ratchet only shrinks: fewer findings than baseline is always clean
    (re-baseline to lock the win in). Keyed per-file-per-RULE so a new rule
    cannot ride in behind a deleted one.
    """
    out: list[tuple[str, str, int, int]] = []
    for rel, rules in sorted(counts.items()):
        allowed_rules = baseline.get(rel, {})
        for rule, n in sorted(rules.items()):
            allowed = allowed_rules.get(rule, 0)
            if n > allowed:
                out.append((rel, rule, n, allowed))
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="Semgrep ratchet lint.")
    parser.add_argument(
        "--update-baseline",
        action="store_true",
        help="Regenerate semgrep_baseline.json from the current tree.",
    )
    args = parser.parse_args()

    findings, scanned, soft_errors = run_semgrep()
    counts = counts_from_findings(findings)

    for err in soft_errors:
        print(f"semgrep_lint: WARNING — reduced coverage ({err})", file=sys.stderr)

    if scanned < MIN_FILES_SCANNED:
        print(
            f"semgrep_lint: only {scanned} file(s) scanned (expected at least "
            f"{MIN_FILES_SCANNED}) — refusing to report clean off a scan that "
            "barely happened. Check whether a scan root moved.\n"
            f"  targets: {', '.join(SCAN_TARGETS)}",
            file=sys.stderr,
        )
        return 1

    if args.update_baseline:
        BASELINE_PATH.write_text(
            json.dumps(counts, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        total = sum(sum(r.values()) for r in counts.values())
        print(
            f"semgrep_lint: baseline written — {len(counts)} file(s), "
            f"{total} finding(s) grandfathered across {scanned} scanned."
        )
        return 0

    regressions = find_regressions(counts, load_baseline())
    if regressions:
        print("NEW SEMGREP FINDINGS (not covered by baseline):")
        for rel, rule, found, allowed in regressions:
            print(f"  {rel}: {rule} = {found} finding(s), baseline allows {allowed}")
        print(
            "\nSemgrep has no project context, so most findings here are false "
            "positives — read the flagged line before acting. If it is genuinely "
            "unsafe, FIX it. If it is the sanctioned pattern (hardcoded SQL "
            "identifier + asyncpg bind params, a log message that merely "
            "mentions login), re-baseline with:\n"
            "    python scripts/ci/semgrep_lint.py --update-baseline\n"
            "and say why in the commit message.",
            file=sys.stderr,
        )
        return 1

    # Print FOUND and BASELINED separately. They are not the same number and
    # can legitimately differ — a tree sitting below its baseline is clean but
    # not yet locked in. Collapsing them into one figure is what made a stale
    # bandit baseline entry invisible during the 2026-08-28 CI audit.
    found = sum(sum(r.values()) for r in counts.values())
    baselined = sum(sum(r.values()) for r in load_baseline().values())
    tail = "" if found == baselined else "  <- re-baseline to lock the win in"
    print(
        f"semgrep_lint: clean — no new findings "
        f"({found} found / {baselined} baselined across {scanned} files "
        f"scanned; ratchet only shrinks).{tail}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
