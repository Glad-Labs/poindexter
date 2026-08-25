#!/usr/bin/env python3
"""CI lint: no NEW bandit security findings (the bandit ratchet).

Bandit is a **textual/AST pattern matcher with no dataflow analysis**. It cannot
tell "user input reaches SQL text" (a real bug) from "hardcoded column-list
constant + asyncpg bind params" (this codebase's sanctioned ``services/``
pattern). That makes it a poor issue-filer and a fine ratchet.

Why this exists
---------------
The weekly ``codebase-audit`` ops session used to file one GitHub issue per
bandit finding. It produced 91 issues; every one examined was a false positive
(#2594-#2623 were all closed as such by #2644), and they buried the 18 genuine
engineering issues under three pages of noise — the real backlog was invisible
past page one. Issue-per-finding is the wrong shape for a rule that is mostly
noise on this codebase. So bandit joins the three existing ratchets
(``lint_silent_excepts`` / ``adapter_purity_lint`` / ``atom_independence_lint``):
existing findings are grandfathered in ``bandit_baseline.json``, CI blocks only
on a **net-new** one, and zero issues get filed.

Baseline shape — per-file, per-RULE
-----------------------------------
The other three ratchets each guard exactly one rule, so ``{relpath: count}`` is
unambiguous. Bandit multiplexes ~30 rules of differing severity through a single
scan, so a bare per-file count would let a brand-new ``B605`` (shell injection)
ride in free as long as the same file shed a trivial ``B404``. Hence
``{relpath: {test_id: count}}`` — a new *rule* in an already-baselined file is
still a regression.

Escape hatch — bandit's own ``# nosec``
---------------------------------------
No custom marker: bandit natively honours ``# nosec B608`` and the scan already
respects it, so a justified exemption is annotated at the source line and simply
never reaches this lint. Placement gotcha (verified against bandit 1.9.x): the
comment must fall within the flagged node's **line span**, so for a multi-line
triple-quoted f-string it belongs on the **closing** ``\"\"\"`` line — a comment
on the line above the statement does NOT suppress.

Reproducibility
---------------
A baseline is only meaningful against the bandit that produced it; a version
bump can add rules and turn a clean tree red. CI installs the exact
``poetry.lock`` version, and ``test_bandit_lint.py`` fails if the workflow's pin
drifts from the lock — so a dependabot bump surfaces here, loudly, instead of as
a mystery red on an unrelated PR.

Public-mirror safety
--------------------
``bandit_baseline.json`` ships in the public mirror, so mirror-stripped operator
files must never enter it (same reasoning as ``adapter_purity_lint``'s
``mcp-server-gladlabs`` exclusion) — see ``PRIVATE_OVERLAY_FILES``.

Run:
    python scripts/ci/bandit_lint.py                    # check
    python scripts/ci/bandit_lint.py --update-baseline  # re-baseline

Exit 0 = no new findings, exit 1 = at least one new finding.
"""
from __future__ import annotations

import argparse
import json
import subprocess  # nosec B404 - invoking our own pinned bandit, no user input
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
BASELINE_PATH = Path(__file__).resolve().parent / "bandit_baseline.json"

# Scan roots — parity with the ops session's historical BANDIT_TARGETS so the
# ratchet inherits exactly the surface that was being issue-filed.
BANDIT_TARGETS = (
    "brain/",
    "scripts/",
    "src/cofounder_agent/services/",
    "src/cofounder_agent/routes/",
)

# Severity floor — parity with the ops session's `-ll` (medium and above).
SEVERITY_FLAG = "-ll"

# Operator-overlay files that live under the scan roots but are `git rm`'d by
# scripts/sync-to-github.sh before the public mirror is built. This baseline
# ships in that mirror, so a finding in one of these must never enter it — the
# path itself would leak the private file's existence. Mirrors
# adapter_purity_lint's exclusion of mcp-server-gladlabs/.
PRIVATE_OVERLAY_FILES = frozenset(
    {
        "src/cofounder_agent/services/operator_overrides.py",
        "src/cofounder_agent/services/operator_leak_patterns.py",
        "src/cofounder_agent/services/taps/claude_code_sessions.py",
        "scripts/kuma_bootstrap.py",
        "scripts/glitchtip_audit.py",
        "scripts/migrate-poindexter-rename.sh",
        "scripts/ci/check_public_mirror_safety.py",
        "scripts/regen-app-settings-doc.py",
    }
)


def _normalize_path(filename: str) -> str:
    """Bandit echoes back ``<cli-target> + os.sep + subpath``, so on Windows a
    nested file arrives mixed-separator (``scripts/ops\\find_phantom.py``). The
    baseline is committed here and compared on Linux CI, so normalize to
    forward slashes and keep it repo-relative."""
    raw = filename.replace("\\", "/")
    path = Path(raw)
    if path.is_absolute():
        try:
            raw = str(path.relative_to(REPO_ROOT))
        except ValueError:
            raw = path.name
    return raw.replace("\\", "/")


