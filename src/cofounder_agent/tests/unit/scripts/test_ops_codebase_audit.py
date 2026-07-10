from __future__ import annotations

import sys
from pathlib import Path


def _ops_dir() -> Path:
    return next(
        p for p in Path(__file__).resolve().parents
        if (p / "scripts" / "ops_sessions").exists()
    ) / "scripts" / "ops_sessions"


sys.path.insert(0, str(_ops_dir()))
import codebase_audit as ca  # noqa: E402


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
