#!/usr/bin/env python3
"""CI lint: transport adapters carry no inline business SQL (#1344, epic #1340).

The [transport-adapter contract](../../docs/architecture/2026-06-10-transport-adapter-contract.md)
decided the **service / module layer is the single contract** and the HTTP API,
the `poindexter` CLI, and the MCP servers are **thin adapters** that delegate to
service functions — no adapter holds business logic or raw SQL. This lint keeps
the rule from re-rotting: it flags inline SQL executed straight from an adapter.

What counts as a violation
--------------------------
A database-execution call — ``conn.fetch* / conn.execute* / conn.fetchrow /
conn.fetchval`` (or the ``pool.*`` / cursor equivalents) — whose **first
argument is a string literal that looks like SQL** (starts with
``SELECT / INSERT / UPDATE / DELETE / WITH / CREATE / ALTER / DROP / TRUNCATE``,
case-insensitively; f-strings count by their first literal chunk).

What is explicitly NOT a violation
----------------------------------
- ``asyncpg.create_pool(...)`` / ``asyncpg.connect(...)`` — opening a pool to
  hand to a service is the correct adapter pattern. The guard keys on *SQL
  execution*, not connection creation, so these method names aren't matched.
- Delegating to a service (``services.foo.bar(...)`` / ``modules.x.y(...)``) —
  the method name isn't a DB-exec method.
- Passing a non-literal query (``conn.fetch(query)``) — the guard flags inline
  literals, not every DB call. A query built or imported elsewhere is out of
  scope by design (it keeps the guard false-positive-free; the common offender
  is the inline literal).

Scan roots (adapter trees only)
-------------------------------
- ``src/cofounder_agent/routes/``
- ``src/cofounder_agent/poindexter/cli/`` **minus the bootstrap allowlist**
  (``setup.py`` / ``migrate.py`` / ``auth.py`` / ``_bootstrap.py`` — the
  permanently-direct provisioning commands that run before the API/schema/first
  OAuth client exist; see the ADR's rule #4).
- ``mcp-server/`` (excluding ``tests/``).
- **Excluded:** ``mcp-server-gladlabs/`` — the baseline JSON ships in the public
  mirror, so the private overlay's paths must never enter it (mirrors
  ``lint_silent_excepts.py``).

Baseline ratchet
----------------
Existing violations are captured per-file in ``adapter_purity_baseline.json``;
the lint fails only when a file exceeds its baseline (a NEW inline-SQL adapter)
or a previously-clean file grows one. The baseline may only shrink — lower a
file's number when you route its SQL into a service and re-run with
``--update-baseline``.

Escape hatch: a ``# noqa: adapter-ok <reason>`` comment on the call line (or
anywhere in its span) exempts a genuinely-direct case (a bootstrap probe, a
diagnostic).

Run:
    python scripts/ci/adapter_purity_lint.py                  # check
    python scripts/ci/adapter_purity_lint.py --update-baseline   # re-baseline

Exit 0 = no new inline SQL, exit 1 = at least one new violation.
"""
from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
BASELINE_PATH = Path(__file__).resolve().parent / "adapter_purity_baseline.json"

OVERRIDE_MARKER = "adapter-ok"

# Bootstrap-direct CLI commands (ADR rule #4) — permanently exempt from the
# adapter contract because they run before the API / schema / first OAuth client
# exist. Excluded by FILENAME within poindexter/cli/.
_CLI_BOOTSTRAP_ALLOWLIST = frozenset(
    {"setup.py", "migrate.py", "auth.py", "_bootstrap.py"}
)

# (root relative to REPO_ROOT, excluded top-level subdirs, excluded filenames)
SCAN_SPECS: list[tuple[str, tuple[str, ...], frozenset[str]]] = [
    ("src/cofounder_agent/routes", (), frozenset()),
    ("src/cofounder_agent/poindexter/cli", (), _CLI_BOOTSTRAP_ALLOWLIST),
    # The public MCP server — operator phone-facing tools. The private
    # ``mcp-server-gladlabs/`` overlay is intentionally NOT scanned: its paths
    # must never enter this baseline, which ships in the public mirror.
    ("mcp-server", ("tests",), frozenset()),
]

# asyncpg / cursor execution methods. A SQL-literal first arg to one of these,
# called straight from an adapter, is the inline-SQL this lint guards.
# create_pool / connect are deliberately absent — opening a connection is fine.
_DB_EXEC_METHODS = {
    "fetch",
    "fetchrow",
    "fetchval",
    "fetchmany",
    "execute",
    "executemany",
}

# A string that opens with a SQL statement keyword (start of the literal, after
# optional leading whitespace/newlines from a triple-quoted block).
_SQL_RE = re.compile(
    r"^\s*(SELECT|INSERT|UPDATE|DELETE|WITH|CREATE|ALTER|DROP|TRUNCATE)\b",
    re.IGNORECASE,
)


