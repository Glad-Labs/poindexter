"""services/module_paths.py -- the string-named module seam (poindexter#1046 step 1).

Two halves.

1. Resolver semantics: both spellings accepted, non-project paths untouched,
   idempotent, ``module:attr`` handled, the ROOT_PACKAGE switch does exactly
   one thing.

2. The acceptance criterion for step 1 of the epic: **every string-named
   project module in the wired call sites resolves and imports under BOTH
   spellings.** The inventory is read from the source files by AST, not
   maintained by hand, so a new string path added to any of those files is
   covered the moment it lands -- and a file that stops naming any module
   trips the floor assertion instead of silently shrinking the check.

Import policy mirrors ``test_registry_completeness``: an import failure of a
*project* module is a real defect and fails hard. Two narrow tolerances, both
visible under ``-rs``:

* the module's **source file is not in this checkout** -- the public mirror
  strips a few operator-only files (a tap over the operator's own Claude
  sessions, the operator overlays), and the registry still names them. That is
  a strip, not a resolver defect, so it skips. It is decided from the resolver's
  own package root, so no stripped path is ever spelled here (the mirror-safety
  guard rejects shipping tests that name one) and it stays correct after the
  tree moves. The blind spot: a newly-dead ``_SAMPLES`` entry also skips here
  rather than failing -- ``get_core_samples()`` already logs an ERROR for it on
  every boot, and sample staleness is that test's job, not this one's.
* a ``ModuleNotFoundError`` for a *third-party* dependency (an optional
  provider missing its extra) -- and even then the project path must have been
  *found* (``find_spec``), which is the property the migration must preserve.
"""

from __future__ import annotations

import ast
import importlib
import importlib.util
import re
from pathlib import Path

import pytest

from services import module_paths as mp

BACKEND = Path(__file__).resolve().parents[3]  # src/cofounder_agent

# The files whose string-named modules were routed through the seam. Keep in
# step with the epic's step-1 list; the floor test below fails if any of them
# stops yielding paths (e.g. it was refactored and the check went blind).
WIRED_FILES = (
    "plugins/registry.py",
    "services/atom_registry.py",
    "services/http_client.py",
    "services/database_service.py",
    "utils/route_registration.py",
    "utils/import_audit.py",
    "routes/task_status_routes.py",
    "poindexter/cli/media.py",
    "modules/content/content_module.py",  # manifest: atoms_package
)

# Strings that look like project paths but are labels, not imports. They are
# persisted (audit_log finding sources, SQL `source` columns) and are handled
# by a later step of the epic, deliberately not by this seam.
_LABEL_CALLS = {"emit_finding", "emit", "_require", "build_avoidance_block_for_pool", "execute"}


# --------------------------------------------------------------------------
# 1. resolver semantics
# --------------------------------------------------------------------------


@pytest.mark.unit
class TestFlatModulePath:
    def test_strips_future_root_from_project_path(self):
        assert mp.flat_module_path("poindexter.services.x.y") == "services.x.y"

    def test_flat_path_unchanged(self):
        assert mp.flat_module_path("services.x") == "services.x"

    def test_cli_package_is_not_a_project_root(self):
        # The CLI itself lives at top-level `poindexter/` today. `cli` is not in
        # PROJECT_ROOTS, so this must NOT be mistaken for a prefixed path.
        assert mp.flat_module_path("poindexter.cli.app") == "poindexter.cli.app"

    def test_third_party_untouched(self):
        for p in ("os.path", "langchain_core.x", "acme_taps.slack", "poindexter"):
            assert mp.flat_module_path(p) == p

    def test_brain_is_a_project_root(self):
        # Decided 2026-09-10: brain ships inside the distribution as poindexter.brain.
        assert mp.flat_module_path("poindexter.brain.bootstrap") == "brain.bootstrap"
        assert mp.resolve_module_path("brain.bootstrap") == mp.resolve_module_path(
            "poindexter.brain.bootstrap"
        )

    @pytest.mark.parametrize("bad", ["", None, 42])
    def test_rejects_non_string(self, bad):
        with pytest.raises(ValueError):
            mp.flat_module_path(bad)  # type: ignore[arg-type]


