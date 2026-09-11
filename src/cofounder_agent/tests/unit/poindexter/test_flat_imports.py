"""poindexter/_flat_imports.py -- the flat->canonical import alias (poindexter#1046 step 2).

Everything here runs against a SYNTHETIC package tree in ``tmp_path`` with its
own root name, so the real ``poindexter`` package is never touched and the
tests cannot leak aliases into the rest of the suite.

What must hold for the migration to be safe:

* identity -- the flat, umbrella and canonical spellings are ONE module object;
* patchability -- ``mock.patch("<flat>.x.y")`` changes what canonical code sees;
* fidelity -- the canonical module keeps its own ``__name__`` and ``__spec__``;
* honesty -- an ImportError inside the canonical module propagates unchanged;
* pre-move no-op -- with no canonical package present, flat imports resolve
  exactly as they did (synthetic tree; the real tree moved in step 2 PR A);
* idempotence -- installing twice yields one finder.
"""

from __future__ import annotations

import importlib
import sys
import textwrap
from pathlib import Path
from unittest import mock

import pytest

from poindexter import _flat_imports as fi

ROOT = "synth_root_1046"
FLAT = "svc_1046"  # the flat name being retired in the synthetic tree
UMB = "umb_1046"  # the umbrella package (real tree: cofounder_agent)


def _write(path: Path, body: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(body), encoding="utf-8")


@pytest.fixture
def tree(tmp_path: Path, monkeypatch):
    """Canonical tree only -- the post-move state: <ROOT>/<FLAT>/... exists, flat does not."""
    base = tmp_path / "site"
    _write(base / ROOT / "__init__.py", "")
    _write(base / ROOT / FLAT / "__init__.py", 'PKG_MARK = "canonical package"\n')
    _write(
        base / ROOT / FLAT / "x.py",
        """
        VALUE = 1
        REGISTRY: list = []

        class Klass:
            pass

        def read_value():
            return VALUE
        """,
    )
    _write(base / ROOT / FLAT / "taps" / "__init__.py", "")
    _write(base / ROOT / FLAT / "taps" / "y.py", "Y = 'deep'\n")
    _write(
        base / ROOT / FLAT / "bad.py",
        "import definitely_not_an_installed_package_1046  # noqa: F401\n",
    )
    _write(base / UMB / "__init__.py", "")  # a real umbrella package, nothing inside
    monkeypatch.syspath_prepend(str(base))
    finder = fi.install(ROOT, {FLAT}, UMB)
    yield base
    fi.uninstall(ROOT)
    for name in [
        n
        for n in sys.modules
        if n == ROOT
        or n.startswith(ROOT + ".")
        or n == FLAT
        or n.startswith(FLAT + ".")
        or n == UMB
        or n.startswith(UMB + ".")
    ]:
        sys.modules.pop(name, None)
    assert finder not in sys.meta_path


@pytest.mark.unit
class TestIdentity:
    def test_flat_and_canonical_are_the_same_object(self, tree):
        flat = importlib.import_module(f"{FLAT}.x")
        canon = importlib.import_module(f"{ROOT}.{FLAT}.x")
        assert flat is canon
        assert sys.modules[f"{FLAT}.x"] is sys.modules[f"{ROOT}.{FLAT}.x"]

    def test_classes_are_identical_not_copies(self, tree):
        flat = importlib.import_module(f"{FLAT}.x")
        canon = importlib.import_module(f"{ROOT}.{FLAT}.x")
        assert flat.Klass is canon.Klass
        assert isinstance(flat.Klass(), canon.Klass)

    def test_module_level_state_is_shared(self, tree):
        flat = importlib.import_module(f"{FLAT}.x")
        canon = importlib.import_module(f"{ROOT}.{FLAT}.x")
        flat.REGISTRY.append("seen")
        assert canon.REGISTRY == ["seen"]

    def test_top_level_flat_package_aliases_too(self, tree):
        flat_pkg = importlib.import_module(FLAT)
        assert flat_pkg is importlib.import_module(f"{ROOT}.{FLAT}")
        assert flat_pkg.PKG_MARK == "canonical package"

    def test_nested_subpackage(self, tree):
        deep = importlib.import_module(f"{FLAT}.taps.y")
        assert deep is importlib.import_module(f"{ROOT}.{FLAT}.taps.y")
        assert deep.Y == "deep"

    def test_from_import_form(self, tree):
        ns: dict = {}
        exec(f"from {FLAT} import x as flat_x\nfrom {ROOT}.{FLAT} import x as canon_x", ns)
        assert ns["flat_x"] is ns["canon_x"]

    def test_umbrella_spelling_is_the_same_object(self, tree):
        """<umbrella>.<flat>.x -- the spelling pyproject entry points use -- aliases too."""
        umb = importlib.import_module(f"{UMB}.{FLAT}.x")
        assert umb is importlib.import_module(f"{ROOT}.{FLAT}.x")
        assert umb is importlib.import_module(f"{FLAT}.x")
        assert importlib.import_module(f"{UMB}.{FLAT}") is importlib.import_module(f"{ROOT}.{FLAT}")

    def test_canonical_first_then_flat(self, tree):
        canon = importlib.import_module(f"{ROOT}.{FLAT}.x")
        flat = importlib.import_module(f"{FLAT}.x")
        assert flat is canon