def _literal_sql_text(arg: ast.expr) -> str | None:
    """Return the SQL text if ``arg`` is a string literal (plain or f-string)
    that looks like SQL, else None."""
    text: str | None = None
    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
        text = arg.value
    elif isinstance(arg, ast.JoinedStr):  # f-string — inspect its first chunk
        for value in arg.values:
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                text = value.value
                break
    if text is not None and _SQL_RE.match(text):
        return text
    return None


def _inline_sql_of_call(node: ast.AST) -> str | None:
    """If ``node`` is a ``<x>.fetch*/execute*(...)`` call with a SQL-literal
    first argument, return that SQL text; else None."""
    if not isinstance(node, ast.Call):
        return None
    func = node.func
    if not isinstance(func, ast.Attribute) or func.attr not in _DB_EXEC_METHODS:
        return None
    if not node.args:
        return None
    return _literal_sql_text(node.args[0])


def _node_has_override(node: ast.AST, lines: list[str]) -> bool:
    """True if an ``adapter-ok`` marker appears anywhere in the node's span."""
    start = getattr(node, "lineno", 1)
    end = start
    for child in ast.walk(node):
        node_end = getattr(child, "end_lineno", None)
        if node_end is not None and node_end > end:
            end = node_end
    for ln in range(start, end + 1):  # lines 0-indexed, AST linenos 1-indexed
        if 0 <= ln - 1 < len(lines) and OVERRIDE_MARKER in lines[ln - 1]:
            return True
    return False


def scan_source(source: str) -> list[tuple[int, str]]:
    """Return ``(lineno, sql_snippet)`` for each un-overridden inline-SQL call."""
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return []
    lines = source.splitlines()
    out: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        sql = _inline_sql_of_call(node)
        if sql is None:
            continue
        if _node_has_override(node, lines):
            continue
        snippet = sql.strip().splitlines()[0][:80] if sql.strip() else ""
        out.append((getattr(node, "lineno", 0), snippet))
    return out


def scan_file(path: Path) -> int:
    """Count un-overridden inline-SQL adapter calls in one file."""
    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return 0
    return len(scan_source(source))


def _iter_target_files():
    """Yield every adapter-tree ``.py`` file in scope (allowlist applied)."""
    for rel_root, excluded_dirs, excluded_files in SCAN_SPECS:
        root = REPO_ROOT / rel_root
        if not root.exists():
            continue
        for py_file in sorted(root.rglob("*.py")):
            rel_parts = py_file.relative_to(root).parts
            if excluded_dirs and rel_parts and rel_parts[0] in excluded_dirs:
                continue
            if py_file.name in excluded_files:
                continue
            yield py_file


def compute_counts() -> dict[str, int]:
    """Map of ``relpath -> inline-SQL count`` across all adapter scan roots."""
    counts: dict[str, int] = {}
    for py_file in _iter_target_files():
        n = scan_file(py_file)
        if n:
            rel = str(py_file.relative_to(REPO_ROOT)).replace("\\", "/")
            counts[rel] = n
    return counts


def load_baseline() -> dict[str, int]:
    if not BASELINE_PATH.exists():
        return {}
    return json.loads(BASELINE_PATH.read_text(encoding="utf-8"))


def _violations_for(rel: str) -> list[tuple[int, str]]:
    """Re-scan one relpath to print its actual violation lines on regression."""
    return scan_source((REPO_ROOT / rel).read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description="Adapter-purity lint (#1344).")
    parser.add_argument(
        "--update-baseline",
        action="store_true",
        help="Regenerate adapter_purity_baseline.json from the current tree.",
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
            f"adapter_purity_lint: baseline written — "
            f"{len(counts)} files, {total} inline-SQL adapter call(s) grandfathered."
        )
        return 0

    baseline = load_baseline()
    regressions: list[str] = []
    for rel, n in sorted(counts.items()):
        allowed = baseline.get(rel, 0)
        if n > allowed:
            regressions.append(f"  {rel}: {n} inline-SQL call(s), baseline allows {allowed}")
            for lineno, snippet in _violations_for(rel):
                regressions.append(f"      L{lineno}: {snippet}")

    if regressions:
        print("NEW INLINE SQL IN A TRANSPORT ADAPTER (not in baseline):")
        for r in regressions:
            print(r)
        print(
            "\nThe service / module layer is the contract — an adapter (route / "
            "CLI / MCP tool) must delegate, not run SQL. Move the query into a "
            "service function and call it. If this is a genuinely-direct case "
            "(a bootstrap probe), add `# noqa: adapter-ok <reason>`. If you "
            "intentionally reduced a count, re-run with --update-baseline. See "
            "docs/architecture/2026-06-10-transport-adapter-contract.md."
        )
        return 1

    total = sum(counts.values())
    print(
        f"adapter_purity_lint: clean — no new inline-SQL adapters "
        f"({total} baselined across {len(counts)} files; ratchet only shrinks)."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
