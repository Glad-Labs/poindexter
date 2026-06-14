"""Unit tests for ``scripts/ci/check-powershell-encoding.py``.

Pins the contract for the lint that prevents the Windows-PowerShell-5.1
BOM-less non-ASCII parse failure -- the em-dash -> smart-quote trap that
broke ``scripts/deploy-worker.ps1`` (running it under ``powershell.exe``
died with a phantom ``Unexpected token '}'`` because 5.1 decodes BOM-less
``.ps1`` files as ANSI, turning an em-dash's UTF-8 bytes into a
string-delimiter smart-quote). Two layers:

1. End-to-end contract test against the live repo -- runs the linter
   over the real tree and asserts it exits 0. Catches a contributor
   landing a ``.ps1`` with an em-dash (or any non-ASCII char) and no BOM.
2. Unit tests against the detector via temp dirs -- pins the BOM
   allowance, the skip list, the suffix scope, and the offender-report
   format so the linter keeps catching the real trap without
   false-positiving on ASCII / BOM'd / out-of-scope files.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

# scripts/ci is a flat directory (no __init__.py); import the linter by
# file path. Same pattern as test_check_shell_line_endings.py. The
# sentinel walk avoids the brittle ``parents[N]`` index that breaks in
# containers and worktrees.
REPO_ROOT = next(
    p for p in Path(__file__).resolve().parents
    if (p / "pyproject.toml").exists() and (p / "src").exists()
)
SCRIPTS_CI = REPO_ROOT / "scripts" / "ci"
LINTER_PATH = SCRIPTS_CI / "check-powershell-encoding.py"

# An em-dash (U+2014) encoded as UTF-8 -- the exact byte sequence Windows
# PowerShell 5.1 mis-decodes into a string-delimiter smart-quote.
_EM_DASH_UTF8 = b"\xe2\x80\x94"
_UTF8_BOM = b"\xef\xbb\xbf"


def _load_linter():
    """Import the hyphenated script as a module by file path."""
    spec = importlib.util.spec_from_file_location(
        "check_powershell_encoding", LINTER_PATH,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def linter():
    return _load_linter()


# ---------------------------------------------------------------------------
# End-to-end: the real repo must stay 5.1-safe
# ---------------------------------------------------------------------------


class TestRepoContract:
    """The whole point of the linter -- keep every tracked .ps1 parseable
    under Windows PowerShell 5.1."""

    def test_repo_passes_lint(self) -> None:
        # Invoke as a subprocess so we exercise the actual entry point
        # (argv parsing, exit codes) -- not just the importable helpers.
        result = subprocess.run(
            [sys.executable, str(LINTER_PATH), str(REPO_ROOT)],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, (
            "PowerShell scripts carry non-ASCII bytes without a BOM:\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )

    def test_deploy_worker_is_5_1_safe(self) -> None:
        """Specific guard for the file that surfaced the bug.

        If this fails, ``Unexpected token '}'`` is about to re-surface for
        anyone deploying via Windows PowerShell 5.1.
        """
        script = REPO_ROOT / "scripts" / "deploy-worker.ps1"
        assert script.exists(), f"missing: {script}"
        data = script.read_bytes()
        non_ascii = [b for b in data if b > 0x7F]
        has_bom = data.startswith(_UTF8_BOM)
        assert not non_ascii or has_bom, (
            f"{script} has {len(non_ascii)} non-ASCII byte(s) and no BOM -- "
            "it will fail to parse under Windows PowerShell 5.1."
        )


# ---------------------------------------------------------------------------
# Unit: detector + skip list + BOM allowance against synthetic trees
# ---------------------------------------------------------------------------


class TestDetector:
    def test_ascii_file_passes(self, tmp_path: Path, linter) -> None:
        (tmp_path / "ok.ps1").write_bytes(b"Write-Host 'hi'\r\n")
        rc = linter.main(["check-powershell-encoding.py", str(tmp_path)])
        assert rc == 0

    def test_non_ascii_no_bom_fails(self, tmp_path: Path, linter, capsys) -> None:
        (tmp_path / "bad.ps1").write_bytes(
            b"Write-Host 'a " + _EM_DASH_UTF8 + b" b'\r\n"
        )
        rc = linter.main(["check-powershell-encoding.py", str(tmp_path)])
        assert rc == 1
        err = capsys.readouterr().err
        assert "bad.ps1" in err
        # The report must name the offending codepoint and the BOM escape
        # hatch so a contributor can fix it without reading the source.
        assert "U+2014" in err
        assert "BOM" in err

    def test_utf8_bom_file_passes(self, tmp_path: Path, linter) -> None:
        # Non-ASCII IS allowed when the file carries a UTF-8 BOM -- 5.1
        # honours the BOM and decodes as UTF-8.
        (tmp_path / "bom.ps1").write_bytes(
            _UTF8_BOM + b"Write-Host 'a " + _EM_DASH_UTF8 + b" b'\r\n"
        )
        rc = linter.main(["check-powershell-encoding.py", str(tmp_path)])
        assert rc == 0

    def test_psm1_and_psd1_also_caught(self, tmp_path: Path, linter) -> None:
        (tmp_path / "mod.psm1").write_bytes(b"# " + _EM_DASH_UTF8 + b"\r\n")
        assert linter.main(
            ["check-powershell-encoding.py", str(tmp_path)]
        ) == 1
        (tmp_path / "mod.psm1").unlink()
        (tmp_path / "man.psd1").write_bytes(b"# " + _EM_DASH_UTF8 + b"\r\n")
        assert linter.main(
            ["check-powershell-encoding.py", str(tmp_path)]
        ) == 1

    def test_non_powershell_files_ignored(self, tmp_path: Path, linter) -> None:
        # Non-ASCII in .py / .md / .sh is out of scope -- those are read as
        # UTF-8 by their consumers; only PowerShell 5.1 defaults to ANSI.
        (tmp_path / "notes.md").write_bytes(b"# " + _EM_DASH_UTF8 + b"\n")
        (tmp_path / "tool.py").write_bytes(b"x = '" + _EM_DASH_UTF8 + b"'\n")
        (tmp_path / "run.sh").write_bytes(b"# " + _EM_DASH_UTF8 + b"\n")
        rc = linter.main(["check-powershell-encoding.py", str(tmp_path)])
        assert rc == 0

    def test_skip_dirs_honoured(self, tmp_path: Path, linter) -> None:
        # A non-ASCII .ps1 under .venv/ or node_modules/ must not fail the
        # lint -- those are third-party / generated trees CI deliberately
        # skips (matches the shell linter / gitleaks skip lists).
        for skip in (".venv", "node_modules", "__pycache__", ".claude"):
            (tmp_path / skip).mkdir(parents=True, exist_ok=True)
            (tmp_path / skip / "vendored.ps1").write_bytes(
                b"# " + _EM_DASH_UTF8 + b"\r\n"
            )
        rc = linter.main(["check-powershell-encoding.py", str(tmp_path)])
        assert rc == 0

    def test_missing_root_returns_2(self, tmp_path: Path, linter) -> None:
        rc = linter.main([
            "check-powershell-encoding.py",
            str(tmp_path / "does-not-exist"),
        ])
        assert rc == 2

    def test_nested_offender_caught(self, tmp_path: Path, linter, capsys) -> None:
        nested = tmp_path / "scripts" / "deep"
        nested.mkdir(parents=True)
        (nested / "deeply-nested.ps1").write_bytes(
            b"Write-Host '" + _EM_DASH_UTF8 + b"'\r\n"
        )
        rc = linter.main(["check-powershell-encoding.py", str(tmp_path)])
        assert rc == 1
        assert "deeply-nested.ps1" in capsys.readouterr().err
