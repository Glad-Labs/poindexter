from __future__ import annotations

import json
import logging
import sys
from pathlib import Path


def _ops_dir() -> Path:
    return next(
        p for p in Path(__file__).resolve().parents
        if (p / "scripts" / "ops_sessions").exists()
    ) / "scripts" / "ops_sessions"


sys.path.insert(0, str(_ops_dir()))
import codebase_audit as ca  # noqa: E402


class _FakeProc:
    """Minimal stand-in for subprocess.CompletedProcess (returncode + stdout only)."""

    def __init__(self, returncode: int, stdout: str):
        self.returncode = returncode
        self.stdout = stdout


_B608_FINDING = {
    "filename": "src/cofounder_agent/services/foo.py",
    "line_number": 42,
    "test_id": "B608",
    "issue_severity": "MEDIUM",
    "issue_text": "Possible SQL injection vector",
    "code": 'query = f"SELECT * FROM t WHERE id = {task_id}"\n',
}


def test_bandit_issue_body_templating():
    finding = {
        "filename": "brain/foo.py",
        "line_number": 42,
        "test_id": "B605",
        "issue_severity": "HIGH",
        "issue_text": "Starting a process with a shell",
        "code": "os.system(cmd)",
    }
    title, body = ca.bandit_issue_body(finding)
    assert "B605" in title
    assert "brain/foo.py:42" in body
    assert "HIGH" in body
    assert "os.system(cmd)" in body


# --- finding_dedup_key: stable identity that survives line drift/reformat ---


def test_dedup_key_stable_across_line_number_drift():
    # An unrelated edit above the flagged line shifts line_number but must
    # not change the finding's identity — otherwise every drift re-files.
    moved = dict(_B608_FINDING, line_number=99)
    assert ca.finding_dedup_key(_B608_FINDING) == ca.finding_dedup_key(moved)


def test_dedup_key_survives_whitespace_reformatting():
    reindented = dict(
        _B608_FINDING,
        code='    query = f"SELECT * FROM t WHERE id = {task_id}"  \n',
    )
    assert ca.finding_dedup_key(_B608_FINDING) == ca.finding_dedup_key(reindented)


def test_dedup_key_differs_for_different_code():
    other = dict(_B608_FINDING, code='query = f"SELECT * FROM t WHERE name = {name}"\n')
    assert ca.finding_dedup_key(_B608_FINDING) != ca.finding_dedup_key(other)


def test_dedup_key_differs_for_different_filename():
    other = dict(_B608_FINDING, filename="src/cofounder_agent/services/bar.py")
    assert ca.finding_dedup_key(_B608_FINDING) != ca.finding_dedup_key(other)


def test_dedup_key_differs_for_different_rule():
    other = dict(_B608_FINDING, test_id="B105")
    assert ca.finding_dedup_key(_B608_FINDING) != ca.finding_dedup_key(other)


def test_bandit_issue_body_embeds_dedup_key():
    _, body = ca.bandit_issue_body(_B608_FINDING)
    assert ca.finding_dedup_key(_B608_FINDING) in body


# --- _has_existing_issue: query gh (open OR closed) before filing ---


def test_has_existing_issue_true_when_gh_finds_a_match(monkeypatch):
    calls = []

    def fake_gh(*args):
        calls.append(args)
        return _FakeProc(0, '[{"number": 2650}]')

    monkeypatch.setattr(ca.c, "gh", fake_gh)
    assert ca._has_existing_issue("deadbeef12345678") is True
    (call,) = calls
    joined = " ".join(call)
    assert "deadbeef12345678" in joined
    assert "all" in call  # state:all — a finding closed as a false positive still dedups
    assert "security" in joined
    assert ca.REPO in call


def test_has_existing_issue_false_when_gh_finds_nothing(monkeypatch):
    monkeypatch.setattr(ca.c, "gh", lambda *_a: _FakeProc(0, "[]"))
    assert ca._has_existing_issue("cafef00ddeadbeef") is False


def test_has_existing_issue_failsafe_on_gh_error(monkeypatch):
    # gh itself failed (auth hiccup, rate limit, ...) — fail toward "don't file"
    monkeypatch.setattr(ca.c, "gh", lambda *_a: _FakeProc(1, ""))
    assert ca._has_existing_issue("x") is True


def test_has_existing_issue_failsafe_on_bad_json(monkeypatch):
    monkeypatch.setattr(ca.c, "gh", lambda *_a: _FakeProc(0, "not json"))
    assert ca._has_existing_issue("x") is True


# --- main(): wiring the dedup check into the finding-filing loop ---


def _fake_run_factory(findings: list[dict]):
    def fake_run(cmd, *, cwd=None):
        if "bandit" in cmd:
            return _FakeProc(0, json.dumps({"results": findings}))
        return _FakeProc(0, "")  # the ruff --fix call; output unused by main()

    return fake_run


def test_main_skips_finding_with_an_existing_issue_without_filing(monkeypatch):
    def boom_gh(*args):
        if args[:2] == ("issue", "create"):
            raise AssertionError("an already-tracked finding must not file a duplicate issue")
        return _FakeProc(0, '[{"number": 1}]')  # _has_existing_issue -> True

    monkeypatch.setattr(ca.c, "get_logger", lambda _name: logging.getLogger("test-codebase-audit"))
    monkeypatch.setattr(ca.c, "run", _fake_run_factory([_B608_FINDING]))
    monkeypatch.setattr(ca.c, "gh", boom_gh)
    monkeypatch.setattr(ca.c, "git", lambda *a, **k: _FakeProc(0, ""))

    assert ca.main() == 0


def test_main_files_new_finding_when_not_already_tracked(monkeypatch):
    filed = []

    def fake_gh(*args):
        if args[:2] == ("issue", "list"):
            return _FakeProc(0, "[]")  # nothing tracked yet
        if args[:2] == ("issue", "create"):
            filed.append(args)
        return _FakeProc(0, "")

    monkeypatch.setattr(ca.c, "get_logger", lambda _name: logging.getLogger("test-codebase-audit"))
    monkeypatch.setattr(ca.c, "run", _fake_run_factory([_B608_FINDING]))
    monkeypatch.setattr(ca.c, "gh", fake_gh)
    monkeypatch.setattr(ca.c, "git", lambda *a, **k: _FakeProc(0, ""))

    assert ca.main() == 0
    (call,) = filed
    assert "B608" in " ".join(call)
