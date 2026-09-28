#!/usr/bin/env python3
"""CI lint: every DB-loaded ``SiteConfig`` gets its reads stamped, or says why not.

Read telemetry (Glad-Labs/poindexter#756) stamps ``app_settings.last_read_at``
from buffers held in process memory: each ``SiteConfig``'s read set and the
process-wide ``settings_read_sink``. A read is stamped only if the process that
made it flushes them (``services/settings_read_telemetry.py::
flush_read_telemetry``), and ``ProbeZeroReaderSettingsJob`` reports every key
that was never stamped as an orphan candidate. So a process that loads a
``SiteConfig`` and never flushes is a blind spot: a key only it reads looks
unused, and the next person to act on that finding retires a live key.

The blind spots arrived one process at a time. The worker flushed from the
start. Prefect content-flow runs did not until 2026-09-28, so
``content_flow_stale_inprogress_minutes`` (read ~700 times a day) sat at NULL
and the finding listed live QA weights. The CLI, the auto-embed sidecar and
``regen_media_scripts.py``, the voice agents and every script under ``scripts/``
did not until the change that added this lint: all six ``tap_*`` keys the
sidecar reads every hour read as never-read on prod.
Nothing about a new entry point says it needs a flush, and a missing one fails
silently, so it is checked here.

What it finds
-------------
Every call that yields a DB-loaded ``SiteConfig`` in the repo's Python outside
test directories:

* ``SiteConfig(pool=...)``;
* ``SiteConfig(...)`` assigned to a name that is then ``.load()``-ed or
  ``.reload()``-ed: in the same function, anywhere in the module for a
  module-level assignment, or anywhere in the module for an attribute target
  (``self._site_config``);
* ``build_container(...)``, ``build_and_wire_subprocess_with_container(...)``
  and ``build_and_wire_for_subprocess(...)``.

The constructions inside those three builders (``BUILDER_DEFS``) are skipped:
they build for their callers, and each call to a builder is a site instead.

When a site is covered
----------------------
When the function it sits in calls ``flush_read_telemetry``, directly or
through a function of the same module that does (the content flow's
``_stamp_settings_reads``, the tap runner's). A helper that only builds and
returns the ``SiteConfig`` (``_wire_subprocess_site_config``) is covered when
every function in the module that calls it is.

Anything else needs an ``ALLOWLIST`` entry, keyed ``<path>::<function>``, that
says why its reads aren't stamped where it builds them: the worker's lifespan
instance is flushed by a job in another module, the CLI's ``cli_site_config``
records into the sink that ``close_cli_pool`` flushes, a long-lived adapter has
no teardown to flush from. Anything that can flush should: one-off scripts and
the parked voice agents flush like everything else.

``cli_site_config`` is flagged anywhere outside ``poindexter/cli/``. Its reads
go to the process-wide sink, and only a CLI command's ``close_cli_pool``
flushes that sink, so a process that isn't a CLI command would never stamp
them.

Stale entries fail
------------------
An ``ALLOWLIST`` entry that matches no uncovered site fails the lint, and so
does a ``BUILDER_DEFS`` entry whose function is gone. Either would silently
excuse the next construction added at that spot. Same rule as
``settings_phantom_read_lint``.

What it deliberately does not catch
-----------------------------------
Reads that never touch a ``SiteConfig``: raw SQL that skips ``record_read``,
the brain daemon's own asyncpg connections, and ``SettingsService`` in a
process that holds no ``SiteConfig``. And a flush that runs before the reads
it should cover: the check is structural (the function flushes), not
temporal.

Static only — stdlib ``ast``, no project imports.

Run: ``python scripts/ci/settings_read_flush_lint.py``
Exit 0 = clean; 1 = an uncovered site, a misplaced ``cli_site_config``, or a
stale ``ALLOWLIST`` / ``BUILDER_DEFS`` entry.
"""

from __future__ import annotations

import ast
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib_scan_floor import require_dir, require_scanned  # noqa: E402

