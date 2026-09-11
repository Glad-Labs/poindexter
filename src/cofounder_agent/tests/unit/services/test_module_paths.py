"""services/module_paths.py -- the string-named module seam (Glad-Labs/poindexter#1046).

Two halves.

1. Resolver semantics: canonical and third-party paths pass through, the retired
   flat spelling is refused with the fix in the message, ``module:attr`` is
   handled, and non-strings are rejected.
2. The epic's definition of done, pinned on the real tree:

   * no flat root (``services``, ``plugins``, ..., ``brain``) is importable as a
     top-level package, its old directory is gone, and no alias finder sits on
     ``sys.meta_path``;
   * **every string-named project module in the wired call sites is spelled
     canonically and imports.** The inventory is read from the source files by
     AST, not maintained by hand, so a new string path added to any of those
     files is covered the moment it lands -- and a file that stops naming any
     module trips the floor assertion instead of silently shrinking the check.

Import policy mirrors ``test_registry_completeness``: an import failure of a
*project* module is a real defect and fails hard. Two narrow tolerances, both
visible under ``-rs``:

* the module's **source file is not in this checkout** -- the public mirror
  strips a few operator-only files (a tap over the operator's own Claude
  sessions, the operator overlays), and the registry still names them. That is
  a strip, not a resolver defect, so it skips. It is decided from the resolver's
  own package root, so no stripped path is ever spelled here (the mirror-safety
  guard rejects shipping tests that name one). The blind spot: a newly-dead
  ``_SAMPLES`` entry also skips here rather than failing -- ``get_core_samples()``
  already logs an ERROR for it on every boot, and sample staleness is that
  test's job, not this one's.
* a ``ModuleNotFoundError`` for a *third-party* dependency (an optional
  provider missing its extra) -- and even then the project path must have been
  *found* (``find_spec``).
"""

from __future__ import annotations

import ast
import importlib
import importlib.machinery
import importlib.util
import re
import sys
from pathlib import Path

import pytest

from poindexter.services import module_paths as mp

# The package root that holds services/, plugins/, ... -- derived from the resolver
# itself so it follows the tree (src/cofounder_agent/poindexter).
PKG_ROOT = Path(mp.__file__).resolve().parent.parent
BACKEND_ROOT = PKG_ROOT.parent  # src/cofounder_agent
REPO_ROOT = BACKEND_ROOT.parent.parent

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
    "cli/media.py",
    "modules/content/content_module.py",  # manifest: atoms_package
)

# Strings that look like project paths but are labels, not imports. They are
# persisted (audit_log finding sources, SQL `source` columns) and are data.
_LABEL_CALLS = {"emit_finding", "emit", "_require", "build_avoidance_block_for_pool", "execute"}

# The definition of done names these seven; `modules` and `brain` moved with them.
_EXPECTED_ROOTS = frozenset(
    {"services", "plugins", "modules", "utils", "routes", "schemas", "config", "tasks", "brain"}
)


# --------------------------------------------------------------------------
# 1. resolver semantics
# --------------------------------------------------------------------------
@pytest.mark.unit
class TestResolveModulePath:
    def test_canonical_path_unchanged(self):
        assert mp.resolve_module_path("poindexter.services.x.y") == "poindexter.services.x.y"
        assert mp.resolve_module_path("poindexter.modules.content.atoms") == (
            "poindexter.modules.content.atoms"
        )

    def test_third_party_and_cli_untouched(self):
        for p in ("os.path", "langchain_core.x", "acme_taps.slack", "poindexter", "poindexter.cli.app"):
            assert mp.resolve_module_path(p) == p

    @pytest.mark.parametrize("root", sorted(mp.PROJECT_ROOTS))
    def test_flat_spelling_is_refused_with_the_fix(self, root):
        with pytest.raises(ModuleNotFoundError) as excinfo:
            mp.resolve_module_path(f"{root}.x")
        assert excinfo.value.name == f"{root}.x"
        assert f"poindexter.{root}.x" in str(excinfo.value)

    def test_flat_refusal_is_an_import_error(self):
        # Callers keep their `except ImportError` policy (the registry logs + skips).
        with pytest.raises(ImportError):
            mp.resolve_module_path("services.x")

    @pytest.mark.parametrize("bad", ["", None, 42])
    def test_rejects_non_string(self, bad):
        with pytest.raises(ValueError):
            mp.resolve_module_path(bad)  # type: ignore[arg-type]

    def test_project_roots_are_the_moved_packages(self):
        assert mp.PROJECT_ROOTS == _EXPECTED_ROOTS
        assert mp.ROOT_PACKAGE == "poindexter"


@pytest.mark.unit
class TestObjectPaths:
    def test_splits_canonical_spec(self):
        assert mp.resolve_object_path("poindexter.services.x:Klass") == (
            "poindexter.services.x",
            "Klass",
        )

    @pytest.mark.parametrize(
        "bad", ["poindexter.services.x", "poindexter.services.x:", ":Klass", ""]
    )
    def test_rejects_specs_without_attr(self, bad):
        with pytest.raises(ValueError):
            mp.resolve_object_path(bad)

    def test_flat_spec_is_refused(self):
        with pytest.raises(ModuleNotFoundError):
            mp.resolve_object_path("services.x:Klass")

    def test_import_object_path_returns_the_attribute(self):
        obj = mp.import_object_path("poindexter.services.module_paths:resolve_module_path")
        assert obj is mp.resolve_module_path
        assert (
            mp.import_object_path("poindexter.services.module_paths:ROOT_PACKAGE") == "poindexter"
        )

    def test_missing_attribute_stays_loud(self):
        with pytest.raises(AttributeError):
            mp.import_object_path("poindexter.services.module_paths:does_not_exist")

    def test_import_module_path_returns_the_real_module(self):
        assert mp.import_module_path("poindexter.services.module_paths") is mp


