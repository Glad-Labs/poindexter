#!/usr/bin/env python3
"""CI lint: operator-facing failure strings must name the exception's cause.

``str()`` on the exception classes this stack actually hits under failure is
the EMPTY STRING — ``httpx.ReadTimeout``, ``httpx.ConnectTimeout``,
``asyncio.TimeoutError`` carry their meaning in the type, not the message. So
a ``JobResult(detail=str(exc))`` or ``emit_finding(body=f"fetch failed:
{exc}")`` produces a job_failure page / Discord finding that names no cause.
That exact shape hid the featured-image render timeouts for weeks
(poindexter#3229), was re-introduced the very day the shared helper shipped,
and had been independently re-hand-rolled as ``{type(e).__name__}: {e}`` in
four jobs — the tell (per the detail-leak lint before it) that the durable
fix is a ratchet, not another manual sweep.

The fix is one call: ``utils.exception_format.describe_exception(exc)`` —
``ReadTimeout`` for empty messages, ``RuntimeError: gpu busy`` otherwise.

## What is flagged

Inside an ``except ... as <name>:`` handler, a call to ``JobResult(...)``
with ``detail=`` — or ``emit_finding(...)`` with ``body=`` — whose value
interpolates ``<name>`` bare:

  - ``detail=str(exc)`` / ``body=str(exc)``
  - ``detail=f"query failed: {exc}"`` (any f-string whose ``{...}`` is the
    bare name, with or without ``!r``/``!s``, or ``{str(exc)}``)
  - the hand-rolled ``f"{type(exc).__name__}: {exc}"`` pair (its second
    piece is a bare interpolation — and the hand-rolled form renders
    ``ReadTimeout: `` with a dangling colon on empty messages anyway)

Deliberate ATTRIBUTE access (``{exc.args[0]}``, ``{exc.detail}``) is NOT
flagged: extracting a specific field is an intent, not the trap. Calls
already wrapped in ``describe_exception(...)`` never match (the argument is
inside a Call, not a bare interpolation).

Scope is ``src/cofounder_agent`` minus ``tests`` — where ``JobResult`` and
``emit_finding`` live. ``brain/`` is a standalone daemon that cannot import
``utils.exception_format`` and is out of scope.

## Ratchet posture

The tree is clean today, so this is a FAIL-ON-ANY guard (no baseline file to
drift). Escape hatch for a deliberate, reviewed case: add
``# noqa: cause-ok <reason>`` on the offending line.

Run:
    python scripts/ci/lint_exception_cause.py    # exit 0 = clean, 1 = violation
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib_scan_floor import require_scanned  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]

# Production tree that constructs JobResult / emit_finding. Tests are
# excluded — a test asserting a detail string is pinning behavior, not
# paging an operator.
SCAN_ROOTS: list[tuple[Path, tuple[str, ...]]] = [
    (REPO_ROOT / "src" / "cofounder_agent", ("tests",)),
]

OVERRIDE_MARKER = "cause-ok"

# (callable name, keyword) pairs that reach an operator: JobResult.detail
# lands in job_run audit rows + the scheduler's job_failure escalation;
# emit_finding.body is the Discord-routed finding text.
TARGETS: frozenset[tuple[str, str]] = frozenset(
    {("JobResult", "detail"), ("emit_finding", "body")}
)


def _call_name(call: ast.Call) -> str:
    func = call.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


def _is_bare_str_of(node: ast.expr, names: set[str]) -> bool:
    """True for ``str(<name>)``."""
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "str"
        and len(node.args) == 1
        and isinstance(node.args[0], ast.Name)
        and node.args[0].id in names
    )


def _value_interpolates_bare(node: ast.expr, names: set[str]) -> bool:
    """True when the kwarg value stringifies a handler name bare.

    Matches ``str(name)`` and f-strings whose ``FormattedValue`` is exactly
    the bare ``Name`` (any conversion) or ``str(Name)``. Attribute/subscript
    forms (``{e.args[0]}``) are deliberate field extraction and don't match.
    """
    if _is_bare_str_of(node, names):
        return True
    if isinstance(node, ast.JoinedStr):
        for part in node.values:
            if not isinstance(part, ast.FormattedValue):
                continue
            inner = part.value
            if isinstance(inner, ast.Name) and inner.id in names:
                return True
            if _is_bare_str_of(inner, names):
                return True
    return False


def _has_override(call: ast.Call, lines: list[str]) -> bool:
    start = call.lineno
    end = call.end_lineno or start
    for ln in range(start, end + 1):
        if 0 <= ln - 1 < len(lines) and OVERRIDE_MARKER in lines[ln - 1]:
            return True
    return False


def scan_source(source: str) -> list[int]:
    """Line numbers of cause-less failure strings in one source string."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    lines = source.splitlines()
    found: set[int] = set()
    for handler in ast.walk(tree):
        if not isinstance(handler, ast.ExceptHandler) or not handler.name:
            continue
        bound = {handler.name}
        for node in ast.walk(handler):
            if not isinstance(node, ast.Call):
                continue
            cname = _call_name(node)
            for kw in node.keywords:
                if (cname, kw.arg) not in TARGETS:
                    continue
                if not _value_interpolates_bare(kw.value, bound):
                    continue
                if _has_override(node, lines):
                    continue
                found.add(node.lineno)
    return sorted(found)


def scan_file(path: Path) -> list[int]:
    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    return scan_source(source)


def main() -> int:
    offenders: list[str] = []
    scanned = 0
    for root, excluded in SCAN_ROOTS:
        if not root.exists():
            continue
        for py in sorted(root.rglob("*.py")):
            rel_parts = py.relative_to(root).parts
            if excluded and rel_parts and rel_parts[0] in excluded:
                continue
            scanned += 1
            for lineno in scan_file(py):
                rel = str(py.relative_to(REPO_ROOT)).replace("\\", "/")
                offenders.append(f"  {rel}:{lineno}")

    if offenders:
        print("Cause-less failure strings (a caught exception interpolated bare):")
        for o in offenders:
            print(o)
        print(
            "\nstr() on timeout-class exceptions (httpx.ReadTimeout, "
            "asyncio.TimeoutError) is the EMPTY STRING, so this "
            "JobResult.detail / finding body can page an operator with no "
            "cause at all (poindexter#3229). Wrap the exception: "
            "from utils.exception_format import describe_exception; "
            'detail=f"query failed: {describe_exception(exc)}". If a case is '
            "genuinely safe and reviewed, add `# noqa: cause-ok <reason>`."
        )
        return 1

    require_scanned(
        scanned, lint="lint_exception_cause", roots=[r for r, _ in SCAN_ROOTS]
    )
    print(f"lint_exception_cause: clean — failure strings name their cause ({scanned} files).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
