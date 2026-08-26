"""Tests for scripts/ci/lint_exception_cause.py — the cause-less-failure guard.

Pins the detection contract for the empty-``str(exc)`` trap (poindexter#3229):
a ``JobResult(detail=...)`` or ``emit_finding(body=...)`` that interpolates a
caught exception bare renders cause-less for timeout-class exceptions
(``str(httpx.ReadTimeout("")) == ""``), so a job_failure page or Discord
finding names nothing an operator can act on. The fix the lint points at is
``utils.exception_format.describe_exception``.

The detector keys on the (callable, keyword) pair inside an except handler —
``HTTPException(detail=...)`` is the *detail-leak* lint's territory and must
never be flagged here (opposite rule: that surface must NOT carry the
exception).
"""
import importlib.util
from pathlib import Path


def _find_repo_root(start: Path) -> Path:
    for parent in start.resolve().parents:
        if (parent / "scripts" / "ci" / "lint_exception_cause.py").exists():
            return parent
    raise RuntimeError("could not locate scripts/ci/lint_exception_cause.py")


def _load_lint_module():
    path = _find_repo_root(Path(__file__)) / "scripts" / "ci" / "lint_exception_cause.py"
    spec = importlib.util.spec_from_file_location("lint_exception_cause_under_test", path)
    assert spec and spec.loader, "could not build import spec for the lint module"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


LINT = _load_lint_module()


def _scan(src: str) -> list[int]:
    return LINT.scan_source(src)


def _in_handler(body_line: str) -> str:
    return (
        "def f():\n"
        "    try:\n        work()\n"
        "    except ValueError as exc:\n"
        f"        {body_line}\n"
    )


class TestFlagged:
    def test_detail_str_exc(self):
        assert _scan(_in_handler("return JobResult(ok=False, detail=str(exc))")) == [5]

    def test_detail_fstring_bare_exc(self):
        assert _scan(
            _in_handler('return JobResult(ok=False, detail=f"query failed: {exc}")')
        ) == [5]

    def test_body_fstring_bare_exc(self):
        assert _scan(_in_handler('emit_finding(kind="x", body=f"boom: {exc}")')) == [5]

    def test_body_str_exc(self):
        assert _scan(_in_handler('emit_finding(kind="x", body=str(exc))')) == [5]

    def test_fstring_str_call_form(self):
        assert _scan(
            _in_handler('return JobResult(ok=False, detail=f"failed: {str(exc)}")')
        ) == [5]

    def test_fstring_conversion_repr(self):
        assert _scan(
            _in_handler('return JobResult(ok=False, detail=f"failed: {exc!r}")')
        ) == [5]

    def test_handrolled_type_name_pair_still_flagged(self):
        # The second interpolation is the bare name — and the hand-rolled
        # form renders "ReadTimeout: " (dangling colon) on empty messages.
        assert _scan(
            _in_handler(
                'return JobResult(ok=False, detail=f"{type(exc).__name__}: {exc}")'
            )
        ) == [5]

    def test_multiline_call_flagged(self):
        src = (
            "def f():\n"
            "    try:\n        work()\n"
            "    except ValueError as e:\n"
            "        return JobResult(\n"
            "            ok=False,\n"
            '            detail=f"data gather failed: {e}",\n'
            "            changes_made=0,\n"
            "        )\n"
        )
        assert _scan(src) == [5]


class TestClean:
    def test_describe_exception_wrapped_is_clean(self):
        assert (
            _scan(
                _in_handler(
                    'return JobResult(ok=False, detail=f"query failed: '
                    '{describe_exception(exc)}")'
                )
            )
            == []
        )

    def test_attribute_extraction_is_deliberate_and_clean(self):
        assert _scan(_in_handler('emit_finding(kind="x", body=f"{exc.args[0]}")')) == []

    def test_httpexception_detail_is_other_lints_territory(self):
        # The detail-leak lint FORBIDS interpolation there; flagging it here
        # too would demand contradictory fixes on one line.
        assert (
            _scan(_in_handler('raise HTTPException(status_code=500, detail=f"{exc}")'))
            == []
        )

    def test_outside_handler_is_clean(self):
        src = 'def f(msg):\n    return JobResult(ok=False, detail=f"failed: {msg}")\n'
        assert _scan(src) == []

    def test_unrelated_name_in_handler_is_clean(self):
        assert (
            _scan(_in_handler('return JobResult(ok=False, detail=f"failed for {slug}")'))
            == []
        )

    def test_logger_calls_are_out_of_scope(self):
        # Log lines were swept by hand (stack#3356) but are not gated — the
        # lint guards the alerting surfaces only.
        assert _scan(_in_handler('logger.warning("failed: %s", exc)')) == []

    def test_override_marker_suppresses(self):
        assert (
            _scan(
                _in_handler(
                    "return JobResult(ok=False, detail=str(exc))  # noqa: cause-ok reviewed"
                )
            )
            == []
        )

    def test_syntax_error_returns_empty(self):
        assert _scan("def f(:\n") == []


class TestLiveTree:
    def test_production_tree_is_clean(self):
        """The lint ships with a clean tree — fail-on-any, no baseline."""
        offenders = []
        for root, excluded in LINT.SCAN_ROOTS:
            if not root.exists():
                continue
            for py in sorted(root.rglob("*.py")):
                rel_parts = py.relative_to(root).parts
                if excluded and rel_parts and rel_parts[0] in excluded:
                    continue
                for lineno in LINT.scan_file(py):
                    offenders.append(f"{py}:{lineno}")
        assert offenders == [], (
            "cause-less failure strings crept back in — wrap with "
            "utils.exception_format.describe_exception: " + ", ".join(offenders)
        )
