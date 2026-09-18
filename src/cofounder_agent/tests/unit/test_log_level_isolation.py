"""The logging level is process state, and two things must hold for it.

No test may READ the ambient level (the gate below), and no test may leave a
level it WROTE behind (``conftest._restore_root_log_level``, verified at the
bottom of this file). Either alone leaves a hole; together they close the
2026-09-18 xdist flake described here.

``caplog`` sees a record only if the emitting logger let it through, and a
logger with no level of its own inherits the root's. Importing
``poindexter.services.logger_config`` sets that root level to ``LOG_LEVEL``
(default INFO) as a module-init side effect — so a test that asserts on an INFO
line without calling ``caplog.set_level`` is not asserting about the code under
test. It is asserting about a process-wide level that anything can move, and
that production code legitimately does move:
``poindexter.cli.pipeline._run`` forces the root to WARNING on every
``pipeline`` subcommand so the operator's terminal isn't flooded.

That is exactly how ``test_topic_sources_igdb.py::test_skips_when_credentials_missing``
became an intermittent failure (2026-09-18). Its "not configured" line is INFO.
Any test that ran a ``pipeline`` subcommand through ``CliRunner`` first left the
worker at WARNING, the record was never emitted, and the assertion failed —
while an isolated re-run passed. Under xdist it depended on which worker drew
the CLI tests, so it read as flake rather than as a missing declaration. The
leak is stopped by the autouse ``conftest._restore_root_log_level`` fixture
(helper in ``tests/unit/_log_isolation.py``); the gate below stops tests from
depending on the ambient level in the first place. Both are needed — the
fixture cannot help a run that starts with ``LOG_LEVEL=WARNING`` in the
environment, and the declaration cannot help a test that asserts nothing about
logs but still moves the level.

Same doctrine as ``test_no_vacuous_loop_assertions`` next door, and as
``scripts/ci/lib_scan_floor.py`` on the CI side: a check that could not have
observed what it claims to check has not passed.

A NEGATIVE assertion needs the declaration MORE, not less. ``assert not
caplog.records`` over a too-high level certifies a silence it was never able to
hear — so those are flagged too, and the fix is the same one line at
``logging.DEBUG``.

SCOPE, deliberately narrow: only ``test_*`` functions that take ``caplog`` AND
read the capture off it. Requesting the fixture without reading it (a stale
parameter) changes no outcome and is not flagged.
"""

from __future__ import annotations

import ast
import logging
from pathlib import Path

import pytest

from tests.unit._log_isolation import root_log_level_restored

TESTS_ROOT = Path(__file__).resolve().parent          # tests/unit

# Reading any of these means the test's outcome depends on what was captured.
CAPTURE_READS = frozenset(
    {"records", "text", "messages", "record_tuples", "get_records"}
)
# Declaring the level: `caplog.set_level(...)` or `with caplog.at_level(...)`.
LEVEL_DECLARATIONS = frozenset({"set_level", "at_level"})

# Tests whose level is declared somewhere this per-function scan cannot see —
# a fixture, or a shared helper. Keyed "<relative path>::<test name>" and
# justified, so the exemption is reviewable rather than silent. Empty today:
# every level declaration in the suite is inline in the test that needs it.
LEVEL_SET_ELSEWHERE: dict[str, str] = {}


def _reads_capture(fn: ast.AST) -> bool:
    for node in ast.walk(fn):
        if isinstance(node, ast.Attribute) and node.attr in CAPTURE_READS:
            if isinstance(node.value, ast.Name) and node.value.id == "caplog":
                return True
    return False


def _declares_level(fn: ast.AST) -> bool:
    for node in ast.walk(fn):
        if isinstance(node, ast.Attribute) and node.attr in LEVEL_DECLARATIONS:
            if isinstance(node.value, ast.Name) and node.value.id == "caplog":
                return True
    return False


