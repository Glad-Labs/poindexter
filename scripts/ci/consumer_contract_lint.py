#!/usr/bin/env python3
"""CI lint: a producer must have a consumer.

The recurring defect in this codebase is not a crash. It is a mechanism that
runs correctly, writes its output, reports healthy — and is read by nobody. The
producer cannot see the problem from its own side, so it looks fine forever:

  * ``qa_numeric_fidelity`` ran 37 times in 30 days while its ``qa_gates`` row
    read ``total_runs=0`` — the reviewer had no ``_REVIEWER_TO_GATE`` alias, so
    every update was dropped. Eighth recurrence of that one specific shape.
  * ``capability_registry`` takes a worker heartbeat every 30 seconds
    (~2,880 writes/day). Nothing has ever read the table.
  * ``qa_rail_degraded`` correctly fired 156 times reporting a crashed judge
    rail, into a log nobody reads, because the kind has no delivery policy.

Each was found the same way: compare two INDEPENDENT recorders of the same
event and notice they disagree. That works, but only when someone thinks to
look. This lint makes the cheap half of it mechanical.

What it checks
--------------
**``table_unread``** — a table this codebase WRITES must be read somewhere.
Readers are looked for in four places, and all four are load-bearing:

  1. application Python (``FROM`` / ``JOIN``)
  2. the operator console's JS
  3. Grafana dashboard JSON (a panel is a real consumer)
  4. **Postgres view definitions in the migration tree**

Skipping (4) produces false accusations: ``routing_outcomes`` and
``capability_outcomes`` look write-only in code and are read by the
``lab_outcomes_v1`` view. An audit that greps only source reports both as dead.

**``finding_kind_unrouted``** — every literal ``kind=`` passed to
``emit_finding`` should have a ``findings.<kind>.delivery`` policy declared in
``settings_defaults.py`` or the baseline seeds. ``findings.default`` is
deliberately inert (``log_only``), so an undeclared kind reaches nobody —
indistinguishable, from the operator's side, from never having been emitted.

What it deliberately does NOT check
-----------------------------------
Endpoints with no recorded traffic. That was measured (83 of 131 in 30 days)
and is **not a usable signal**: most are operator affordances that are
legitimately rare — ``/approve``, ``/restart``, ``/unpublish``. Shipping that
list would repeat the bandit mistake this repo already learned from, where 91
issues were filed and every one was a false positive.

Ratchet, not a cleanup order
----------------------------
Existing violations are captured in ``consumer_contract_baseline.json``. The
lint fails only on a NET-NEW one. Fixing a baselined item is welcome but not
forced here — several are real product decisions ("is the reader unbuilt, or
was it removed?") that belong in an issue, not a build break. Refresh the
baseline only when you have READ the new entries.
"""

from __future__ import annotations

import ast
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib_scan_floor import ScanFloorError, require_dir, require_scanned  # noqa: E402

LINT = "consumer-contract"
REPO = Path(__file__).resolve().parents[2]
PKG = REPO / "src" / "cofounder_agent" / "poindexter"
MIGRATIONS = PKG / "services" / "migrations"
CONSOLE = REPO / "src" / "cofounder_agent" / "console"
GRAFANA = REPO / "infrastructure" / "grafana"
BASELINE = Path(__file__).with_name("consumer_contract_baseline.json")

# A table nothing should be expected to read from application code. Keep this
# SHORT and say why — an entry here is a claim that the write is the whole
# point, not a place to silence an inconvenient finding.
TABLE_EXEMPT: dict[str, str] = {
    "schema_migrations": "migration runner bookkeeping; read by the runner's own SQL",
    "module_schema_migrations": "per-module migration bookkeeping, same as above",
    "checkpoints": "LangGraph's Postgres checkpointer owns these tables",
    "checkpoint_writes": "LangGraph checkpointer internal",
    "checkpoint_blobs": "LangGraph checkpointer internal",
    "checkpoint_migrations": "LangGraph checkpointer internal",
}


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""


def _app_py_files() -> list[Path]:
    return [
        p
        for p in PKG.rglob("*.py")
        if "migrations" not in p.parts and "__pycache__" not in p.parts
    ]


def _migration_text() -> str:
    return "\n".join(
        _read(p) for p in MIGRATIONS.iterdir() if p.suffix in (".py", ".sql")
    )


def _real_tables(migration_text: str) -> set[str]:
    """Table names from DDL — so prose in a docstring can't look like a write.

    Without this the writer regex matches English ("UPDATE the dictionary") and
    invents dozens of tables that do not exist.
    """
    return {
        t.lower()
        for t in re.findall(
            r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(?:public\.)?([a-z_][a-z0-9_]*)",
            migration_text,
            re.IGNORECASE,
        )
    }


