"""A test whose only assertions live in a loop over a possibly-empty iterable
has not tested anything when that iterable is empty.

It goes green, it stays green, and it stays green for exactly as long as the
bug that emptied the iterable survives. `test_every_route_requires_auth`
asserted that every data-plane route declares ``verify_api_token``; with the
router emptied — what a registration bug produces — it passed, certifying that
every route was guarded while none were.

The repo already holds this doctrine on the other side of the fence.
``scripts/ci/lib_scan_floor.py::require_scanned`` exists because ten CI lints
were found reporting clean on a missing scan root, and CLAUDE.md states it
outright: *a check that scanned nothing has not passed*. This applies the same
rule to the tests.

SCOPE, deliberately narrow. Only loops whose iterable's emptiness cannot be
read off the page are flagged — a call, an attribute, or a bare name not bound
to a non-empty literal in the same file. Looping a module constant
(``for m in _FLEET``) is not flagged: that iterable is visibly non-empty, and
flagging it would bury the real signal. Same reasoning as the bandit and
semgrep ratchets — see CLAUDE.md on noisy static analysis.

ACCEPTED FLOORS (either is fine):
  * ``nonempty(...)`` / ``anonempty(...)`` from ``tests.unit._nonempty``
  * ``for ... else: pytest.fail(...)`` — the loop completing without a break
    fails the test, so an empty iterable cannot pass silently.

WHEN EMPTY IS THE PASSING STATE, do not add a floor — state the negative
directly instead, as ``test_uses_get_secret_not_sync_get`` does. A loop over
``mock.mock_calls`` asserting "this was never called" is correct when empty,
and a floor there would assert the opposite of the intent.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

TESTS_ROOT = Path(__file__).resolve().parent          # tests/unit
SAFE_CALLS = {"range", "enumerate", "zip", "reversed", "sorted", "list", "tuple", "set", "dict"}
ASSERT_CTX = ("raises", "warns", "fail", "approx", "xfail", "exit")

# Loops that are correct precisely BECAUSE the iterable can be empty: the
# assertion is a negative ("never called with X"), so zero iterations passes.
# Keyed by "<relative path>::<test name>" and justified, not silently skipped.
EMPTY_IS_SUCCESS: dict[str, str] = {
    "services/test_brain_compose_drift_probe.py::test_audit_payload_does_not_leak_env_values":
        "Asserts a secret does NOT appear in any string arg of the audit write. "
        "A call with no string args leaks nothing, so zero iterations is a pass, "
        "not a blind spot. The outer loop over execute.call_args_list is floored, "
        "which is what proves the probe wrote at all.",
    "services/test_media_gpu_budgets.py::test_media_locks_use_background_priority":
        "Asserts that WHERE a gpu-lock call passes priority=, the value is "
        "'background'. A call with no keywords has nothing to check. The outer "
        "loop over _gpu_lock_calls(path) is floored, so the file is known to "
        "contain lock calls. (A lock call missing priority entirely would slip "
        "past this test — a real gap, but one about what the test asserts, not "
        "about whether it ran.)",
}


def _is_assertish(node: ast.AST) -> bool:
    if isinstance(node, (ast.Assert, ast.Raise)):
        return True
    if isinstance(node, (ast.With, ast.AsyncWith)):
        for item in node.items:
            call = item.context_expr
            if isinstance(call, ast.Call):
                name = getattr(call.func, "attr", None) or getattr(call.func, "id", None)
                if name in ASSERT_CTX:
                    return True
    if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
        name = getattr(node.value.func, "attr", "") or ""
        if name.startswith("assert_") or name in ("fail", "exit"):
            return True
    return False


def _nonempty_literal(node: ast.AST) -> bool:
    return isinstance(node, (ast.List, ast.Tuple, ast.Set, ast.Dict)) and bool(
        getattr(node, "elts", None) or getattr(node, "keys", None)
    )


def _literal_names(tree: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and _nonempty_literal(node.value):
            names.update(t.id for t in node.targets if isinstance(t, ast.Name))
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            if _nonempty_literal(node.value) and isinstance(node.target, ast.Name):
                names.add(node.target.id)
    return names


def _emptiness_unknown(it: ast.AST, literal_names: set[str]) -> bool:
    """True when the page does not show that the iterable is non-empty."""
    if _nonempty_literal(it):
        return False
    if isinstance(it, ast.Name):
        return it.id not in literal_names
    if isinstance(it, ast.Call):
        fn = getattr(it.func, "id", None)
        base = getattr(it.func, "value", None)
        attr = getattr(it.func, "attr", None)
        # `CONST.items()` is as visible as CONST itself, and so is a dict
        # literal written at the loop head.
        if attr in ("items", "keys", "values"):
            if isinstance(base, ast.Name):
                return base.id not in literal_names
            if _nonempty_literal(base):
                return False
        if fn in SAFE_CALLS:
            return not all(isinstance(a, ast.Constant) for a in it.args)
        return True
    return True


def _has_floor(loop: ast.AST) -> bool:
    if "nonempty(" in ast.unparse(loop.iter):          # nonempty / anonempty
        return True
    if loop.orelse:                                     # for ... else: pytest.fail(...)
        block = ast.Module(body=loop.orelse, type_ignores=[])
        if any(_is_assertish(n) for n in ast.walk(block)):
            return True
    return False


def _offenders(path: Path) -> list[str]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except SyntaxError:
        return []
    literal_names = _literal_names(tree)
    out: list[str] = []
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not fn.name.startswith("test_"):
            continue
        key = f"{path.relative_to(TESTS_ROOT)}::{fn.name}"
        if key in EMPTY_IS_SUCCESS:
            continue
        in_loop: dict[int, ast.AST] = {}
        for node in ast.walk(fn):
            if isinstance(node, (ast.For, ast.AsyncFor)):
                for child in ast.walk(node):
                    in_loop[id(child)] = node
        asserts = [n for n in ast.walk(fn) if _is_assertish(n)]
        if not asserts:
            continue                                    # `no_assert` is a separate question
        if any(id(a) not in in_loop for a in asserts):
            continue                                    # something asserts unconditionally
        # A test is vacuous only when NO assertion is guaranteed to be reached.
        # If any assertion sits directly inside a floored loop, the floor makes
        # that loop run, so the test does assert something — a deeper nested
        # loop over `entry` is then guarded by the outer floor and the
        # assertions above it, not by a second floor of its own.
        owning = {id(in_loop[id(a)]): in_loop[id(a)] for a in asserts if id(a) in in_loop}
        if any(_has_floor(loop) for loop in owning.values()):
            continue
        for loop in owning.values():
            if _emptiness_unknown(loop.iter, literal_names):
                out.append(f"{key} (line {loop.lineno}: for ... in {ast.unparse(loop.iter)[:60]})")
                break
    return out


def test_scan_floor_the_gate_examined_tests():
    """This gate is itself a scan — it must fail if it scanned nothing."""
    files = list(TESTS_ROOT.rglob("test_*.py"))
    assert len(files) > 500, f"only found {len(files)} test files — the walk broke"


def test_no_loop_only_assertions_over_possibly_empty_iterables():
    files = sorted(TESTS_ROOT.rglob("test_*.py"))
    findings: list[str] = []
    for path in files:
        findings.extend(_offenders(path))
    assert not findings, (
        f"{len(findings)} test(s) assert ONLY inside a loop whose iterable may be "
        "empty. An empty iterable makes them pass having checked nothing:\n  "
        + "\n  ".join(sorted(findings))
        + "\n\nAdd a floor: wrap the iterable in nonempty()/anonempty() from "
        "tests.unit._nonempty, or use `for ... else: pytest.fail(...)`. If an "
        "EMPTY iterable is the passing state (a negative assertion such as "
        "'never called with X'), state that directly instead of looping, or "
        "record it in EMPTY_IS_SUCCESS with the reason."
    )


@pytest.mark.parametrize(
    "src,flagged,why",
    [
        (
            "def test_x():\n    for r in router.routes:\n        assert r.auth\n",
            True,
            "attribute iterable, no floor — the shape that shipped",
        ),
        (
            "def test_x():\n    for r in nonempty(router.routes, 'routes'):\n        assert r.auth\n",
            False,
            "nonempty() floor",
        ),
        (
            "def test_x():\n    for r in router.routes:\n        assert r.auth\n"
            "        break\n    else:\n        pytest.fail('none')\n",
            False,
            "for/else floor",
        ),
        (
            "_FLEET = [1, 2]\ndef test_x():\n    for m in _FLEET:\n        assert m\n",
            False,
            "module constant — visibly non-empty",
        ),
        (
            "def test_x():\n    for i in range(3):\n        assert i >= 0\n",
            False,
            "range with a constant",
        ),
        (
            "def test_x():\n    assert 1\n    for r in router.routes:\n        assert r.auth\n",
            False,
            "has an unconditional assertion too",
        ),
    ],
)
def test_detector_matches_the_hazard(tmp_path, src, flagged, why):
    """Guards the guard: a detector that matches nothing passes everything."""
    p = tmp_path / "test_sample.py"
    p.write_text(src, encoding="utf-8")
    global TESTS_ROOT
    original, TESTS_ROOT = TESTS_ROOT, tmp_path
    try:
        assert bool(_offenders(p)) is flagged, why
    finally:
        TESTS_ROOT = original
