#!/usr/bin/env python3
"""CI lint: no HTTPException detail that echoes a caught exception to a client.

Information-disclosure guard (security audit finding L2, poindexter#724). A
FastAPI ``HTTPException``'s ``detail=`` is serialized verbatim into the HTTP
response body, so interpolating a caught exception into it leaks internal
detail to the caller:

  - an httpx ``ConnectError`` embeds the resolved IP/port of whatever a
    user-supplied URL pointed at — turning a scrape endpoint into a low-grade
    internal-network probe;
  - a JWT library error names exactly which claim/step failed (expiry,
    signature, audience...) — useful feedback for someone forging tokens.

The fix is always the same shape: a generic ``detail=`` string plus a
server-side ``logger.warning(..., e)``. This pattern had to be cleaned out
twice (#642, then #724), which is the tell that the durable fix is a ratchet,
not another manual sweep.

## What is flagged

A flag fires only when an ``HTTPException(...)`` call inside an
``except ... as <name>:`` handler sets ``detail=`` to an f-string that
interpolates ``<name>`` in any form — ``{e}``, ``{str(e)}``,
``{type(e).__name__}: {e}``, ``{e!r}``, ``{e.args[0]}``.

Detection keys on the ``HTTPException`` *call*, NOT the ``detail=`` keyword:
dozens of internal result dataclasses (``JobResult(ok=False,
detail=f"query failed: {e}")``) reuse the keyword name but never reach a
client, and must never be flagged. Scope is ``src/cofounder_agent`` (where
FastAPI's HTTPException is constructed); ``brain/`` is a headless daemon and
``mcp-server/`` speaks its own transport, so neither builds HTTPException.

## Ratchet posture

The tree is clean today, so this is a FAIL-ON-ANY guard (no baseline file to
drift). Escape hatch for a deliberate, reviewed case (e.g. re-raising an
upstream HTTPException's own detail): add ``# noqa: detail-ok <reason>`` on
the offending line.

Run:
    python scripts/ci/lint_http_detail_leak.py    # exit 0 = clean, 1 = leak found
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

# Production trees that construct FastAPI HTTPException. Tests are excluded —
# an HTTPException raised in a test asserts behavior, it never reaches a real
# client, so it is not an information-disclosure risk.
SCAN_ROOTS: list[tuple[Path, tuple[str, ...]]] = [
    (REPO_ROOT / "src" / "cofounder_agent", ("tests",)),
]

OVERRIDE_MARKER = "detail-ok"


def _is_httpexception_call(call: ast.Call) -> bool:
    """True if call is ``HTTPException(...)`` or ``<mod>.HTTPException(...)``."""
    func = call.func
    if isinstance(func, ast.Name):
        return func.id == "HTTPException"
    if isinstance(func, ast.Attribute):
        return func.attr == "HTTPException"
    return False


def _detail_keyword(call: ast.Call) -> ast.keyword | None:
    for kw in call.keywords:
        if kw.arg == "detail":
            return kw
    return None


def _fstring_references(node: ast.expr, names: set[str]) -> bool:
    """True if ``node`` is an f-string interpolating any of ``names``.

    Walks each ``FormattedValue`` subtree so every interpolation form is
    caught: ``{e}`` (Name), ``{str(e)}`` (Call), ``{type(e).__name__}``
    (Attribute), ``{e!r}`` (conversion), ``{e.args[0]}`` (Subscript).
    """
    if not isinstance(node, ast.JoinedStr):
        return False
    for value in node.values:
        if isinstance(value, ast.FormattedValue):
            for sub in ast.walk(value.value):
                if isinstance(sub, ast.Name) and sub.id in names:
                    return True
    return False


def _has_override(call: ast.Call, lines: list[str]) -> bool:
    """True if a ``detail-ok`` marker sits anywhere in the call's line span."""
    start = call.lineno
    end = call.end_lineno or start
    for ln in range(start, end + 1):
        if 0 <= ln - 1 < len(lines) and OVERRIDE_MARKER in lines[ln - 1]:
            return True
    return False


def scan_source(source: str) -> list[int]:
    """Line numbers of HTTPException detail leaks in one source string."""
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
            if not (isinstance(node, ast.Call) and _is_httpexception_call(node)):
                continue
            detail = _detail_keyword(node)
            if detail is None or not _fstring_references(detail.value, bound):
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
    for root, excluded in SCAN_ROOTS:
        if not root.exists():
            continue
        for py in sorted(root.rglob("*.py")):
            rel_parts = py.relative_to(root).parts
            if excluded and rel_parts and rel_parts[0] in excluded:
                continue
            for lineno in scan_file(py):
                rel = str(py.relative_to(REPO_ROOT)).replace("\\", "/")
                offenders.append(f"  {rel}:{lineno}")

    if offenders:
        print("HTTPException detail leaks (a caught exception echoed to the client):")
        for o in offenders:
            print(o)
        print(
            "\nAn HTTPException detail= is serialized into the HTTP response body; "
            "interpolating a caught exception leaks internal detail (resolved IPs, "
            "which JWT claim failed, ...). Use a generic detail= and log the "
            "exception server-side (logger.warning(..., e)). If a case is genuinely "
            "safe and reviewed, add `# noqa: detail-ok <reason>`."
        )
        return 1

    print("lint_http_detail_leak: clean — no HTTPException detail leaks.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