LINT = "settings_read_flush_lint"
REPO = Path(__file__).resolve().parents[2]
PKG = REPO / "src" / "cofounder_agent" / "poindexter"

# Directory names never descended into. Hidden directories are skipped too
# (``.git``, ``.venv``, and ``.claude/worktrees``, which holds whole copies of
# the repo in the main checkout).
SKIP_DIRS = frozenset(
    {"tests", "node_modules", "site-packages", "__pycache__", "venv", "pypa", "pypoetry"}
)

FLUSH = "flush_read_telemetry"
BUILDERS = frozenset(
    {"build_container", "build_and_wire_subprocess_with_container", "build_and_wire_for_subprocess"}
)
CLI_CTOR = "cli_site_config"
CLI_DIR = "src/cofounder_agent/poindexter/cli/"
LOADERS = frozenset({"load", "reload"})

# Where each builder is defined. Constructions inside these functions build for
# their callers, so they are skipped rather than reported.
BUILDER_DEFS = frozenset(
    {
        ("src/cofounder_agent/poindexter/services/bootstrap.py", "build_container"),
        ("src/cofounder_agent/poindexter/services/di_wiring.py", "build_and_wire_for_subprocess"),
        (
            "src/cofounder_agent/poindexter/services/di_wiring.py",
            "build_and_wire_subprocess_with_container",
        ),
    }
)

_WORKER = (
    "the worker's lifespan SiteConfig. FlushSettingsReadTelemetryJob "
    "(services/jobs/flush_settings_read_telemetry.py) drains it every minute: "
    "the plugin scheduler seeds this instance into every job as "
    "config['_site_config']"
)
# Every site that builds a DB-loaded SiteConfig without flushing it where it is
# built, with the reason. Keyed "<repo-relative path>::<enclosing function>".
# Kept to the structural exceptions: a place that CAN flush should, so a new
# one-off script or a parked service gets the same treatment as the worker.
ALLOWLIST: dict[str, str] = {
    # --- flushed, but from somewhere this lint can't follow ---
    # main.py's module-level SiteConfig() is loaded by the lifespan's
    # StartupManager (utils/startup_manager.py::_load_site_config), which
    # receives it injected, so it is not a construction site; the lifespan's
    # build_container() call is.
    "src/cofounder_agent/main.py::lifespan": _WORKER,
    "src/cofounder_agent/poindexter/cli/_bootstrap.py::cli_site_config": (
        "records into the process-wide settings_read_sink (read_recorder), "
        "which close_cli_pool flushes before it closes a CLI command's pool; "
        "cli_audit_sink_lint routes every CLI pool through close_cli_pool, and "
        "this lint rejects cli_site_config outside poindexter/cli/"
    ),
    # --- long-lived, with no teardown to flush from ---
    "mcp-server/server.py::_get_site_config": (
        "MCP server: a long-lived adapter (stdio for a Claude session; the "
        "mcp-http container runs it for weeks) that caches this SiteConfig for "
        "the life of the process, with no teardown or scheduler to flush from. "
        "Its tools call services the worker also runs (TopicBatchService, "
        "game_mode, the ollama helpers); a key only these tools read stays "
        "unstamped"
    ),
}


@dataclass(frozen=True)
class Site:
    """One construction of a DB-loaded ``SiteConfig``."""

    path: str
    scope: str
    line: int
    kind: str

    @property
    def key(self) -> str:
        return f"{self.path}::{self.scope}"


@dataclass
class ModuleReport:
    """What :func:`scan_source` found in one module."""

    uncovered: list[Site] = field(default_factory=list)
    covered: list[Site] = field(default_factory=list)
    misplaced_cli_ctor: list[int] = field(default_factory=list)
    builder_defs_seen: set[str] = field(default_factory=set)


def _call_name(call: ast.Call) -> str | None:
    func = call.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _tail(node: ast.AST) -> tuple[str, bool] | None:
    """``(name, is_attribute)`` for an assignment target or call receiver."""
    if isinstance(node, ast.Name):
        return node.id, False
    if isinstance(node, ast.Attribute):
        return node.attr, True
    return None


