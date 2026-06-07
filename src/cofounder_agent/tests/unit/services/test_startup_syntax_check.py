"""Tests for StartupManager syntax-checking helpers.

Covers:
  _scan_syntax_errors(dir) — the pure walk-and-compile helper
  _check_module_syntax()   — the orchestrator that exits on errors

The 2026-06-07 crash-loop root cause: content_module.py had unresolved
git merge conflict markers (<<<<<<< HEAD) which caused a SyntaxError.
The worker exited with code 0 on every restart attempt, making the root
cause non-obvious. This check catches it before uvicorn even starts.

Uses the built-in compile() — no .pyc writes — so it works even when
the container filesystem is read-only for __pycache__.
"""

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parents[3]))
from utils.startup_manager import StartupManager


# ---------------------------------------------------------------------------
# _scan_syntax_errors — pure walk + compile, testable without mocking
# ---------------------------------------------------------------------------

class TestScanSyntaxErrors:
    def test_returns_empty_for_clean_files(self, tmp_path):
        (tmp_path / "a.py").write_text("x = 1\n")
        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / "b.py").write_text("def foo(): return 42\n")

        errors = StartupManager._scan_syntax_errors(tmp_path)
        assert errors == []

    def test_detects_conflict_markers(self, tmp_path):
        bad = tmp_path / "conflicted.py"
        bad.write_text("x = 1\n<<<<<<< HEAD\ndef foo(): pass\n=======\ndef foo(): return 1\n>>>>>>>\n")

        errors = StartupManager._scan_syntax_errors(tmp_path)

        assert len(errors) == 1
        assert "conflicted.py" in errors[0][0]

    def test_detects_plain_syntax_error(self, tmp_path):
        (tmp_path / "broken.py").write_text("def foo(\n")

        errors = StartupManager._scan_syntax_errors(tmp_path)

        assert len(errors) == 1

    def test_skips_pycache_directory(self, tmp_path):
        cache = tmp_path / "__pycache__"
        cache.mkdir()
        (cache / "stale.py").write_text("<<<<<<< HEAD\n")
        (tmp_path / "clean.py").write_text("x = 1\n")

        errors = StartupManager._scan_syntax_errors(tmp_path)

        assert errors == [], "__pycache__ files must be ignored"

    def test_reports_all_errors_in_multi_file_tree(self, tmp_path):
        (tmp_path / "ok.py").write_text("x = 1\n")
        (tmp_path / "bad1.py").write_text("<<<<<<< HEAD\n")
        sub = tmp_path / "pkg"
        sub.mkdir()
        (sub / "bad2.py").write_text("def foo(\n")

        errors = StartupManager._scan_syntax_errors(tmp_path)

        assert len(errors) == 2
        paths = {e[0] for e in errors}
        assert any("bad1.py" in p for p in paths)
        assert any("bad2.py" in p for p in paths)

    def test_does_not_write_pyc_files(self, tmp_path):
        (tmp_path / "ok.py").write_text("x = 1\n")
        StartupManager._scan_syntax_errors(tmp_path)
        assert not any(tmp_path.rglob("*.pyc")), "Must not write .pyc files (read-only container filesystem)"


# ---------------------------------------------------------------------------
# _check_module_syntax — integration: exits 1 on errors, passes on clean tree
# ---------------------------------------------------------------------------

class TestCheckModuleSyntax:
    def _make_manager(self):
        return StartupManager(site_config=None)

    def test_exits_1_when_conflict_markers_present(self, tmp_path):
        (tmp_path / "conflicted.py").write_text("<<<<<<< HEAD\n")

        mgr = self._make_manager()
        with patch.object(StartupManager, "_scan_syntax_errors", return_value=[(str(tmp_path / "conflicted.py"), "SyntaxError: invalid syntax")]):
            with patch("utils.startup_manager.Path") as MockPath:
                mock_dir = MockPath.return_value.parent.parent.__truediv__.return_value
                mock_dir.is_dir.return_value = True

                with pytest.raises(SystemExit) as exc_info:
                    mgr._check_module_syntax()

        assert exc_info.value.code == 1

    def test_does_not_exit_when_modules_dir_missing(self, tmp_path):
        mgr = self._make_manager()
        with patch("utils.startup_manager.Path") as MockPath:
            mock_dir = MockPath.return_value.parent.parent.__truediv__.return_value
            mock_dir.is_dir.return_value = False

            # Should return without raising or exiting
            mgr._check_module_syntax()

    def test_does_not_exit_when_all_clean(self, tmp_path):
        mgr = self._make_manager()
        with patch.object(StartupManager, "_scan_syntax_errors", return_value=[]):
            with patch("utils.startup_manager.Path") as MockPath:
                mock_dir = MockPath.return_value.parent.parent.__truediv__.return_value
                mock_dir.is_dir.return_value = True
                mock_dir.rglob.return_value = []

                # Should return without raising or exiting
                mgr._check_module_syntax()

    def test_logs_critical_with_offending_filename(self, tmp_path, caplog):
        offender = str(tmp_path / "oops.py")
        mgr = self._make_manager()
        with patch.object(StartupManager, "_scan_syntax_errors", return_value=[(offender, "SyntaxError: invalid syntax")]):
            with patch("utils.startup_manager.Path") as MockPath:
                mock_dir = MockPath.return_value.parent.parent.__truediv__.return_value
                mock_dir.is_dir.return_value = True

                with caplog.at_level("CRITICAL"):
                    with pytest.raises(SystemExit):
                        mgr._check_module_syntax()

        assert any("oops.py" in r.message for r in caplog.records), (
            "Offending filename must appear in a CRITICAL log message"
        )