@pytest.mark.unit
class TestResolveModulePath:
    def test_both_spellings_resolve_identically(self):
        assert mp.resolve_module_path("poindexter.services.x") == mp.resolve_module_path(
            "services.x"
        )

    def test_flat_tree_resolves_to_flat(self):
        # ROOT_PACKAGE is "" until step 2 of the epic.
        assert mp.ROOT_PACKAGE == ""
        assert mp.resolve_module_path("poindexter.modules.content.atoms") == (
            "modules.content.atoms"
        )

    def test_idempotent(self):
        once = mp.resolve_module_path("poindexter.utils.route_utils")
        assert mp.resolve_module_path(once) == once

    def test_root_package_switch_is_the_only_change(self, monkeypatch):
        monkeypatch.setattr(mp, "ROOT_PACKAGE", "poindexter")
        assert mp.resolve_module_path("services.x") == "poindexter.services.x"
        assert mp.resolve_module_path("poindexter.services.x") == "poindexter.services.x"
        # non-project paths are still untouched under the new root
        assert mp.resolve_module_path("langchain_core.x") == "langchain_core.x"
        assert mp.resolve_module_path("poindexter.cli.app") == "poindexter.cli.app"

    def test_every_declared_project_root_is_importable(self):
        # Importability rather than a directory under BACKEND: `brain` is a repo-root
        # sibling until step 2 moves it, and after the move every root lives under
        # poindexter/. find_spec is the move-proof form of "this root exists".
        for root in sorted(mp.PROJECT_ROOTS):
            assert importlib.util.find_spec(root) is not None, (
                f"PROJECT_ROOTS names {root!r} but it is not importable"
            )


@pytest.mark.unit
class TestObjectPaths:
    def test_splits_and_resolves(self):
        assert mp.resolve_object_path("poindexter.services.x:Klass") == ("services.x", "Klass")

    @pytest.mark.parametrize("bad", ["services.x", "services.x:", ":Klass", ""])
    def test_rejects_specs_without_attr(self, bad):
        with pytest.raises(ValueError):
            mp.resolve_object_path(bad)

    def test_import_object_path_returns_the_attribute(self):
        obj = mp.import_object_path("services.module_paths:resolve_module_path")
        assert obj is mp.resolve_module_path
        assert mp.import_object_path("poindexter.services.module_paths:ROOT_PACKAGE") == ""

    def test_missing_attribute_stays_loud(self):
        with pytest.raises(AttributeError):
            mp.import_object_path("services.module_paths:does_not_exist")

    def test_import_module_path_both_spellings_same_object(self):
        assert mp.import_module_path("services.module_paths") is mp.import_module_path(
            "poindexter.services.module_paths"
        )


# --------------------------------------------------------------------------
# 2. acceptance: every wired string path resolves + imports both ways
# --------------------------------------------------------------------------


def _string_module_paths(rel: str) -> list[tuple[int, str]]:
    """Every string constant in ``rel`` that names a project module or a
    ``module:attr`` object, excluding label-style call arguments."""
    src = (BACKEND / rel).read_text(encoding="utf-8")
    tree = ast.parse(src)
    parents: dict[ast.AST, ast.AST] = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[child] = node
    out: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
            continue
        value = node.value
        module_part = value.split(":", 1)[0]
        if not mp.is_project_module_path(module_part) or " " in value or value.endswith("."):
            continue
        # Entry-point GROUP names share the future root's prefix: `"poindexter.modules"`
        # in registry.py's group map is a setuptools group, not the `modules` package.
        # A bare two-segment `poindexter.<x>` is never a module path this seam is asked
        # to import (the package roots are always spelled flat, e.g. "modules").
        if re.fullmatch(rf"{re.escape(mp.FUTURE_ROOT)}\.[a-z_]+", value):
            continue
        # A `/` operand is a filesystem path segment, never a module:
        # `(_p / "brain" / "bootstrap.py")` in database_service's sys.path walk.
        parent = parents.get(node)
        if isinstance(parent, ast.BinOp) and isinstance(parent.op, ast.Div):
            continue
        # skip label-style arguments: emit_finding(source=...), _require(source=...)
        p = parents.get(node)
        while p is not None and not isinstance(p, ast.Call):
            p = parents.get(p)
        if isinstance(p, ast.Call):
            fn = p.func
            name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")
            if name in _LABEL_CALLS:
                continue
        # skip the resolver's own docstring-free examples (its module is not in WIRED_FILES)
        out.append((node.lineno, value))
    return out


def _inventory() -> list[tuple[str, int, str]]:
    return [(rel, ln, v) for rel in WIRED_FILES for ln, v in _string_module_paths(rel)]


_INVENTORY = _inventory()


