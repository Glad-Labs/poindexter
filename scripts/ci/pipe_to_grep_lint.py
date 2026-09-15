#!/usr/bin/env python3
"""Forbid ``echo "$var" | grep -q …`` in workflows and pipefail shell scripts.

Under ``set -o pipefail`` (GitHub's default for ``shell: bash`` steps, and set
explicitly in our deploy and CI scripts) that pair is a classifier that lies on
big inputs: ``grep -q`` exits at its first match, and once the echoed text is
larger than the pipe buffer ``echo`` is still writing and takes SIGPIPE, so the
pipeline's status is 141 and the ``if`` takes the "no match" branch. Small
inputs never trip it. It made the REQUIRED ``test-backend`` check pass in 5 s
on a 940-file PR having run nothing (stack#3626, 2026-09-10), and the same
shape sat in the deploy script's rebuild map and in ``security.yml``'s scan
classifier. The fix is always a here-string (``grep -qE PAT <<<"$var"``) or
``grep -c``, which reads the whole input.

Scope: every ``.github/workflows/*.yml`` (steps run under pipefail by default),
and every ``scripts/**/*.sh`` that sets ``pipefail``. A shell script without
pipefail gets the pipeline status of ``grep`` and is not flagged.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib_scan_floor import require_dir, require_scanned  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
LINT = "pipe_to_grep_lint"
WORKFLOWS = REPO / ".github" / "workflows"
SCRIPTS = REPO / "scripts"

# Any producer that writes a SHELL VARIABLE into an early-exit grep, not just
# `echo`. Widened 2026-09-15 after stack#3779: the offsite coverage check used
# `printf '%s\n' "${listing}" | grep -qxF` and this lint was blind to it, so a
# 23,763-line listing made a present bootstrap.toml read as missing and paged
# CRITICAL on the backup a restore depends on. The producer was never the
# point — writing an unbounded variable into a grep that exits early is.
#
# Scoped to `"$var"` expansions deliberately: a fixed-output command
# (`docker ps | grep -q`, `uname | grep -q`) writes far less than the 64 KB
# pipe buffer and finishes before grep can exit, so it cannot take SIGPIPE.
# Widening to every command would bury the real signal in those.
PATTERN = re.compile(
    r"""\b(?:echo|printf)\b[^|]*"\$\{?[A-Za-z_][A-Za-z0-9_]*\}?"[^|]*\|\s*grep\s+-[A-Za-z]*q"""
)
PIPEFAIL = re.compile(r"set\s+-[a-zA-Z]*o\s+pipefail|set\s+-o\s+pipefail|pipefail")


def _offenders(path: Path) -> list[tuple[int, str]]:
    out: list[tuple[int, str]] = []
    for lineno, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
        stripped = line.lstrip()
        if stripped.startswith("#"):
            continue
        if PATTERN.search(line):
            out.append((lineno, stripped[:140]))
    return out


def main() -> int:
    require_dir(WORKFLOWS, lint=LINT)
    require_dir(SCRIPTS, lint=LINT)
    scanned = 0
    findings: list[str] = []
    for wf in sorted(WORKFLOWS.glob("*.yml")):
        scanned += 1
        for lineno, text in _offenders(wf):
            findings.append(f"{wf.relative_to(REPO)}:{lineno}: {text}")
    for sh in sorted(SCRIPTS.rglob("*.sh")):
        scanned += 1
        if not PIPEFAIL.search(sh.read_text(encoding="utf-8", errors="replace")):
            continue
        for lineno, text in _offenders(sh):
            findings.append(f"{sh.relative_to(REPO)}:{lineno}: {text}")
    require_scanned(scanned, lint=LINT, what="workflow/shell files", roots=(WORKFLOWS, SCRIPTS))
    if findings:
        print(f"{LINT}: {len(findings)} `echo/printf \"$var\" | grep -q` classifier(s) under pipefail -- these report")
        print("  \"no match\" on large inputs (SIGPIPE, exit 141). Use `grep -qE PAT <<<\"$var\"` or `grep -c`:")
        for f in findings:
            print(f"    {f}")
        return 1
    print(f"{LINT}: clean -- no pipe-to-grep classifiers under pipefail ({scanned} workflow/shell files scanned).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