@pytest.mark.unit
class TestFidelity:
    def test_canonical_name_and_spec_are_not_clobbered(self, tree):
        flat = importlib.import_module(f"{FLAT}.x")
        assert flat.__name__ == f"{ROOT}.{FLAT}.x"
        assert flat.__spec__.name == f"{ROOT}.{FLAT}.x"
        assert flat.__spec__.loader is not None
        assert not isinstance(flat.__spec__.loader, fi._AliasLoader)

    def test_class_module_attribute_is_canonical(self, tree):
        flat = importlib.import_module(f"{FLAT}.x")
        assert flat.Klass.__module__ == f"{ROOT}.{FLAT}.x"


@pytest.mark.unit
class TestPatchability:
    def test_mock_patch_on_flat_name_changes_what_canonical_code_sees(self, tree):
        """The payoff: ~4,000 existing mock.patch("services.x.y") strings keep working."""
        canon = importlib.import_module(f"{ROOT}.{FLAT}.x")
        with mock.patch(f"{FLAT}.x.VALUE", 42):
            assert canon.VALUE == 42
            assert canon.read_value() == 42
        assert canon.read_value() == 1

    def test_monkeypatch_string_target_on_flat_name(self, tree, monkeypatch):
        canon = importlib.import_module(f"{ROOT}.{FLAT}.x")
        monkeypatch.setattr(f"{FLAT}.x.VALUE", 7)
        assert canon.VALUE == 7


@pytest.mark.unit
class TestHonesty:
    def test_third_party_import_error_inside_canonical_propagates(self, tree):
        with pytest.raises(ModuleNotFoundError) as exc:
            importlib.import_module(f"{FLAT}.bad")
        # The REAL missing module is reported, not a misleading "No module named svc.bad".
        assert exc.value.name == "definitely_not_an_installed_package_1046"

    def test_missing_submodule_fails_normally(self, tree):
        with pytest.raises(ModuleNotFoundError):
            importlib.import_module(f"{FLAT}.does_not_exist")

    def test_non_flat_imports_are_untouched(self, tree):
        finder = next(
            f for f in sys.meta_path if isinstance(f, fi.FlatImportAliasFinder) and f.root == ROOT
        )
        assert finder.find_spec("json") is None
        assert finder.find_spec("os.path") is None
        assert finder.find_spec(f"{ROOT}.{FLAT}.x") is None  # canonical head -> not ours


@pytest.mark.unit
class TestCircularReentry:
    """A canonical module whose execution re-enters the SAME flat name mid-import
    must still be executed exactly once, with one object under every spelling.
    The first finder imported inside find_spec and CPython's _find_spec then
    swapped in the canonical spec -> a second execution (the task_routes /
    services.integrations double import PR A hit)."""

    ROOT, FLAT = "synth_root_cyc_1046", "svc_cyc_1046"

    @pytest.fixture
    def cyc_tree(self, tmp_path: Path, monkeypatch):
        base = tmp_path / "cyc"
        R, F = self.ROOT, self.FLAT
        _write(base / R / "__init__.py", "")
        # Shape 1: the package __init__ imports its own child by the FLAT name
        # (services/integrations/__init__.py does this).
        _write(
            base / R / F / "__init__.py",
            f"""
            import sys
            sys._pkg_exec_1046 = getattr(sys, "_pkg_exec_1046", 0) + 1
            from {F}.child import CHILD
            """,
        )
        _write(base / R / F / "child.py", "CHILD = 'child'\n")
        # Shape 2: module a imports b, which imports back from a by the FLAT name
        # (routes/task_routes.py <-> task_publishing_routes.py).
        _write(
            base / R / F / "a.py",
            f"""
            import sys
            sys._a_exec_1046 = getattr(sys, "_a_exec_1046", 0) + 1
            EARLY = 'defined before the cycle'
            from {F}.b import B
            LATE = 'defined after'
            """,
        )
        _write(base / R / F / "b.py", f"from {F}.a import EARLY\nB = EARLY.upper()\n")
        monkeypatch.syspath_prepend(str(base))
        finder = fi.install(R, {F})
        for attr in ("_pkg_exec_1046", "_a_exec_1046"):
            monkeypatch.delattr(sys, attr, raising=False)
        yield base
        fi.uninstall(R)
        for name in [
            n
            for n in sys.modules
            if n == R or n.startswith(R + ".") or n == F or n.startswith(F + ".")
        ]:
            sys.modules.pop(name, None)
        assert finder not in sys.meta_path

    def test_package_importing_its_own_child_by_flat_name_executes_once(self, cyc_tree):
        R, F = self.ROOT, self.FLAT
        pkg = importlib.import_module(F)
        assert sys._pkg_exec_1046 == 1  # type: ignore[attr-defined]
        assert pkg is sys.modules[f"{R}.{F}"] is sys.modules[F]
        assert sys.modules[f"{F}.child"] is sys.modules[f"{R}.{F}.child"]
        assert pkg.CHILD == "child"

    def test_mutual_cycle_through_the_flat_name_executes_once(self, cyc_tree):
        R, F = self.ROOT, self.FLAT
        a = importlib.import_module(f"{F}.a")
        assert sys._a_exec_1046 == 1  # type: ignore[attr-defined]
        assert a is sys.modules[f"{R}.{F}.a"] is sys.modules[f"{F}.a"]
        assert sys.modules[f"{F}.b"] is sys.modules[f"{R}.{F}.b"]
        assert a.B == "DEFINED BEFORE THE CYCLE" and a.LATE == "defined after"

    def test_canonical_first_then_flat_still_one_object(self, cyc_tree):
        R, F = self.ROOT, self.FLAT
        canon = importlib.import_module(f"{R}.{F}.a")
        assert importlib.import_module(f"{F}.a") is canon
        assert sys._a_exec_1046 == 1  # type: ignore[attr-defined]