class _Scanner(ast.NodeVisitor):
    """One walk over a module, recording every fact the checks need."""

    def __init__(self) -> None:
        # Enclosing scopes, innermost last: (qualname, kind) with kind
        # "function" or "class". The module is the empty stack.
        self.stack: list[tuple[str, str]] = []
        # (parent qualname, simple name) -> qualname, for every function.
        self.defs: dict[tuple[str, str], str] = {}
        # function qualname (or "<module>") -> resolved callees / flush flag
        self.raw_calls: dict[str, list[tuple[str, bool, str]]] = {}
        self.calls_flush: set[str] = set()
        # (scope, call node, kind) for every candidate construction
        self.ctor_calls: list[tuple[str, ast.Call, str]] = []
        # SiteConfig(...) without pool=, keyed by id(call):
        # (scope, target name, target is an attribute, module-level)
        self.unpooled: dict[int, tuple[str, str, bool, bool]] = {}
        # .load()/.reload() receivers: (scope, name, is_attribute)
        self.loads: list[tuple[str, str, bool]] = []
        self.cli_ctor_lines: list[int] = []

    # -- scope bookkeeping ---------------------------------------------------
    def _scope(self) -> str:
        """The innermost enclosing function, or ``<module>``."""
        for qual, kind in reversed(self.stack):
            if kind == "function":
                return qual
        return "<module>"

    def _visit_def(self, node: ast.AST, name: str, kind: str) -> None:
        parent = self.stack[-1][0] if self.stack else ""
        qual = f"{parent}.{name}" if parent else name
        if kind == "function":
            self.defs[(parent, name)] = qual
        self.stack.append((qual, kind))
        self.generic_visit(node)
        self.stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_def(node, node.name, "function")

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_def(node, node.name, "function")

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._visit_def(node, node.name, "class")

    # -- facts ---------------------------------------------------------------
    def _record_assign(self, targets: list[ast.expr], value: ast.expr | None) -> None:
        if not isinstance(value, ast.Call) or _call_name(value) != "SiteConfig":
            return
        if any(kw.arg == "pool" for kw in value.keywords):
            return  # reported as SiteConfig(pool=...) already
        for target in targets:
            tail = _tail(target)
            if tail is not None:
                name, is_attr = tail
                self.unpooled[id(value)] = (self._scope(), name, is_attr, not self.stack)

    def visit_Assign(self, node: ast.Assign) -> None:
        self._record_assign(node.targets, node.value)
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        self._record_assign([node.target], node.value)
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        name = _call_name(node)
        scope = self._scope()
        if name is not None:
            if name == FLUSH:
                self.calls_flush.add(scope)
            is_attr = isinstance(node.func, ast.Attribute)
            receiver = ""
            if is_attr:
                tail = _tail(node.func.value)  # type: ignore[attr-defined]
                receiver = tail[0] if tail else ""
            self.raw_calls.setdefault(scope, []).append((name, is_attr, receiver))
            if name == "SiteConfig":
                if any(kw.arg == "pool" for kw in node.keywords):
                    self.ctor_calls.append((scope, node, "SiteConfig(pool=...)"))
                else:
                    self.ctor_calls.append((scope, node, "SiteConfig() then .load()"))
            elif name in BUILDERS:
                self.ctor_calls.append((scope, node, f"{name}()"))
            elif name == CLI_CTOR:
                self.cli_ctor_lines.append(node.lineno)
            if name in LOADERS and is_attr:
                tail = _tail(node.func.value)  # type: ignore[attr-defined]
                if tail is not None:
                    self.loads.append((scope, tail[0], tail[1]))
        self.generic_visit(node)


