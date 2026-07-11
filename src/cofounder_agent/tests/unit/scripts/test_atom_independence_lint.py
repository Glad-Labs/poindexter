"""Tests for scripts/ci/atom_independence_lint.py — the atom-independence guard.

Pins the invariant that keeps ``modules/content/atoms/`` composable by an
architect LLM building ad-hoc graphs: an atom (a graph node) may depend
*downward* on utilities/libraries, but it must NOT reference a stage or a
sibling atom. If atom A imports atom B, placing A on a graph silently drags B
along — B's requires/produces contract is invisible to the graph_def, breaking
composition.

The registry already enforces the node/library split at discovery time
(``atom_registry`` skips ``_``-prefixed modules), so this lint reuses the same
contract: importing an ``atoms/_helper`` (library) or a module-root helper
(``modules.content.api``) or substrate (``services.*``) is fine;
importing ``modules.content.stages.*`` or a non-underscore sibling atom is a
violation. A ``# noqa: atom-independence-ok`` marker exempts an intentional
case. Existing violations are grandfathered in ``atom_independence_baseline.json``
(ratchet-only-shrinks).
"""

import importlib.util
from pathlib import Path


def _find_repo_root(start: Path) -> Path:
    for parent in start.resolve().parents:
        if (parent / "scripts" / "ci" / "atom_independence_lint.py").exists():
            return parent
    raise RuntimeError("could not locate scripts/ci/atom_independence_lint.py")


def _load_lint_module():
    path = _find_repo_root(Path(__file__)) / "scripts" / "ci" / "atom_independence_lint.py"
    spec = importlib.util.spec_from_file_location("atom_independence_lint_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


LINT = _load_lint_module()


def _count(src: str, *, check_sibling_atoms: bool = True) -> int:
    return len(LINT.scan_source(src, check_sibling_atoms=check_sibling_atoms))


class TestStageImportFlagged:
    def test_from_stage_import_is_flagged(self):
        src = "from modules.content.stages.generate_content import GenerateContentStage\n"
        assert _count(src) == 1

    def test_plain_import_of_stage_is_flagged(self):
        src = "import modules.content.stages.url_validation\n"
        assert _count(src) == 1

    def test_lazy_function_local_stage_import_is_flagged(self):
        # ast.walk descends into function bodies — a deferred import still couples.
        src = (
            "async def run(state):\n"
            "    from modules.content.stages.generate_content import GenerateContentStage\n"
            "    return {}\n"
        )
        assert _count(src) == 1

    def test_stage_violation_reason_names_the_module(self):
        src = "from modules.content.stages.generate_content import X\n"
        (lineno, reason), = LINT.scan_source(src, check_sibling_atoms=True)
        assert lineno == 1
        assert "stage" in reason.lower()
        assert "generate_content" in reason


class TestSiblingAtomImportFlagged:
    def test_from_atoms_import_sibling_node_is_flagged(self):
        src = "from modules.content.atoms import two_pass_writer\n"
        assert _count(src) == 1

    def test_dotted_sibling_atom_import_is_flagged(self):
        src = "from modules.content.atoms.two_pass_writer import run\n"
        assert _count(src) == 1

    def test_plain_import_sibling_atom_is_flagged(self):
        src = "import modules.content.atoms.two_pass_writer\n"
        assert _count(src) == 1


class TestAllowedDependencies:
    def test_underscore_sibling_library_not_flagged(self):
        # atoms/_qa_rail_common.py is a library (registry skips ``_``), not a node.
        src = "from modules.content.atoms._qa_rail_common import reviewer_to_dict\n"
        assert _count(src) == 0

    def test_from_atoms_import_underscore_lib_not_flagged(self):
        src = "from modules.content.atoms import _qa_persist\n"
        assert _count(src) == 0

    def test_module_root_library_not_flagged(self):
        # A module-root content file (not a stage, not a sibling atom) is a
        # library an atom may import — e.g. the module's api boundary.
        src = "from modules.content.api import build_topic_decision_artifact\n"
        assert _count(src) == 0

    def test_substrate_import_not_flagged(self):
        src = "from services.image_service import get_image_service\n"
        assert _count(src) == 0

    def test_plugins_import_not_flagged(self):
        src = "from plugins.atom import AtomMeta, FieldSpec\n"
        assert _count(src) == 0


class TestSiblingAtomRuleScope:
    def test_sibling_atom_not_flagged_for_non_atom_file(self):
        # A module-root library (check_sibling_atoms=False) is not itself a node,
        # so a sibling-atom import isn't its concern — but a STAGE import still is.
        src = (
            "from modules.content.atoms import two_pass_writer\n"
            "from modules.content.stages.url_validation import UrlValidationStage\n"
        )
        assert _count(src, check_sibling_atoms=False) == 1


class TestOverride:
    def test_atom_independence_ok_override_exempts(self):
        src = (
            "from modules.content.stages.generate_content import X  "
            "# noqa: atom-independence-ok legacy dev_diary bridge\n"
        )
        assert _count(src) == 0


class TestCounting:
    def test_two_violations_counted(self):
        src = (
            "from modules.content.stages.generate_content import X\n"
            "from modules.content.atoms.two_pass_writer import run\n"
        )
        assert _count(src) == 2


class TestBaselineRatchet:
    def test_scan_root_resolves_to_the_atom_tree(self):
        """Guard against the lint silently going blind (e.g. a scan-root typo).

        Both grandfathered fossils burned down in 2026-07 (image → the helper
        web moved to ``atoms/_image_helpers.py``; writer → ``GenerateContentStage``
        moved to the module-root ``modules/content/writer_core.py``), so the
        baseline is empty and there is no known violator left to assert on.
        Instead assert the scan root resolves and discovers the atom package —
        exactly what a scan-root typo would break.
        """
        names = {p.name for p in LINT._atom_files()}
        assert "content_generate_draft.py" in names, names
        assert len(names) > 20, len(names)  # the atom package is substantial

    def test_baseline_is_empty_after_burndown(self):
        """Zero grandfathered violations is the intended steady state. Pins it so
        a re-introduced atom→stage / atom→atom import (which would appear in
        ``compute_counts`` above the empty baseline) can't be quietly
        re-baselined back to green."""
        assert LINT.load_baseline() == {}

    def test_real_tree_matches_baseline(self):
        """The committed baseline must satisfy the live tree (no drift).

        Fails if an atom→stage / atom→atom import was added without baselining,
        or a violation was removed and the baseline wasn't lowered.
        """
        counts = LINT.compute_counts()
        baseline = LINT.load_baseline()
        offenders = {
            rel: (n, baseline.get(rel, 0))
            for rel, n in counts.items()
            if n > baseline.get(rel, 0)
        }
        assert offenders == {}, f"atom-independence baseline drift: {offenders}"