@pytest.mark.unit
def test_pre_move_state_is_a_no_op(tmp_path: Path, monkeypatch):
    """No canonical package -> the finder steps aside and the flat package on
    sys.path imports exactly as today. Installing early is safe."""
    root, flat = "synth_root_premove_1046", "flat_premove_1046"
    base = tmp_path / "flat_only"
    _write(base / flat / "__init__.py", "")
    _write(base / flat / "z.py", "Z = 'flat file'\n")
    monkeypatch.syspath_prepend(str(base))
    fi.install(root, {flat})
    try:
        mod = importlib.import_module(f"{flat}.z")
        assert mod.Z == "flat file"
        assert mod.__name__ == f"{flat}.z"  # genuinely flat, not aliased
    finally:
        fi.uninstall(root)
        for name in [n for n in sys.modules if n == flat or n.startswith(flat + ".")]:
            sys.modules.pop(name, None)


@pytest.mark.unit
def test_install_is_idempotent_per_root():
    root = "synth_root_idem_1046"
    try:
        a = fi.install(root, {"nothing_1046"})
        b = fi.install(root, {"nothing_1046"})
        assert a is b
        assert (
            sum(
                1
                for f in sys.meta_path
                if isinstance(f, fi.FlatImportAliasFinder) and f.root == root
            )
            == 1
        )
    finally:
        fi.uninstall(root)


@pytest.mark.unit
def test_flat_roots_match_the_resolver_single_source_of_truth():
    """The finder cannot import module_paths (it must stay dependency-free and
    runs before the package exists), so the two root sets are pinned equal here."""
    from services import module_paths as mp

    assert fi.FLAT_ROOTS == mp.PROJECT_ROOTS
    assert fi.ROOT == mp.FUTURE_ROOT


@pytest.mark.unit
def test_real_finder_is_active_and_the_real_tree_is_aliased(monkeypatch):
    """Step 2 PR A: poindexter/__init__.py installs the real finder, so on the REAL
    tree the flat and canonical spellings are one object. This is the acceptance
    test for the move itself -- not a synthetic tree."""
    import poindexter  # noqa: F401 -- activation happens on import

    real = [
        f for f in sys.meta_path if isinstance(f, fi.FlatImportAliasFinder) and f.root == fi.ROOT
    ]
    assert len(real) == 1, f"expected exactly one real finder, found {len(real)}"
    import poindexter.services.module_paths as canon
    import services.module_paths as flat

    assert flat is canon
    assert sys.modules["services"] is sys.modules["poindexter.services"]
    assert flat.__name__ == "poindexter.services.module_paths"
    assert flat.ROOT_PACKAGE == "poindexter"
    # The umbrella spelling needs `src/` importable: the poetry manifest ships
    # src/cofounder_agent as the `cofounder_agent` package, but the test env does
    # not install it (CI is `poetry install --no-root`) and step 4 of the epic
    # dropped the pytest pythonpath entry that used to stand in for it. Build the
    # condition here, and drop any `cofounder_agent` another checkout's editable
    # install may have put in sys.modules, so the assertion is about THIS tree.
    src_dir = Path(poindexter.__file__).resolve().parents[2]
    assert (src_dir / "cofounder_agent" / "__init__.py").is_file(), src_dir
    monkeypatch.syspath_prepend(str(src_dir))
    for key in [k for k in sys.modules if k == "cofounder_agent" or k.startswith("cofounder_agent.")]:
        monkeypatch.delitem(sys.modules, key)
    before = set(sys.modules)
    try:
        import cofounder_agent.services.module_paths as umbrella  # the entry-point spelling

        assert umbrella is canon
    finally:
        for key in set(sys.modules) - before:
            if key == "cofounder_agent" or key.startswith("cofounder_agent."):
                sys.modules.pop(key, None)
