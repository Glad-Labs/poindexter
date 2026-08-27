#!/usr/bin/env python3
"""Guard the CLI pool seam so ``poindexter <cmd>`` never goes finding-blind.

``utils/findings.py::emit_finding`` writes through a module-global
``AuditLogger``. Long-lived contexts get it from ``DatabaseService``; a
short-lived ``poindexter <cmd>`` gets it from ``open_cli_pool()`` in
``poindexter/cli/_bootstrap.py``. A CLI command that opens a bare
``asyncpg.create_pool`` instead has no sink, so every finding any service it
calls emits is DROPPED — it never reaches ``audit_log``, so the findings
router, the Findings dashboard, and the operator page never see it.

That is not hypothetical. During the first live Pro purchase (2026-08-26)
``poindexter pro sync`` emitted a ``pro_delivery_action_needed`` warn finding
and the log read:

    DROPPED warn finding (global AuditLogger not initialised):
    event=finding source=pro_delivery — will NOT reach the alert pipeline

19 CLI modules were reachable to an emitter the same way.

**Why this is a lint and not a comment.** The invariant regressed within hours
of being established: while the fixing PR was in review, `pro relay` merged to
main carrying three fresh ``await pool.close()`` calls. Nothing about a CLI
command's shape tells you it needs an audit sink, and the failure is silent by
construction — a dropped finding produces no failing test and no user-visible
error, just an operator page that never arrives. So it has to be checked
mechanically.

Two shapes are flagged, because the regression above proves either alone is
insufficient:

1. ``asyncpg.create_pool(...)`` — a pool opened outside the seam has no sink.
2. ``pool.close()`` — the relay regression called ``_open_ctx()`` (which DOES
   use ``open_cli_pool``) and then closed the pool directly, skipping the
   drain. A finding emitted moments earlier is only *scheduled*, so closing
   without draining kills it with ``InterfaceError('pool is closing')`` — the
   GlitchTip #863 race. Detecting only (1) would have missed it entirely.

Bare ``asyncpg.connect`` is deliberately NOT flagged: several commands
(``integrations``, ``settings``, ``stores``, ``validators``, ``backup``) need a
single Connection for ``conn.transaction()``, and a shared bare connection
can't safely take concurrent background writes anyway (asyncpg forbids
concurrent operations per connection).

Run: ``python scripts/ci/cli_audit_sink_lint.py``
Exit 0 = clean, 1 = a CLI command bypasses the seam.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

_CLI_DIR = Path(__file__).resolve().parents[2] / "src" / "cofounder_agent" / "poindexter" / "cli"

# _bootstrap.py DEFINES the seam — it is the one file that legitimately calls
# asyncpg.create_pool and pool.close().
_SEAM_OWNER = "_bootstrap.py"

_REMEDY = (
    "use `from poindexter.cli._bootstrap import close_cli_pool, open_cli_pool`, "
    "then `pool = await open_cli_pool()` / `await close_cli_pool(pool)`"
)


def _iter_violations(path: Path) -> list[tuple[int, str]]:
    """Return ``(lineno, message)`` for every seam bypass in one file."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except SyntaxError as exc:  # pragma: no cover - a broken file fails elsewhere
        return [(exc.lineno or 0, f"could not parse: {exc}")]

    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        attr = node.func.attr
        value = node.func.value

        # 1. asyncpg.create_pool(...) — a pool with no audit sink attached.
        if attr == "create_pool" and isinstance(value, ast.Name) and value.id == "asyncpg":
            found.append(
                (
                    node.lineno,
                    "asyncpg.create_pool() opens a pool with no audit sink, so every "
                    f"finding this command's services emit is DROPPED — {_REMEDY}",
                )
            )

        # 2. pool.close() — closes without draining in-flight finding writes.
        elif attr == "close" and isinstance(value, ast.Name) and value.id == "pool":
            found.append(
                (
                    node.lineno,
                    "pool.close() skips the drain, so a finding emitted just before "
                    "teardown dies on InterfaceError('pool is closing') — "
                    f"{_REMEDY}",
                )
            )
    return found


def main() -> int:
    if not _CLI_DIR.is_dir():
        print(f"cli_audit_sink_lint: CLI dir not found: {_CLI_DIR}", file=sys.stderr)
        return 1

    violations: list[tuple[Path, int, str]] = []
    for path in sorted(_CLI_DIR.glob("*.py")):
        if path.name == _SEAM_OWNER:
            continue
        for lineno, message in _iter_violations(path):
            violations.append((path, lineno, message))

    if not violations:
        print("cli_audit_sink_lint: clean — every CLI pool routes through the seam.")
        return 0

    print(
        f"cli_audit_sink_lint: {len(violations)} CLI pool site(s) bypass the audit "
        "sink seam:\n",
        file=sys.stderr,
    )
    for path, lineno, message in violations:
        rel = path.relative_to(_CLI_DIR.parents[3])
        print(f"  {rel}:{lineno}: {message}", file=sys.stderr)
    print(
        "\nWhy this matters: a CLI command without the sink drops every finding its "
        "services emit — silently. See docs/architecture/findings-routing.md "
        "(Execution contexts).",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
