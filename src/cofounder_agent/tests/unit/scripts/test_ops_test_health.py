from __future__ import annotations

import sys
from pathlib import Path


def _ops_dir() -> Path:
    return next(
        p for p in Path(__file__).resolve().parents
        if (p / "scripts" / "ops_sessions").exists()
    ) / "scripts" / "ops_sessions"


sys.path.insert(0, str(_ops_dir()))
import test_health as th  # noqa: E402


def test_parse_pytest_failures_extracts_nodeids():
    out = (
        "tests/unit/test_a.py::test_one PASSED\n"
        "FAILED tests/unit/test_b.py::test_two - AssertionError: 1 != 2\n"
        "FAILED tests/unit/scripts/test_c.py::test_three\n"
    )
    failures = th.parse_pytest_failures(out)
    assert {"file": "tests/unit/test_b.py", "test": "test_two", "message": "AssertionError: 1 != 2"} in failures
    assert any(f["test"] == "test_three" for f in failures)
    assert all("test_a" not in f["file"] for f in failures)


def test_extract_patched_file_pulls_code_fence():
    raw = "Here is the fix:\n```python\ndef test_x():\n    assert True\n```\nDone."
    assert th.extract_patched_file(raw) == "def test_x():\n    assert True"


def test_extract_patched_file_none_when_no_fence():
    assert th.extract_patched_file("no code here") is None