def _resolve(scanner: _Scanner, scope: str, name: str, is_attr: bool, receiver: str) -> str | None:
    """The module-local function a call in ``scope`` refers to, if any.

    A bare name resolves the way Python's lexical scoping does: a function
    nested in the caller, then in each enclosing function, then at module
    level. ``self.x()`` / ``cls.x()`` resolve to a method of the caller's
    class. Other attribute calls are not module-local."""
    parts = [] if scope == "<module>" else scope.split(".")
    if is_attr:
        if receiver not in {"self", "cls"} or len(parts) < 2:
            return None
        return scanner.defs.get((".".join(parts[:-1]), name))
    for depth in range(len(parts), -1, -1):
        found = scanner.defs.get((".".join(parts[:depth]), name))
        if found is not None:
            return found
    return None


def _flushing_scopes(scanner: _Scanner) -> set[str]:
    """Scopes that call ``flush_read_telemetry``, directly or through a
    module-local function that does (fixpoint)."""
    callees: dict[str, set[str]] = {}
    for scope, calls in scanner.raw_calls.items():
        resolved = {_resolve(scanner, scope, n, a, r) for n, a, r in calls}
        callees[scope] = {q for q in resolved if q is not None}
    flushing = set(scanner.calls_flush)
    changed = True
    while changed:
        changed = False
        for scope, targets in callees.items():
            if scope not in flushing and targets & flushing:
                flushing.add(scope)
                changed = True
    return flushing


def _callers(scanner: _Scanner, target: str) -> set[str]:
    return {
        scope
        for scope, calls in scanner.raw_calls.items()
        if any(_resolve(scanner, scope, n, a, r) == target for n, a, r in calls)
    }


def _is_db_loaded(scanner: _Scanner, call: ast.Call) -> bool:
    """Whether a ``SiteConfig(...)`` without ``pool=`` is loaded from the DB."""
    info = scanner.unpooled.get(id(call))
    if info is None:
        return False  # not assigned (returned or passed inline): never loaded
    scope, name, is_attr, module_level = info
    for load_scope, load_name, load_is_attr in scanner.loads:
        if load_name != name or load_is_attr != is_attr:
            continue
        if is_attr or module_level or load_scope == scope:
            return True
    return False


def scan_source(
    source: str, path: str, builder_defs: frozenset[tuple[str, str]] = BUILDER_DEFS
) -> ModuleReport:
    """Every DB-loaded ``SiteConfig`` construction in ``source``, split into
    covered and uncovered, plus misplaced ``cli_site_config`` calls.

    ``path`` is repo-relative with forward slashes. Pure function of its
    arguments, so the tests drive it with plain strings. A syntax error yields
    an empty report rather than raising, so one broken file never aborts a
    whole-tree scan (it fails elsewhere)."""
    report = ModuleReport()
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return report
    scanner = _Scanner()
    scanner.visit(tree)

    builder_scopes = {fn for p, fn in builder_defs if p == path}
    report.builder_defs_seen = {q for q in scanner.defs.values() if q in builder_scopes}
    flushing = _flushing_scopes(scanner)

    for scope, call, kind in scanner.ctor_calls:
        if scope in builder_scopes:
            continue
        if kind == "SiteConfig() then .load()" and not _is_db_loaded(scanner, call):
            continue
        site = Site(path=path, scope=scope, line=call.lineno, kind=kind)
        callers = _callers(scanner, scope) if scope != "<module>" else set()
        covered = scope in flushing or (bool(callers) and callers <= flushing)
        (report.covered if covered else report.uncovered).append(site)

    if not path.startswith(CLI_DIR):
        report.misplaced_cli_ctor = sorted(scanner.cli_ctor_lines)
    return report


def iter_python_files(root: Path) -> list[Path]:
    """Every ``.py`` under ``root``, skipping tests, vendored and hidden dirs."""
    found: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(
            d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")
        )
        found.extend(Path(dirpath) / f for f in sorted(filenames) if f.endswith(".py"))
    return found


@dataclass
class Evaluation:
    """The verdict over a set of modules: what failed, and what was counted."""

    failures: list[str]
    covered: int
    uncovered: int
    allowlisted: int


