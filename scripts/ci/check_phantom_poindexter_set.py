#!/usr/bin/env python3
"""Fail the build if any file documents the phantom ``poindexter set`` command.

The Poindexter CLI registers a ``settings`` command group
(``src/cofounder_agent/poindexter/cli/app.py`` + ``.../cli/settings.py``). The
real command is::

    poindexter settings set <key> <value>            # plain key
    poindexter settings set <key> <value> --secret   # encrypted key
    poindexter settings set <key> <value> --allow-new  # unseeded key

There is **no top-level ``poindexter set``** — running it errors with
``No such command 'set'``. Despite that, operator docs/runbooks, runtime error
strings, and seeded ``app_settings`` descriptions kept documenting a bare
``poindexter set ...``. It was reconciled twice (#1556 for the ``--secret``
commands, #1562 for everything else) and crept back each time. This guard makes
the regression fail loud so it stops recurring.

What it flags
=============

The phantom form: ``poindexter set`` immediately followed by a space, a
backtick, a ``<``, a ``"``, or end-of-line. That distinguishes the phantom from
the legitimate neighbours it must NOT flag:

==============================  ======  ========================================
String                          Verdict Why
==============================  ======  ========================================
``poindexter set foo``          FAIL    bare ``set`` subcommand (does not exist)
```` `poindexter set` ````      FAIL    backtick-wrapped phantom reference
``poindexter set <key>``        FAIL    doc placeholder for the phantom command
``poindexter settings set foo`` PASS    the real command group
``poindexter setup``            PASS    the real bootstrap command
``poindexter set-secret``       PASS    legacy spelling, harmless
``poindexter set_secret``       PASS    legacy spelling, harmless
==============================  ======  ========================================

Stdlib-only on purpose — runs in <2s in CI with no install step.

Exemptions
==========

Two escape hatches, both narrow:

* ``_ALLOWLIST_PATHS`` — the handful of files that carry the phantom string as
  *data*, not as operator guidance: the #1562 remediation migration (its
  ``_DESCRIPTIONS`` map stores the old phantom text it reverts), the
  auto-generated ``CHANGELOG.md`` (release-please copies the fix's commit
  subject verbatim), and this checker + its unit test (both necessarily contain
  the phantom form).
* An inline ``allow-phantom-set`` marker — drop it in a same-line comment
  (``# allow-phantom-set``, ``<!-- allow-phantom-set -->``, …) on any line that
  must mention the phantom form on purpose (e.g. a doc describing this very
  bug). The whole line is then skipped.

Exit codes: ``0`` clean, ``1`` one or more phantom references found.
"""

from __future__ import annotations

import io
import re
import subprocess
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

# Windows shells default stdout to cp1252, which crashes on the em-dash and
# arrow characters this script's output (and matched lines) can contain. Force
# UTF-8 so the lint runs cleanly in CI (Linux) and on the operator's
# PowerShell host alike. Mirrors scripts/ci/check_public_mirror_safety.py.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
else:  # pragma: no cover — Python < 3.7 doesn't have reconfigure
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------

# ``poindexter set`` only when the next character makes it the bare ``set``
# subcommand: a space (``poindexter set foo``), a backtick (```` `poindexter
# set` ````), a ``<`` (``poindexter set <key>``), a ``"``, or end-of-line.
# The positive lookahead is what keeps the legitimate neighbours clean:
# ``settings`` (next char ``t``), ``setup`` (``u``), ``set-secret`` (``-``),
# and ``set_secret`` (``_``) all fail the lookahead, so none are flagged.
_PHANTOM_RE = re.compile(r'poindexter set(?=[ `<"]|$)')

# Drop this token in a same-line comment to exempt an intentional mention.
_ALLOW_MARKER = "allow-phantom-set"

# Path of this checker, relative to the repo root — used to skip self-scanning.
_SELF_PATH = "scripts/ci/check_phantom_poindexter_set.py"

# Files that legitimately carry the phantom string as data, not guidance.
# Keep this tight: every entry must reference the phantom form for a structural
# reason, never as an operator instruction.
_ALLOWLIST_PATHS: frozenset[str] = frozenset(
    {
        # This checker defines the detection regex + the canonical-fix message.
        _SELF_PATH,
        # Its unit test feeds the predicate phantom-form fixtures.
        "src/cofounder_agent/tests/unit/scripts/test_check_phantom_poindexter_set.py",
        # The #1562 remediation migration embeds the phantom string as the
        # old-text it matches and reverts in app_settings descriptions.
        "src/cofounder_agent/services/migrations/"
        "20260613_120000_fix_poindexter_set_in_app_settings_descriptions.py",
        # Auto-generated by release-please: it copies the phantom-fix commit
        # subject ("correct phantom `poindexter set` ... to `poindexter
        # settings set`") verbatim, and generated content can't carry an
        # inline marker. Mirrors the leak guard's CHANGELOG special-casing.
        "CHANGELOG.md",
    }
)