def _view_bodies(migration_text: str) -> str:
    return "\n".join(
        m.group(0)
        for m in re.finditer(
            r"CREATE\s+(?:OR\s+REPLACE\s+)?VIEW\b[\s\S]{0,8000}",
            migration_text,
            re.IGNORECASE,
        )
    )


def find_unread_tables() -> tuple[list[str], int]:
    migration_text = _migration_text()
    real = _real_tables(migration_text)
    app_files = _app_py_files()
    app = "\n".join(_read(p) for p in app_files)
    console = "\n".join(_read(p) for p in CONSOLE.rglob("*.js")) if CONSOLE.is_dir() else ""
    grafana = "\n".join(_read(p) for p in GRAFANA.rglob("*.json")) if GRAFANA.is_dir() else ""
    readers = app + console + grafana + _view_bodies(migration_text)

    written = {
        t.lower()
        for t in re.findall(
            r"\b(?:INSERT\s+INTO|UPDATE)\s+(?:public\.)?([a-z_][a-z0-9_]*)",
            app,
            re.IGNORECASE,
        )
    } & real

    unread = [
        t
        for t in sorted(written)
        if t not in TABLE_EXEMPT
        and not re.search(
            r"\b(?:FROM|JOIN)\s+(?:public\.)?" + re.escape(t) + r"\b",
            readers,
            re.IGNORECASE,
        )
    ]
    return unread, len(app_files)


def find_unrouted_finding_kinds() -> tuple[list[str], int]:
    """Literal ``kind=`` arguments to ``emit_finding`` with no delivery policy.

    AST rather than regex: a call spanning lines with the kind on its own line
    is the normal shape here, and a regex over the call text misses it.
    """
    kinds: set[str] = set()
    scanned = 0
    for path in _app_py_files():
        try:
            tree = ast.parse(_read(path))
        except SyntaxError:
            continue
        scanned += 1
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
            if name != "emit_finding":
                continue
            for kw in node.keywords:
                if kw.arg == "kind" and isinstance(kw.value, ast.Constant):
                    if isinstance(kw.value.value, str):
                        kinds.add(kw.value.value)

    declared_text = _read(PKG / "services" / "settings_defaults.py") + _read(
        MIGRATIONS / "0000_baseline.seeds.sql"
    )
    declared = set(re.findall(r"findings\.([a-z0-9_]+)\.delivery", declared_text))
    return sorted(k for k in kinds if k not in declared), scanned


def main() -> int:
    require_dir(PKG, lint=LINT)
    require_dir(MIGRATIONS, lint=LINT)

    unread, py_count = find_unread_tables()
    unrouted, ast_count = find_unrouted_finding_kinds()

    require_scanned(py_count, lint=LINT, what="application python files", roots=(PKG,))
    require_scanned(ast_count, lint=LINT, what="parseable python files", roots=(PKG,))

    current = {"table_unread": unread, "finding_kind_unrouted": unrouted}

    if "--write-baseline" in sys.argv:
        BASELINE.write_text(json.dumps(current, indent=2, sort_keys=True) + "\n")
        print(f"{LINT}: baseline written — "
              f"{len(unread)} unread table(s), {len(unrouted)} unrouted finding kind(s)")
        return 0

    baseline = json.loads(_read(BASELINE) or "{}")
    failed = False
    for contract, items in current.items():
        known = set(baseline.get(contract, []))
        new = [i for i in items if i not in known]
        if new:
            failed = True
            print(f"\n{LINT}: NET-NEW {contract} ({len(new)}):")
            for i in new:
                print(f"    - {i}")
            if contract == "table_unread":
                print(
                    "\n  A table this code writes is read by nothing — not application\n"
                    "  Python, not the console, not a Grafana panel, not a Postgres view.\n"
                    "  Either wire the reader you intended, or add it to TABLE_EXEMPT in\n"
                    "  this lint with the reason the write IS the point."
                )
            else:
                print(
                    "\n  emit_finding() uses a kind with no findings.<kind>.delivery policy.\n"
                    "  findings.default is deliberately inert (log_only), so this finding\n"
                    "  reaches nobody — the same as not emitting it. Declare a policy in\n"
                    "  services/settings_defaults.py (log_only is a fine CHOICE; the point\n"
                    "  is that it be one)."
                )

    if failed:
        print(f"\n  Refresh with: python {Path(__file__).name} --write-baseline")
        print("  Read the new entries before you do.")
        return 1

    print(
        f"{LINT}: OK — {py_count} python file(s) scanned; "
        f"{len(unread)} unread table(s) and {len(unrouted)} unrouted kind(s), all baselined"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ScanFloorError as exc:
        print(f"{LINT}: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
