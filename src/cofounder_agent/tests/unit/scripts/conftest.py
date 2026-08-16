"""Shared isolation for the ops-session script tests in this directory.

These tests import ``scripts/ops_sessions/*`` modules and drive their
``main()``s. Any path reaching ``_common.get_logger`` writes a
realistic-looking ``<session>-<stamp>.log``, and without isolation it lands in
the OPERATOR'S real ``~/.poindexter/logs/claude-sessions/`` — where files mean
"a session ran". Observed 2026-08-15: pytest-deposited ``test-health-*.log`` /
``pin-check-*.log`` files (synthetic fixture scenarios inside) were mistaken
for real 23:35 session fires during an unrelated investigation.

``OPS_LOG_DIR`` is read per ``get_logger`` call, so one env var here covers
every session module regardless of how it imported the function (``import
_common`` and ``from _common import get_logger`` both honor it) — no per-test
``get_logger`` patching required. ``test_ops_common.py``'s isolation section
pins both this fixture's presence and the seam itself.
"""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolate_ops_session_logs(monkeypatch, tmp_path):
    monkeypatch.setenv("OPS_LOG_DIR", str(tmp_path / "ops-session-logs"))
