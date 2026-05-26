#!/usr/bin/env python3
"""Lint tracked shell scripts for CRLF line endings.

Why this exists
---------------
``DbBackupJob`` runs ``scripts/db-backup-local.sh`` inside the worker
container every 12h. The script starts with ``set -euo pipefail`` on
line 17. When the file is checked out with CRLF endings, bash parses
the ``-o`` argument as ``pipefail\\r`` and emits::

    set: pipefail: invalid option name

The job then crashes -- 15 occurrences flooded GlitchTip before the
root cause was tracked down. The ``.gitattributes`` rule
``*.sh text eol=lf`` normalizes future checkouts, but an existing
Windows working tree from before the rule landed can still carry
CRLF. This linter is the regression gate: any tracked ``*.sh`` /
``*.bash`` file with ``\\r`` in its bytes fails CI.

Scope
-----
- Walks the working tree (no git history).
- Matches ``*.sh`` and ``*.bash`` files anywhere in the repo, skipping
  the standard build / cache dirs the rest of CI also skips.
- Reads each candidate in **binary** mode (Python's text mode on
  Windows silently normalizes CRLF -> LF, which would defeat the
  check; binary mode preserves bytes).
- Fails fast with a single summary report listing each offender and
  its CR count, so a contributor can run ``dos2unix`` (or
  ``git add --renormalize -- '*.sh' && git commit``) and re-push.

Exit codes
----------
- ``0``: every tracked shell script is LF-only.
- ``1``: one or more files contain ``\\r`` bytes.
- ``2``: invocation / IO error (treated as test failure by CI).

Wired into CI as a step in ``.github/workflows/security.yml``.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Mirror the skip list the rest of CI uses (Trivy / gitleaks). Anything
# under these prefixes is third-party or generated and not our convention
# problem to police.
_SKIP_DIR_PREFIXES = (
    ".git/",
    ".venv/",
    "node_modules/",
    "__pycache__/",
    ".next/",
    ".vercel/",
    "dist/",
    "build/",
    ".pytest_cache/",
    ".claude/",
)

_SHELL_SUFFIXES = (".sh", ".bash")


def _should_skip(rel_path: str) -> bool:
    rel_norm = rel_path.replace("\\", "/")
    if any(rel_norm.startswith(prefix) for prefix in _SKIP_DIR_PREFIXES):
        return True
    # Skip top-level node_modules etc. as well as nested ones.
    return any(f"/{prefix}" in f"/{rel_norm}" for prefix in _SKIP_DIR_PREFIXES)


def _iter_shell_files(root: Path):
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix not in _SHELL_SUFFIXES:
            continue
        rel = path.relative_to(root).as_posix()
        if _should_skip(rel):
            continue
        yield path, rel


def main(argv: list[str]) -> int:
    root = Path(argv[1]) if len(argv) > 1 else Path.cwd()
    if not root.is_dir():
        print(f"ERROR: not a directory: {root}", file=sys.stderr)
        return 2

    offenders: list[tuple[str, int]] = []
    scanned = 0
    for path, rel in _iter_shell_files(root):
        scanned += 1
        try:
            data = path.read_bytes()
        except OSError as exc:
            print(f"ERROR: could not read {rel}: {exc}", file=sys.stderr)
            return 2
        cr_count = data.count(b"\r")
        if cr_count:
            offenders.append((rel, cr_count))

    if offenders:
        print(
            f"FAIL: {len(offenders)} shell script(s) have CRLF line endings "
            f"(scanned {scanned} files):",
            file=sys.stderr,
        )
        for rel, cr_count in offenders:
            print(f"  - {rel} ({cr_count} CR bytes)", file=sys.stderr)
        print(
            "\nFix: `git add --renormalize -- '*.sh' '*.bash' && git commit -m "
            "'chore: re-normalize shell scripts to LF'`.\n"
            "See docs/operations/troubleshooting.md "
            "for the long-form explainer.",
            file=sys.stderr,
        )
        return 1

    print(f"OK: {scanned} shell script(s) are LF-only.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
