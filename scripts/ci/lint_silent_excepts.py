#!/usr/bin/env python3
"""CI lint: no NEW silently-swallowed exceptions in production code.

Silent-failure audit follow-up (H2). A swallowed exception is invisible to
the operator when its handler body is *only* one of:

  - ``pass``                       — nothing is recorded at all
  - ``<logger>.debug(...)``        — below the prod log level AND below
                                     GlitchTip's ERROR event gate
                                     (LoggingIntegration event_level=ERROR)

Either way the failure never reaches Loki (at prod level), never creates a
GlitchTip issue, and never pages. This lint stops that category from
GROWING. It does NOT try to fix the ~existing sites in one pass — those are
captured in a per-file BASELINE (``silent_excepts_baseline.json``) and the
lint only fails when a file's count exceeds its baseline (i.e. a NEW silent
swallow was added) or a previously-clean file grows one.

The baseline is a ratchet: it may only shrink. Lower a file's number when
you convert a silent swallow into a visible one (escalate the log level,
emit a finding, re-raise) and re-run with ``--update-baseline``.

Escape hatch for a genuinely-fine new silent swallow (best-effort cleanup,
a close() in a finally-ish except): add a ``# noqa: silent-ok`` comment on
the ``except`` line or anywhere inside the handler, with a short reason.

Run:
    python scripts/ci/lint_silent_excepts.py                 # check
    python scripts/ci/lint_silent_excepts.py --update-baseline  # re-baseline

Exit 0 = no new silent swallows, exit 1 = at least one new one.
"""
from __future__ import annotations

import argparse
import ast
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
BASELINE_PATH = Path(__file__).resolve().parent / "silent_excepts_baseline.json"

# Production Python trees. Tests are intentionally excluded — a swallowed
# exception in a test asserts nothing, it isn't an operator-visibility risk.
SCAN_ROOTS = [
    (REPO_ROOT / "src" / "cofounder_agent", ("tests",)),
    (REPO_ROOT / "brain", ()),
]

OVERRIDE_MARKER = "silent-ok"


def _is_debug_call(stmt: ast.stmt) -> bool:
    """True if stmt is a bare ``<anything>.debug(...)`` expression statement."""
    if not isinstance(stmt, ast.Expr):
        return False
    call = stmt.value
    if not isinstance(call, ast.Call):
        return False
    func = call.func
    return isinstance(func, ast.Attribute) and func.attr == "debug"


def _handler_is_silent(handler: ast.ExceptHandler) -> bool:
    """True if the handler body swallows silently (pass-only or debug-only).

    A handler with any second statement (a finding emit, a re-raise, a
    state mutation, a higher-severity log) is NOT silent — the failure is
    being recorded or acted on.
    """
    body = handler.body
    if len(body) != 1:
        return False
    only = body[0]
    return isinstance(only, ast.Pass) or _is_debug_call(only)


def _handler_has_override(handler: ast.ExceptHandler, lines: list[str]) -> bool:
    """True if a ``silent-ok`` marker appears anywhere in the handler span."""
    start = handler.lineno
    end = start
    for node in ast.walk(handler):
        node_end = getattr(node, "end_lineno", None)
        if node_end is not None and node_end > end:
            end = node_end
    # lines is 0-indexed; AST linenos are 1-indexed.
    for ln in range(start, end + 1):
        if 0 <= ln - 1 < len(lines) and OVERRIDE_MARKER in lines[ln - 1]:
            return True
    return False


def scan_file(path: Path) -> int:
    """Count un-overridden silent except handlers in one file."""
    try:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
    except (SyntaxError, UnicodeDecodeError):
        return 0
    lines = source.splitlines()
    count = 0
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler) and _handler_is_silent(node):
            if not _handler_has_override(node, lines):
                count += 1
    return count


def compute_counts() -> dict[str, int]:
    """Map of ``relpath -> silent-handler count`` across all scan roots."""
    counts: dict[str, int] = {}
    for root, excluded in SCAN_ROOTS:
        if not root.exists():
            continue
        for py_file in sorted(root.rglob("*.py")):
            rel_parts = py_file.relative_to(root).parts
            if excluded and rel_parts and rel_parts[0] in excluded:
                continue
            n = scan_file(py_file)
            if n:
                rel = str(py_file.relative_to(REPO_ROOT)).replace("\\", "/")
                counts[rel] = n
    return counts


def load_baseline() -> dict[str, int]:
    if not BASELINE_PATH.exists():
        return {}
    return json.loads(BASELINE_PATH.read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--update-baseline",
        action="store_true",
        help="Regenerate silent_excepts_baseline.json from the current tree.",
    )
    args = parser.parse_args()

    counts = compute_counts()

    if args.update_baseline:
        BASELINE_PATH.write_text(
            json.dumps(dict(sorted(counts.items())), indent=2) + "\n",
            encoding="utf-8",
        )
        total = sum(counts.values())
        print(
            f"lint_silent_excepts: baseline written — "
            f"{len(counts)} files, {total} silent handlers grandfathered."
        )
        return 0

    baseline = load_baseline()
    regressions: list[str] = []
    for rel, n in sorted(counts.items()):
        allowed = baseline.get(rel, 0)
        if n > allowed:
            regressions.append(
                f"  {rel}: {n} silent except handler(s), baseline allows {allowed}"
            )

    if regressions:
        print("NEW SILENT EXCEPTION HANDLERS (not in baseline):")
        for r in regressions:
            print(r)
        print(
            "\nA handler whose body is only `pass` or `<logger>.debug(...)` "
            "swallows the failure below the operator's visibility "
            "(prod log level + GlitchTip ERROR gate). Make it visible — "
            "escalate the log level, emit_finding(severity>=warn), or "
            "re-raise. If the swallow is genuinely fine (best-effort "
            "cleanup), add `# noqa: silent-ok <reason>` on the except line. "
            "If you intentionally reduced a count, re-run with "
            "--update-baseline."
        )
        return 1

    total = sum(counts.values())
    print(
        f"lint_silent_excepts: clean — no new silent handlers "
        f"({total} baselined across {len(counts)} files; ratchet only shrinks)."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