def evaluate(
    sources: dict[str, str],
    allowlist: dict[str, str] | None = None,
    builder_defs: frozenset[tuple[str, str]] | None = None,
) -> Evaluation:
    """Check ``{repo-relative path: source}`` against ``allowlist``.

    Pure function of its arguments (defaults: this module's ``ALLOWLIST`` and
    ``BUILDER_DEFS``), so the tests drive it with synthetic trees."""
    allowlist = ALLOWLIST if allowlist is None else allowlist
    builder_defs = BUILDER_DEFS if builder_defs is None else builder_defs
    uncovered: list[Site] = []
    covered: list[Site] = []
    misplaced: list[tuple[str, int]] = []
    builder_defs_seen: set[tuple[str, str]] = set()
    for rel, source in sources.items():
        report = scan_source(source, rel, builder_defs)
        uncovered.extend(report.uncovered)
        covered.extend(report.covered)
        misplaced.extend((rel, line) for line in report.misplaced_cli_ctor)
        builder_defs_seen.update((rel, q) for q in report.builder_defs_seen)

    failures: list[str] = []
    for site in sorted(uncovered, key=lambda s: (s.path, s.line)):
        if site.key not in allowlist:
            failures.append(
                f"  {site.path}:{site.line}: {site.kind} in {site.scope}() is never "
                "flushed, so every setting read through it looks unread to "
                "ProbeZeroReaderSettingsJob. Call "
                "`await flush_read_telemetry(pool, site_config)` "
                "(poindexter.services.settings_read_telemetry) in this function "
                "before the pool closes, or add an ALLOWLIST entry saying why "
                "this process doesn't flush. In poindexter/cli/, build it with "
                "`cli_site_config(pool)` instead."
            )
    for rel, line in misplaced:
        failures.append(
            f"  {rel}:{line}: cli_site_config() outside poindexter/cli/. Its reads "
            "go to the process-wide sink, which only a CLI command's "
            "close_cli_pool() flushes; use SiteConfig(pool=pool) and flush it."
        )
    live_keys = {site.key for site in uncovered}
    for key in sorted(set(allowlist) - live_keys):
        failures.append(
            f"  ALLOWLIST[{key!r}] matches no unflushed SiteConfig any more (renamed, "
            "now flushed, or gone). Delete the entry: a leftover exemption would "
            "excuse the next construction added there."
        )
    for rel, qual in sorted(builder_defs - builder_defs_seen):
        failures.append(
            f"  BUILDER_DEFS entry {rel}::{qual} names a function that no longer "
            "exists. Update it, or the builder's own constructions are reported "
            "as sites."
        )
    return Evaluation(
        failures=failures,
        covered=len(covered),
        uncovered=len(uncovered),
        allowlisted=sum(1 for site in uncovered if site.key in allowlist),
    )


def main() -> int:
    require_dir(PKG, lint=LINT)
    files = iter_python_files(REPO)
    sources = {
        f.relative_to(REPO).as_posix(): f.read_text(encoding="utf-8", errors="ignore")
        for f in files
    }
    result = evaluate(sources)
    require_scanned(len(files), lint=LINT, what="Python files", roots=(REPO,))
    require_scanned(
        result.covered + result.uncovered,
        lint=LINT,
        what="DB-loaded SiteConfig construction sites",
        roots=(REPO,),
    )

    if result.failures:
        print(
            f"{LINT}: {len(result.failures)} problem(s) with settings read telemetry "
            "coverage:\n",
            file=sys.stderr,
        )
        print("\n".join(result.failures), file=sys.stderr)
        print(
            "\nWhy this matters: a process that never flushes its reads makes a "
            "live key look unused. See docs/architecture/services/site_config.md "
            '("Read telemetry & orphan detection").',
            file=sys.stderr,
        )
        return 1

    print(
        f"{LINT}: clean — {result.covered} flushed and {result.allowlisted} allowlisted "
        f"SiteConfig construction site(s) across {len(files)} files."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