def line_has_phantom_set(line: str) -> bool:
    """Return True if *line* contains the phantom ``poindexter set`` command.

    Pure predicate over a single line — no allow-list or marker logic (those
    live in :func:`scan`). This is the unit the contract test feeds strings to.
    """
    return _PHANTOM_RE.search(line) is not None


# ---------------------------------------------------------------------------
# File classification
# ---------------------------------------------------------------------------

# Text extensions worth scanning. The phantom command surfaces in operator
# docs (.md), seed/runtime code (.py), config (.yml/.yaml/.toml), SQL seeds
# (.sql), shell/PowerShell helpers (.sh/.ps1), and JSON. Binary types can't
# carry operator guidance, so they're skipped. Mirrors the leak guard's set.
_TEXT_EXTS = frozenset(
    {
        ".py",
        ".md",
        ".json",
        ".yml",
        ".yaml",
        ".toml",
        ".sh",
        ".ps1",
        ".sql",
        ".txt",
        ".cfg",
        ".ini",
        ".env",
    }
)


def _is_text_file(rel_path: str) -> bool:
    """Restrict scanning to known text extensions + a couple of special cases."""
    name = Path(rel_path).name
    if name.startswith("Dockerfile"):
        return True
    if name.startswith(".") and "." not in name[1:]:
        # Bare dotfiles (.gitignore, .env) ship as text but have no suffix.
        return True
    return Path(rel_path).suffix.lower() in _TEXT_EXTS


def _list_tracked_files(repo_root: Path) -> list[str]:
    """Return tracked files via ``git ls-files`` — only what's committed ships.

    Using git (not a filesystem walk) means ``.venv``/``__pycache__``/build
    artifacts are excluded for free, and it matches the ``git grep`` the PR's
    verify step runs.
    """
    out = subprocess.check_output(["git", "ls-files"], cwd=repo_root, text=True)
    return [line for line in out.splitlines() if line.strip()]


# ---------------------------------------------------------------------------
# Scanning
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Hit:
    file: str
    line_no: int
    line_text: str


def scan(repo_root: Path, rel_paths: Iterable[str] | None = None) -> list[Hit]:
    """Scan *rel_paths* (default: all tracked files) for the phantom command.

    *rel_paths* is injectable so the contract test can drive ``scan`` over a
    temp directory without a git repo. Allow-listed paths, non-text files, and
    lines carrying the ``allow-phantom-set`` marker are skipped.
    """
    if rel_paths is None:
        rel_paths = _list_tracked_files(repo_root)
    hits: list[Hit] = []
    for rel in rel_paths:
        if rel in _ALLOWLIST_PATHS:
            continue
        if not _is_text_file(rel):
            continue
        try:
            text = (repo_root / rel).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            # Unreadable / non-UTF-8 files carry no operator guidance.
            continue
        for line_no, line in enumerate(text.splitlines(), start=1):
            if _ALLOW_MARKER in line:
                continue
            if line_has_phantom_set(line):
                hits.append(Hit(rel, line_no, line.rstrip()))
    return hits


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

_CANONICAL_FIX = (
    "There is no top-level `poindexter set`. Use the settings group:\n"
    "    poindexter settings set <key> <value>             # plain key\n"
    "    poindexter settings set <key> <value> --secret    # encrypted key\n"
    "    poindexter settings set <key> <value> --allow-new # unseeded key\n"
    "For an intentional mention (e.g. describing this bug), add an "
    "`allow-phantom-set` marker in a same-line comment."
)


def _format_hit(hit: Hit) -> str:
    snippet = hit.line_text
    if len(snippet) > 100:
        snippet = snippet[:97] + "..."
    return f"  {hit.file}:{hit.line_no}\n    line: {snippet}"


def main() -> int:
    repo_root = Path(
        subprocess.check_output(["git", "rev-parse", "--show-toplevel"], text=True).strip()
    )
    hits = scan(repo_root)
    if not hits:
        print(
            "[phantom-poindexter-set] OK — no phantom `poindexter set` references in tracked files."
        )
        return 0
    n_files = len({h.file for h in hits})
    print(
        f"[phantom-poindexter-set] FAIL — {len(hits)} phantom "
        f"`poindexter set` reference(s) across {n_files} file(s):"
    )
    print()
    for h in sorted(hits, key=lambda hit: (hit.file, hit.line_no)):
        print(_format_hit(h))
    print()
    print(_CANONICAL_FIX)
    return 1


if __name__ == "__main__":
    sys.exit(main())