# --------------------------------------------------------------------------
# 2a. definition of done: the flat roots are gone from the real tree
# --------------------------------------------------------------------------
@pytest.mark.unit
class TestFlatRootsAreGone:
    """``import services`` must NOT work (epic DoD). Checked on sys.path itself
    (``PathFinder``), not via ``sys.modules``: a test elsewhere may park a bare
    ``types.ModuleType("plugins")`` fake there, and that is not an alias."""

    @pytest.mark.parametrize("root", sorted(_EXPECTED_ROOTS))
    def test_flat_root_is_not_on_sys_path(self, root):
        spec = importlib.machinery.PathFinder.find_spec(root)
        assert spec is None, f"{root!r} is still importable as a top-level package: {spec}"
        loaded = sys.modules.get(root)
        assert loaded is None or getattr(loaded, "__file__", None) is None, (
            f"a real module sits under the flat name {root!r}: {loaded}"
        )

    @pytest.mark.parametrize("root", sorted(_EXPECTED_ROOTS))
    def test_each_root_imports_under_the_package(self, root):
        module = importlib.import_module(f"{mp.ROOT_PACKAGE}.{root}")
        assert module.__name__ == f"{mp.ROOT_PACKAGE}.{root}"

    @pytest.mark.parametrize("root", sorted(_EXPECTED_ROOTS))
    def test_stub_directory_is_gone(self, root):
        assert not (BACKEND_ROOT / root).exists(), f"stub package still on disk: {BACKEND_ROOT / root}"
        assert (PKG_ROOT / root).is_dir()  # utils/ is a namespace package: no __init__.py

    def test_repo_root_brain_stub_is_gone(self):
        assert not (REPO_ROOT / "brain").exists()

    def test_no_alias_finder_on_meta_path(self):
        finders = [f for f in sys.meta_path if type(f).__name__ == "FlatImportAliasFinder"]
        assert not finders, finders


# --------------------------------------------------------------------------
# 2b. acceptance: every wired string path is canonical and imports
# --------------------------------------------------------------------------
_CANONICAL = re.compile(rf"^{re.escape(mp.ROOT_PACKAGE)}\.[a-z_]+\.[A-Za-z0-9_.]+$")


def _string_module_paths(rel: str) -> list[tuple[int, str]]:
    """Every string constant in ``rel`` that names a project module or a
    ``module:attr`` object, excluding label-style call arguments."""
    src = (PKG_ROOT / rel).read_text(encoding="utf-8")
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
        # Entry-point GROUP names share the root's prefix: `"poindexter.modules"` in
        # registry.py's group map is a setuptools group, not a module path. Three
        # segments or more is what a module path this seam is asked for looks like.
        if not _CANONICAL.match(module_part) or " " in value or value.endswith("."):
            continue
        # A `/` operand is a filesystem path segment, never a module.
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


def _source_shipped(dotted: str) -> bool:
    """True when this checkout contains the module's source (file or package)."""
    parts = dotted.split(".")[1:]  # drop the root package
    rel = PKG_ROOT.joinpath(*parts)
    return rel.with_suffix(".py").is_file() or (rel / "__init__.py").is_file()


def _find_spec(dotted: str):
    """``find_spec`` raises ``ModuleNotFoundError`` when a *parent* package
    imports but the child does not; for this test that is simply "not found"."""
    try:
        return importlib.util.find_spec(dotted)
    except ModuleNotFoundError:
        return None


def _assert_importable(spec: str, *, where: str) -> None:
    """The project path is FOUND under its (canonical) spelling, then imported.
    A missing *project* module or any non-import error is a defect. A missing
    *third-party* module (optional extra) is the one tolerated outcome -- and
    only after find_spec succeeded."""
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
        assert missing_root and missing_root != mp.ROOT_PACKAGE and missing_root not in mp.PROJECT_ROOTS, (
            f"{where}: {spec!r} failed on a PROJECT module: {exc}"
        )
        pytest.skip(f"{where}: {spec!r} needs optional third-party {exc.name!r}")
    if attr:
        assert hasattr(module, attr), f"{where}: {spec!r} imports but has no attribute {attr!r}"


@pytest.mark.unit
@pytest.mark.parametrize(
    "rel,lineno,spec", _INVENTORY, ids=[f"{r}:{ln}:{v}" for r, ln, v in _INVENTORY]
)
def test_wired_string_path_is_canonical_and_imports(rel: str, lineno: int, spec: str):
    where = f"{rel}:{lineno}"
    _assert_importable(spec, where=where)
    # The flat form of the same path must be refused by the seam (DoD: one spelling).
    module_part, _, _ = spec.partition(":")
    flat = module_part.split(".", 1)[1]
    with pytest.raises(ModuleNotFoundError):
        mp.resolve_module_path(flat)


@pytest.mark.unit
def test_wired_call_sites_no_longer_import_project_paths_directly():
    """The seam only holds if the wired files stop calling importlib on a
    project path themselves. ``importlib.import_module(name)`` on a *variable*
    is fine inside the seam's own callers only when the argument was already
    resolved; the recognisable regression is a literal project path fed
    straight to importlib/__import__."""
    offenders: list[str] = []
    for rel in WIRED_FILES:
        tree = ast.parse((PKG_ROOT / rel).read_text(encoding="utf-8"))
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
                and _CANONICAL.match(node.args[0].value)
            ):
                offenders.append(f"{rel}:{node.lineno} {ast.unparse(node)[:80]}")
    assert not offenders, (
        "literal project paths still fed straight to importlib:\n  " + "\n  ".join(offenders)
    )
