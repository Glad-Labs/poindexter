#!/usr/bin/env python3
"""Lint tracked PowerShell scripts for the BOM-less non-ASCII trap.

Why this exists
---------------
Windows PowerShell 5.1 (still the default ``powershell.exe`` on Windows)
decodes a ``.ps1`` file that has **no byte-order mark** using the system
ANSI code page (Windows-1252), *not* UTF-8. When such a file contains a
multi-byte UTF-8 character -- e.g. an em-dash ``U+2014`` (bytes
``E2 80 94``) -- the three bytes are mis-decoded as three Windows-1252
characters whose last one is ``U+201D`` (a "smart" closing double-quote).
PowerShell accepts smart-quotes as string delimiters, so the stray quote
prematurely terminates the surrounding string, desyncs brace/token
parsing, and the script dies at parse time with a misleading error like::

    Unexpected token '}' in expression or statement.

This bit ``scripts/deploy-worker.ps1`` (em-dashes in its ``Fail`` strings):
running it under Windows PowerShell 5.1 failed to parse before a single
line executed, reporting a phantom brace error 3 lines downstream of the
real culprit. PowerShell 7+ reads BOM-less files as UTF-8 and is immune,
but the scripts must keep working under the shipped 5.1 too.

Policy
------
A tracked PowerShell script (``*.ps1`` / ``*.psm1`` / ``*.psd1``) must be
either:

- **pure ASCII** (no byte > 0x7F), or
- **UTF-8 with a BOM** (``EF BB BF``), which 5.1 honours.

Anything else (non-ASCII bytes with no BOM) is an offender.

Scope
-----
- Walks the working tree (no git history), skipping the standard build /
  cache dirs the rest of CI also skips.
- Reads each candidate in **binary** mode (byte-accurate; text mode would
  silently decode away the very bytes we are policing).
- Fails fast with a single summary report listing each offender, its
  non-ASCII byte count, the first offending line, and the distinct
  codepoints -- so a contributor can find and ASCII-ify them.

Exit codes
----------
- ``0``: every tracked PowerShell script is ASCII or UTF-8-with-BOM.
- ``1``: one or more files carry non-ASCII bytes without a BOM.
- ``2``: invocation / IO error (treated as test failure by CI).

Wired into CI as a step in ``.github/workflows/security.yml``.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Mirror the skip list the rest of CI uses (Trivy / gitleaks / the shell
# line-ending linter). Anything under these prefixes is third-party or
# generated and not our convention problem to police.
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

_PS_SUFFIXES = (".ps1", ".psm1", ".psd1")

_UTF8_BOM = b"\xef\xbb\xbf"
_UTF16_BOMS = (b"\xff\xfe", b"\xfe\xff")


def _should_skip(rel_path: str) -> bool:
    rel_norm = rel_path.replace("\\", "/")
    if any(rel_norm.startswith(prefix) for prefix in _SKIP_DIR_PREFIXES):
        return True
    # Skip nested vendored trees too, not just top-level ones.
    return any(f"/{prefix}" in f"/{rel_norm}" for prefix in _SKIP_DIR_PREFIXES)


def _iter_ps_files(root: Path):
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix.lower() not in _PS_SUFFIXES:
            continue
        rel = path.relative_to(root).as_posix()
        if _should_skip(rel):
            continue
        yield path, rel


def _has_bom(data: bytes) -> bool:
    return data.startswith(_UTF8_BOM) or data.startswith(_UTF16_BOMS)


def _scan(data: bytes) -> tuple[int, int, list[str]]:
    """Return ``(non_ascii_count, first_offending_line, codepoints)``.

    ``first_offending_line`` is 1-based (``\\n`` count before the first
    non-ASCII byte). ``codepoints`` is the sorted set of distinct
    non-ASCII codepoints, best-effort UTF-8 decode.
    """
    count = sum(1 for b in data if b > 0x7F)
    first_line = 0
    for i, b in enumerate(data):
        if b > 0x7F:
            first_line = data.count(b"\n", 0, i) + 1
            break
    try:
        text = data.decode("utf-8")
        cps = sorted({f"U+{ord(c):04X}" for c in text if ord(c) > 0x7F})
    except UnicodeDecodeError:
        cps = ["<invalid-utf8>"]
    return count, first_line, cps


def main(argv: list[str]) -> int:
    root = Path(argv[1]) if len(argv) > 1 else Path.cwd()
    if not root.is_dir():
        print(f"ERROR: not a directory: {root}", file=sys.stderr)
        return 2

    offenders: list[tuple[str, int, int, list[str]]] = []
    scanned = 0
    for path, rel in _iter_ps_files(root):
        scanned += 1
        try:
            data = path.read_bytes()
        except OSError as exc:
            print(f"ERROR: could not read {rel}: {exc}", file=sys.stderr)
            return 2
        if _has_bom(data):
            continue
        count, first_line, cps = _scan(data)
        if count:
            offenders.append((rel, count, first_line, cps))

    if offenders:
        print(
            f"FAIL: {len(offenders)} PowerShell script(s) carry non-ASCII "
            f"bytes without a BOM (scanned {scanned} files):",
            file=sys.stderr,
        )
        for rel, count, first_line, cps in offenders:
            print(
                f"  - {rel} ({count} non-ASCII bytes; first at line "
                f"{first_line}; chars: {' '.join(cps)})",
                file=sys.stderr,
            )
        print(
            "\nWhy: Windows PowerShell 5.1 decodes BOM-less .ps1 files as "
            "ANSI (Windows-1252), turning multi-byte UTF-8 chars (e.g. "
            "em-dash U+2014) into string-delimiter smart-quotes -> the "
            "script fails to parse before it runs.\n"
            "Fix: replace non-ASCII chars with ASCII equivalents (em-dash "
            "-> '-', ellipsis -> '...', smart-quotes -> straight quotes), "
            "or re-save the file as UTF-8 with a BOM.\n"
            "See docs/operations/troubleshooting.md for the long-form "
            "explainer.",
            file=sys.stderr,
        )
        return 1

    print(f"OK: {scanned} PowerShell script(s) are ASCII or UTF-8-with-BOM.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
