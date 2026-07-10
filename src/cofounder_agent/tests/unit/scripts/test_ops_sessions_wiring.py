from __future__ import annotations

from pathlib import Path


def _ps1() -> str:
    root = next(p for p in Path(__file__).resolve().parents if (p / "scripts" / "claude-sessions.ps1").exists())
    return (root / "scripts" / "claude-sessions.ps1").read_text(encoding="utf-8")


def test_frontier_sessions_disabled():
    text = _ps1()
    # issue-resolver and test-expansion must be present but Enabled = $false
    for name in ("issue-resolver", "test-expansion"):
        assert name in text
    assert text.count("Enabled = $false") >= 2


def test_rewired_sessions_point_at_ops_scripts():
    text = _ps1()
    # Path uses backslashes in the ps1 (ops_sessions\dependency_review.py); assert on
    # the filename token, which is separator-agnostic.
    for module in (
        "dependency_review.py", "codebase_audit.py", "doc_sync.py",
        "claude_md_sync.py", "triage_sweep.py", "alert_triage.py", "test_health.py",
    ):
        assert module in text
    assert "ops_sessions" in text


def test_run_session_substitutes_rundir_token():
    text = _ps1()
    # the harness must replace the {runDir} placeholder before Start-Process
    assert "{runDir}" in text                      # tokens present in definitions
    assert ".Replace('{runDir}'" in text           # substitution wired in Run-Session
