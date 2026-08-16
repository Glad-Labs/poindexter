from __future__ import annotations

import sys
from pathlib import Path

import pytest


def _ops_dir() -> Path:
    return next(
        p for p in Path(__file__).resolve().parents
        if (p / "scripts" / "ops_sessions").exists()
    ) / "scripts" / "ops_sessions"


sys.path.insert(0, str(_ops_dir()))
import test_health as th  # noqa: E402


@pytest.fixture(autouse=True)
def _stub_pin_preflight(monkeypatch):
    """main() now preflights the model pin (stack#3163) with a real HTTP GET;
    stub it so these tests exercise the fix loop, not the operator box's
    live Ollama. The preflight has its own tests in test_ops_common.py."""
    monkeypatch.setattr(th.c, "preflight_model_pins", lambda *m, **k: None)


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


# --- order-dependent-failure gate ------------------------------------------
# Why this exists: the post-patch re-run gate re-runs ONE test id, so for a
# failure caused by leaked state it passes whatever the LLM wrote — including
# a rewrite that removes the assertions. All four failures test-health hit on
# 2026-08-07 were exactly that shape (green alone, red in a full run), so
# without a pre-flight every one would have become a bogus "fix" PR.


class _Proc:
    def __init__(self, returncode: int) -> None:
        self.returncode = returncode
        self.stdout = ""
        self.stderr = ""


def test_fails_in_isolation_true_for_a_genuinely_broken_test(monkeypatch):
    monkeypatch.setattr(th.c, "run", lambda *a, **k: _Proc(1))
    assert th._fails_in_isolation("/repo", "tests/unit/test_x.py::test_y") is True


def test_fails_in_isolation_false_when_it_passes_alone(monkeypatch):
    monkeypatch.setattr(th.c, "run", lambda *a, **k: _Proc(0))
    assert th._fails_in_isolation("/repo", "tests/unit/test_x.py::test_y") is False


def test_isolation_probe_runs_only_the_one_test_id(monkeypatch):
    """It must re-run the single node id, not the file or the suite — running
    the file would re-introduce intra-file ordering and defeat the check."""
    seen: dict = {}

    def fake_run(cmd, **kwargs):
        seen["cmd"] = cmd
        return _Proc(1)

    monkeypatch.setattr(th.c, "run", fake_run)
    th._fails_in_isolation("/repo", "tests/unit/test_x.py::test_y")
    assert "tests/unit/test_x.py::test_y" in seen["cmd"]
    # no:cacheprovider keeps the probe from writing .pytest_cache into the
    # session worktree (mirrors the main run's flags).
    assert "no:cacheprovider" in seen["cmd"]


def test_order_dependent_failures_never_reach_the_model(monkeypatch, tmp_path):
    """End-to-end on main(): a failure that passes in isolation must consume
    zero LLM calls and produce no PR."""
    calls: list = []

    monkeypatch.setattr(th, "_repo_root", lambda: tmp_path)
    (tmp_path / "src" / "cofounder_agent" / "tests").mkdir(parents=True)
    target = tmp_path / "src" / "cofounder_agent" / "tests" / "test_polluted.py"
    target.write_text("original contents\n", encoding="utf-8")

    def fake_run(cmd, **kwargs):
        # First call = the full-suite run (reports the failure); every later
        # call is the isolation probe, which passes.
        if not calls:
            calls.append("suite")
            p = _Proc(1)
            p.stdout = "FAILED tests/test_polluted.py::test_thing - boom\n"
            return p
        calls.append("probe")
        return _Proc(0)

    def boom(*a, **k):
        raise AssertionError("LLM must not be called for an order-dependent failure")

    monkeypatch.setattr(th.c, "run", fake_run)
    monkeypatch.setattr(th.c, "ollama_chat", boom)
    monkeypatch.setattr(
        th.c, "commit_and_open_pr",
        lambda **k: (_ for _ in ()).throw(AssertionError("must not open a PR")),
    )

    assert th.main() == 0
    # The victim file is left byte-identical — no speculative rewrite.
    assert target.read_text(encoding="utf-8") == "original contents\n"
    assert calls == ["suite", "probe"]