def _takes_caplog(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    args = fn.args
    named = [a.arg for a in (*args.posonlyargs, *args.args, *args.kwonlyargs)]
    return "caplog" in named


def _scan(path: Path) -> tuple[list[str], int]:
    """Return (offenders, number of capture-reading tests seen) for one file."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except SyntaxError:
        return [], 0
    offenders: list[str] = []
    seen = 0
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not fn.name.startswith("test_") or not _takes_caplog(fn):
            continue
        if not _reads_capture(fn):
            continue                                   # stale parameter, no outcome rides on it
        seen += 1
        key = f"{path.relative_to(TESTS_ROOT)}::{fn.name}"
        if key in LEVEL_SET_ELSEWHERE:
            continue
        if not _declares_level(fn):
            offenders.append(f"{key} (line {fn.lineno})")
    return offenders, seen


def _scan_all() -> tuple[list[str], int, int]:
    files = sorted(TESTS_ROOT.rglob("test_*.py"))
    offenders: list[str] = []
    seen = 0
    for path in files:
        found, n = _scan(path)
        offenders.extend(found)
        seen += n
    return offenders, seen, len(files)


def test_scan_floor_the_gate_examined_tests():
    """This gate is itself a scan — it must fail if it scanned nothing.

    Two floors, because they fail differently: a broken walk finds no files,
    while a rotted ``CAPTURE_READS`` walks every file and recognises nothing.
    """
    _, seen, file_count = _scan_all()
    assert file_count > 500, f"only found {file_count} test files — the walk broke"
    assert seen > 100, (
        f"only {seen} tests were recognised as reading caplog — the detector "
        "stopped matching (was ~185 at 2026-09-18)"
    )


def test_every_caplog_assertion_declares_its_level():
    offenders, _, _ = _scan_all()
    assert not offenders, (
        f"{len(offenders)} test(s) assert on captured log records without "
        "declaring a capture level. They pass or fail on the process-wide root "
        "logger level, which another test can move:\n  "
        + "\n  ".join(sorted(offenders))
        + "\n\nAdd `caplog.set_level(logging.INFO, logger=<module>.__name__)` "
        "(or `with caplog.at_level(...)`) naming the level the assertion needs. "
        "For a negative assertion — `assert not caplog.records` — declare "
        "logging.DEBUG, so the silence is one the test could actually have "
        "heard. If a fixture sets the level instead, record the test in "
        "LEVEL_SET_ELSEWHERE with the reason."
    )


@pytest.mark.parametrize(
    "src,flagged,why",
    [
        (
            "def test_x(caplog):\n    go()\n    assert any('x' in r.message for r in caplog.records)\n",
            True,
            "reads records, declares nothing — the shape that flaked",
        ),
        (
            "def test_x(caplog):\n    caplog.set_level(logging.INFO)\n    go()\n"
            "    assert 'x' in caplog.text\n",
            False,
            "set_level declares the level",
        ),
        (
            "def test_x(caplog):\n    with caplog.at_level(logging.DEBUG):\n        go()\n"
            "    assert caplog.messages\n",
            False,
            "at_level declares the level",
        ),
        (
            "def test_x(caplog):\n    assert not caplog.records\n",
            True,
            "a negative assertion needs the declaration more, not less",
        ),
        (
            "def test_x(caplog):\n    assert go() == 1\n",
            False,
            "stale caplog parameter — no outcome rides on the capture",
        ),
        (
            "async def test_x(caplog):\n    await go()\n    assert caplog.records\n",
            True,
            "async tests are scanned the same way",
        ),
        (
            "def helper(caplog):\n    assert caplog.records\n",
            False,
            "not a test_ function",
        ),
        (
            "def test_x(other):\n    assert other.records\n",
            False,
            "`records` on something that is not caplog",
        ),
    ],
)
def test_detector_matches_the_hazard(tmp_path, src, flagged, why):
    """Guards the guard: a detector that matches nothing passes everything."""
    path = tmp_path / "test_sample.py"
    path.write_text(src, encoding="utf-8")
    global TESTS_ROOT
    original, TESTS_ROOT = TESTS_ROOT, tmp_path
    try:
        offenders, _ = _scan(path)
        assert bool(offenders) is flagged, why
    finally:
        TESTS_ROOT = original


# ---------------------------------------------------------------------------
# The other half: the leak itself. The gate above keeps tests from READING the
# ambient level; this keeps the code under test from WRITING it past its own
# test. Driving the context manager directly rather than relying on two ordered
# tests, which xdist is free to split across workers.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("moved_to", [logging.WARNING, logging.ERROR, logging.DEBUG])
def test_root_level_is_put_back(moved_to):
    root = logging.getLogger()
    before = root.level
    with root_log_level_restored():
        root.setLevel(moved_to)          # what _quiet_service_logging() does
        assert root.level == moved_to, "the block must still see its own write"
    assert root.level == before


def test_root_level_is_put_back_even_when_the_block_raises():
    """A test that fails after moving the level must not take the rest down."""
    root = logging.getLogger()
    before = root.level
    with pytest.raises(AssertionError):
        with root_log_level_restored():
            root.setLevel(logging.ERROR)
            raise AssertionError("the test under the fixture failed")
    assert root.level == before


def test_global_disable_floor_is_put_back():
    """``logging.disable()`` outranks every logger level, so it leaks harder."""
    before = logging.root.manager.disable
    with root_log_level_restored():
        logging.disable(logging.CRITICAL)
        assert logging.root.manager.disable == logging.CRITICAL
    assert logging.root.manager.disable == before


def test_the_autouse_fixture_is_actually_wired():
    """The helper only helps if conftest still calls it on every test.

    Asserting on the fixture NAME rather than on behaviour: behaviour is
    already covered above, and what rots is the wiring — a fixture deleted or
    renamed in conftest leaves these tests green while every test in the suite
    goes unprotected.
    """
    conftest = TESTS_ROOT / "conftest.py"
    src = conftest.read_text(encoding="utf-8")
    assert "root_log_level_restored" in src, (
        "tests/unit/conftest.py no longer uses root_log_level_restored — the "
        "autouse fixture that contains a root-logger level write is gone, and "
        "nothing between tests puts the level back."
    )
    tree = ast.parse(src)
    fixtures = [
        fn.name
        for fn in ast.walk(tree)
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef))
        and any(
            isinstance(d, ast.Call)
            and any(
                kw.arg == "autouse" and getattr(kw.value, "value", False) is True
                for kw in d.keywords
            )
            for d in fn.decorator_list
        )
    ]
    assert "_restore_root_log_level" in fixtures, (
        f"_restore_root_log_level is not an autouse fixture in conftest "
        f"(autouse fixtures found: {sorted(fixtures)})"
    )
