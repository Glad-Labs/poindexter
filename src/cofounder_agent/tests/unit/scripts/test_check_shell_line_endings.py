"""Unit tests for ``scripts/ci/check-shell-line-endings.py``.

Pins the contract for the lint that prevents the GlitchTip-flooding
``set: pipefail: invalid opt`` regression in ``DbBackupJob``. Two layers:

1. End-to-end contract test against the live repo — runs the script
   over the real tree and asserts it exits 0. Catches the case where
   a contributor lands a CRLF ``.sh`` file (the same way the original
   bug snuck in).
2. Unit tests against the underlying helpers via temp dirs — pins the
   detector, the skip list, and the offender-report format so the
   linter keeps catching real CRLF without false-positiving on
   skipped paths.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

# scripts/ci is a flat directory (no __init__.py); add it to sys.path so
# we can import the linter module directly. Same pattern as
# test_grafana_panels_lint.py.
REPO_ROOT = next(
    p for p in Path(__file__).resolve().parents
    if (p / "pyproject.toml").exists() and (p / "src").exists()
)
SCRIPTS_CI = REPO_ROOT / "scripts" / "ci"
LINTER_PATH = SCRIPTS_CI / "check-shell-line-endings.py"


def _load_linter():
    """Import the hyphenated script as a module by file path."""
    spec = importlib.util.spec_from_file_location(
        "check_shell_line_endings", LINTER_PATH,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def linter():
    return _load_linter()


# ---------------------------------------------------------------------------
# End-to-end: real repo must stay LF-only
# ---------------------------------------------------------------------------


class TestRepoContract:
    """The whole point of the linter — guarantee the live tree stays LF."""

    # The CRLF drift that prompted the original 2026-05-27 xfail (26 shell
    # scripts under scripts/ and skills/) has since been renormalized to LF,
    # so this contract gates honestly again: the linter must exit 0 over the
    # live tree. The stale ``@pytest.mark.xfail`` was removed in
    # Glad-Labs/glad-labs-stack#997 (it was xpassing). If CRLF drift returns,
    # this test now fails loudly instead of silently tolerating it.
    def test_repo_passes_lint(self) -> None:
        # Invoke as a subprocess so we exercise the actual entry point
        # (argv parsing, exit codes) — not just the importable helpers.
        result = subprocess.run(
            [sys.executable, str(LINTER_PATH), str(REPO_ROOT)],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, (
            f"Repo has CRLF in .sh/.bash files:\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )

    def test_db_backup_script_is_lf(self) -> None:
        """Specific guard for the GlitchTip regression source-file.

        If this test fails, ``set: pipefail: invalid opt`` is about to
        re-surface in production.
        """
        script = REPO_ROOT / "scripts" / "db-backup-local.sh"
        assert script.exists(), f"missing: {script}"
        data = script.read_bytes()
        cr_count = data.count(b"\r")
        assert cr_count == 0, (
            f"{script} has {cr_count} CR bytes — bash will fail at "
            f"`set -euo pipefail` and DbBackupJob will crash every 12h."
        )


# ---------------------------------------------------------------------------
# Unit: detector + skip list against synthetic trees
# ---------------------------------------------------------------------------


class TestDetector:
    def test_lf_file_passes(self, tmp_path: Path, linter) -> None:
        (tmp_path / "ok.sh").write_bytes(b"#!/bin/bash\necho hi\n")
        rc = linter.main(["check-shell-line-endings.py", str(tmp_path)])
        assert rc == 0

    def test_crlf_file_fails(self, tmp_path: Path, linter, capsys) -> None:
        (tmp_path / "bad.sh").write_bytes(b"#!/bin/bash\r\nset -o pipefail\r\n")
        rc = linter.main(["check-shell-line-endings.py", str(tmp_path)])
        assert rc == 1
        err = capsys.readouterr().err
        assert "bad.sh" in err
        assert "CRLF" in err
        # The remediation hint must mention --renormalize so a contributor
        # who has never hit this can copy-paste the fix.
        assert "renormalize" in err

    def test_bash_extension_also_caught(self, tmp_path: Path, linter) -> None:
        (tmp_path / "lib.bash").write_bytes(b"#!/usr/bin/env bash\r\n")
        rc = linter.main(["check-shell-line-endings.py", str(tmp_path)])
        assert rc == 1

    def test_non_shell_files_ignored(self, tmp_path: Path, linter) -> None:
        # A CRLF .py / .md / .ps1 file is out of scope — line-ending policy
        # for those is enforced elsewhere (gitleaks parity for .py, etc.).
        (tmp_path / "notes.md").write_bytes(b"# Title\r\n")
        (tmp_path / "tool.py").write_bytes(b"print('hi')\r\n")
        (tmp_path / "run.ps1").write_bytes(b"Write-Host hi\r\n")
        rc = linter.main(["check-shell-line-endings.py", str(tmp_path)])
        assert rc == 0

    def test_skip_dirs_honoured(self, tmp_path: Path, linter) -> None:
        # A CRLF .sh under .venv/ or node_modules/ must not fail the lint —
        # those are third-party / generated trees the lint deliberately
        # skips (matches the gitleaks / trivy skip lists).
        for skip in (".venv", "node_modules", "__pycache__", ".claude"):
            (tmp_path / skip).mkdir(parents=True, exist_ok=True)
            (tmp_path / skip / "vendored.sh").write_bytes(b"#!/bin/sh\r\n")
        rc = linter.main(["check-shell-line-endings.py", str(tmp_path)])
        assert rc == 0

    def test_missing_root_returns_2(self, tmp_path: Path, linter) -> None:
        rc = linter.main([
            "check-shell-line-endings.py",
            str(tmp_path / "does-not-exist"),
        ])
        assert rc == 2

    def test_nested_crlf_caught(self, tmp_path: Path, linter, capsys) -> None:
        nested = tmp_path / "src" / "scripts" / "deep"
        nested.mkdir(parents=True)
        (nested / "deeply-nested.sh").write_bytes(b"#!/bin/bash\r\necho hi\r\n")
        rc = linter.main(["check-shell-line-endings.py", str(tmp_path)])
        assert rc == 1
        err = capsys.readouterr().err
        assert "deeply-nested.sh" in err