def counts_from_findings(findings: list[dict]) -> dict[str, dict[str, int]]:
    """Map ``relpath -> {test_id: count}`` from raw bandit JSON results,
    normalizing paths and dropping mirror-stripped private overlay files."""
    counts: dict[str, dict[str, int]] = {}
    for finding in findings:
        rel = _normalize_path(finding["filename"])
        if rel in PRIVATE_OVERLAY_FILES:
            continue
        counts.setdefault(rel, {})
        test_id = finding["test_id"]
        counts[rel][test_id] = counts[rel].get(test_id, 0) + 1
    return {rel: dict(sorted(rules.items())) for rel, rules in sorted(counts.items())}


def run_bandit() -> list[dict]:
    """Run the pinned bandit over the scan roots and return its raw results.

    Bandit exits 1 when it finds anything, so the return code is not an error
    signal — parse stdout instead. A missing/broken bandit yields no stdout;
    that fails loud rather than silently reporting a clean tree (which would
    turn this gate into a no-op).
    """
    proc = subprocess.run(  # nosec B603 - fixed argv, no shell, no user input
        [
            sys.executable, "-m", "bandit", "-r", *BANDIT_TARGETS,
            "-q", SEVERITY_FLAG, "-f", "json",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if not (proc.stdout or "").strip():
        raise RuntimeError(
            "bandit produced no output — it is probably not installed in this "
            f"interpreter ({sys.executable}).\n"
            f"  rc={proc.returncode}\n  stderr={(proc.stderr or '').strip()[:500]}\n"
            "Install it (`poetry install --with dev`, or `pip install "
            "bandit==<poetry.lock version>`) and re-run. Refusing to report a "
            "clean tree from a scan that did not happen."
        )
    payload = json.loads(proc.stdout)
    errors = payload.get("errors") or []
    if errors:
        raise RuntimeError(f"bandit reported scan errors (results unreliable): {errors}")
    return payload.get("results", [])


def compute_counts() -> dict[str, dict[str, int]]:
    """Per-file, per-rule finding counts for the current tree."""
    return counts_from_findings(run_bandit())


def load_baseline() -> dict[str, dict[str, int]]:
    if not BASELINE_PATH.exists():
        return {}
    return json.loads(BASELINE_PATH.read_text(encoding="utf-8"))


def find_regressions(
    counts: dict[str, dict[str, int]],
    baseline: dict[str, dict[str, int]],
) -> list[tuple[str, str, int, int]]:
    """Return ``(relpath, test_id, found, allowed)`` for every rule whose count
    exceeds its baseline. The ratchet only shrinks: fewer findings than the
    baseline is always clean (re-baseline to lock the win in)."""
    out: list[tuple[str, str, int, int]] = []
    for rel, rules in sorted(counts.items()):
        allowed_rules = baseline.get(rel, {})
        for test_id, n in sorted(rules.items()):
            allowed = allowed_rules.get(test_id, 0)
            if n > allowed:
                out.append((rel, test_id, n, allowed))
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="Bandit ratchet lint.")
    parser.add_argument(
        "--update-baseline",
        action="store_true",
        help="Regenerate bandit_baseline.json from the current tree.",
    )
    args = parser.parse_args()

    counts = compute_counts()
    n_files = len(counts)
    total = sum(sum(r.values()) for r in counts.values())

    if args.update_baseline:
        BASELINE_PATH.write_text(json.dumps(counts, indent=2) + "\n", encoding="utf-8")
        print(
            f"bandit_lint: baseline written - {n_files} files, "
            f"{total} finding(s) grandfathered."
        )
        return 0

    regressions = find_regressions(counts, load_baseline())
    if regressions:
        print("NEW BANDIT FINDING (not in baseline):")
        for rel, test_id, n, allowed in regressions:
            print(f"  {rel}: {test_id} = {n} finding(s), baseline allows {allowed}")
        print(
            "\nBandit has no dataflow analysis, so it flags textual patterns - "
            "most findings here are false positives. Read the flagged line and "
            "decide:\n"
            "  * genuinely safe -> annotate it `# nosec <RULE> - <why it's safe>`. "
            "The comment must sit INSIDE the flagged node's line span: for a "
            'multi-line triple-quoted f-string that is the CLOSING """ line, not '
            "the line above.\n"
            "  * genuinely unsafe -> fix the code. Do NOT annotate it away.\n"
            "If you intentionally REMOVED findings, re-run with "
            "--update-baseline to lock the win in."
        )
        return 1

    print(
        f"bandit_lint: clean - no new findings "
        f"({total} baselined across {n_files} files; ratchet only shrinks)."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