@pytest.mark.unit
def test_inventory_floor():
    """Guard the guard: the wired files must keep yielding string paths.

    Today this is ~190 (128 registry samples, 42 routes, 10 http_client
    modules, plus the long tail). A collapse means a file was refactored and
    this check went blind -- the exact silent failure the scan-floor principle
    in CLAUDE.md exists to prevent.
    """
    assert len(_INVENTORY) >= 150, f"only {len(_INVENTORY)} string paths found across {WIRED_FILES}"
    per_file = {rel: sum(1 for r, _, _ in _INVENTORY if r == rel) for rel in WIRED_FILES}
    empty = [rel for rel, n in per_file.items() if n == 0]
    assert not empty, f"wired files yielding no string paths (check went blind?): {empty}"


def _root_dir(root: str) -> Path | None:
    """Filesystem directory of a project root, wherever this checkout keeps it.

    `services`, `plugins`, ... sit under src/cofounder_agent; `brain` sits at the
    repo root until step 2 moves it; after the move every root is under
    poindexter/. `find_spec` on a bare top-level name only LOCATES the package
    (no import), so this is cheap and follows the tree wherever it goes."""
    spec = importlib.util.find_spec(root)
    if spec is None or not spec.submodule_search_locations:
        return None
    return Path(next(iter(spec.submodule_search_locations)))


def _source_shipped(dotted: str) -> bool:
    """True when this checkout contains the module's source (file or package)."""
    flat = mp.flat_module_path(dotted)
    root, _, rest = flat.partition(".")
    root_dir = _root_dir(root)
    if root_dir is None:
        return False
    if not rest:
        return True
    rel = root_dir.joinpath(*rest.split("."))
    return rel.with_suffix(".py").is_file() or (rel / "__init__.py").is_file()


def _find_spec(dotted: str):
    """``find_spec`` raises ``ModuleNotFoundError`` when a *parent* package
    imports but the child does not (``poindexter.poindexter``); for this test
    that is simply "not found", so read it as ``None``."""
    try:
        return importlib.util.find_spec(dotted)
    except ModuleNotFoundError:
        return None


def _assert_importable(spec: str, *, where: str) -> None:
    """The step-1 property: the project path is FOUND under the resolved
    spelling. Then import it; a missing *project* module or any non-import
    error is a defect. A missing *third-party* module (optional extra) is the
    one tolerated outcome -- and only after find_spec succeeded."""
    module_part, _, attr = spec.partition(":")
    resolved = mp.resolve_module_path(module_part)
    if not _source_shipped(module_part):
        pytest.skip(
            f"{where}: {module_part!r} source is not shipped in this checkout (mirror strip)"
        )
    assert _find_spec(resolved) is not None, (
        f"{where}: {spec!r} -> {resolved!r} not found on sys.path"
    )
    try:
        module = importlib.import_module(resolved)
    except ModuleNotFoundError as exc:
        missing_root = (exc.name or "").split(".")[0]
        assert (
            missing_root and missing_root not in mp.PROJECT_ROOTS and missing_root != mp.FUTURE_ROOT
        ), f"{where}: {spec!r} failed on a PROJECT module: {exc}"
        pytest.skip(f"{where}: {spec!r} needs optional third-party {exc.name!r}")
    if attr:
        assert hasattr(module, attr), f"{where}: {spec!r} imports but has no attribute {attr!r}"


@pytest.mark.unit
@pytest.mark.parametrize(
    "rel,lineno,spec", _INVENTORY, ids=[f"{r}:{ln}:{v}" for r, ln, v in _INVENTORY]
)
def test_wired_string_path_imports_under_both_spellings(rel: str, lineno: int, spec: str):
    where = f"{rel}:{lineno}"
    _assert_importable(spec, where=where)
    module_part, sep, attr = spec.partition(":")
    prefixed = f"{mp.FUTURE_ROOT}.{module_part}" + (f":{attr}" if sep else "")
    _assert_importable(prefixed, where=where + " (prefixed)")
    assert mp.resolve_module_path(prefixed.split(":")[0]) == mp.resolve_module_path(module_part)


@pytest.mark.unit
def test_wired_call_sites_no_longer_import_project_paths_directly():
    """The seam only holds if the wired files stop calling importlib on a
    project path themselves. ``importlib.import_module(name)`` on a *variable*
    is fine inside the seam's own callers only when the argument was already
    resolved; the recognisable regression is a literal project path fed
    straight to importlib/__import__."""
    offenders: list[str] = []
    for rel in WIRED_FILES:
        tree = ast.parse((BACKEND / rel).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")
            if name not in {"import_module", "__import__"}:
                continue
            if (
                node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
            ):
                if mp.is_project_module_path(node.args[0].value):
                    offenders.append(f"{rel}:{node.lineno} {ast.unparse(node)[:80]}")
    assert not offenders, (
        "literal project paths still fed straight to importlib:\n  " + "\n  ".join(offenders)
    )
