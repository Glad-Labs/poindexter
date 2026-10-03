"""dsn_credential_literal_lint: no tracked file carries a literal DB password.

Thirty tracked files used to fall back to one hardcoded local DSN, and its
password was still live on the operator's database when that was found. These
tests pin what the lint flags, what it lets through as a placeholder or test
fixture, and that its report never prints the password it found.

Fixture DSNs are assembled at run time (``SCHEME``, ``DOLLAR_BRACE``): the
real-tree test at the bottom scans this file too, so a literal one here would
fail it.
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[5]
LINT = REPO_ROOT / "scripts" / "ci" / "dsn_credential_literal_lint.py"

SCHEME = "postgresql" + "://"  # joined at run time; see the module docstring
DOLLAR_BRACE = "$" + "{"
REALISTIC = "Corr3ct-Horse-Battery"  # shaped like a credential someone would pick


def _load():
    spec = importlib.util.spec_from_file_location("dsn_credential_literal_lint", LINT)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


lint = _load()


def _dsn(password: str, user: str = "poindexter") -> str:
    return f'DSN = "{SCHEME}{user}:{password}@localhost:5433/poindexter_brain"'


class TestFlags:
    def test_literal_password_in_a_dsn(self):
        problems = lint.scan_text("scripts/foo.py", _dsn(REALISTIC))
        assert len(problems) == 1
        assert problems[0].startswith("scripts/foo.py:1: ")

    def test_driver_suffixed_scheme(self):
        text = f'"{SCHEME.replace("://", "+asyncpg://")}u:{REALISTIC}@h/db"'
        assert lint.scan_text("app/db.py", text)

    def test_shell_default_password(self):
        text = f'PG_PASS="{DOLLAR_BRACE}LOCAL_POSTGRES_PASSWORD:-{REALISTIC}}}"'
        problems = lint.scan_text("scripts/verify.sh", text)
        assert len(problems) == 1
        assert "LOCAL_POSTGRES_PASSWORD" in problems[0]

    def test_dsn_split_across_adjacent_string_literals(self):
        """Two of the original thirty sites were written this way."""
        text = f'URL = (\n    f"{SCHEME}poindexter:{REALISTIC}"\n    f"@localhost:5433/db"\n)\n'
        problems = lint.scan_text("poindexter/cli/setup.py", text)
        assert len(problems) == 1
        assert problems[0].startswith("poindexter/cli/setup.py:2: ")

    def test_fixture_words_are_not_allowed_outside_tests(self):
        assert lint.scan_text("scripts/foo.py", _dsn("hunter2"))
        assert lint.scan_text("scripts/foo.py", _dsn("test"))

    def test_report_masks_the_password(self):
        """CI logs on the public mirror are public: never echo the match."""
        problems = lint.scan_text("scripts/foo.py", _dsn(REALISTIC))
        shell = f'{DOLLAR_BRACE}PGPASSWORD:-{REALISTIC}}}'
        problems += lint.scan_text("scripts/foo.sh", shell)
        assert len(problems) == 2
        for line in problems:
            assert REALISTIC not in line
            assert f"<{len(REALISTIC)} chars>" in line


class TestAllows:
    @pytest.mark.parametrize(
        "password",
        [
            "",
            "<password>",
            "<pw>",
            DOLLAR_BRACE + "POSTGRES_PASSWORD}",
            "{password}",
            "%(password)s",
            "***",
            "[Filtered]",
            "...",
            "your-password",
            "postgres",
            "pw",
            "x",
        ],
    )
    def test_placeholders_anywhere(self, password):
        assert lint.scan_text("docs/setup.md", _dsn(password)) == []

    @pytest.mark.parametrize(
        "path",
        [
            "src/cofounder_agent/tests/unit/test_x.py",
            "mcp-server/tests/test_y.py",
            "scripts/test_z.py",
            "src/cofounder_agent/tests/integration/conftest.py",
            "web/public-site/__tests__/a.test.js",
        ],
    )
    @pytest.mark.parametrize("password", ["test", "hunter2", "FAKEPW", "fakepw-e2e", "p%40ss%2Fword"])
    def test_fixture_values_in_test_files(self, path, password):
        assert lint.scan_text(path, _dsn(password)) == []

    def test_a_realistic_password_is_flagged_in_a_test_file_too(self):
        assert lint.scan_text("src/cofounder_agent/tests/unit/test_x.py", _dsn(REALISTIC))

    def test_required_and_alternate_expansions_are_not_defaults(self):
        text = (
            f'{DOLLAR_BRACE}LOCAL_POSTGRES_PASSWORD:?Run poindexter setup}} '
            f'{DOLLAR_BRACE}LOCAL_POSTGRES_PASSWORD:-}} '
            f'{DOLLAR_BRACE}PGPASSWORD:-{DOLLAR_BRACE}FALLBACK}}}}'
        )
        assert lint.scan_text("scripts/run.sh", text) == []


class TestScanFloor:
    def test_fails_outside_a_git_checkout(self, tmp_path):
        with pytest.raises(SystemExit):
            lint.tracked_files(tmp_path)


def test_real_tree_is_clean():
    """The repo itself carries no literal database password."""
    proc = subprocess.run(
        [sys.executable, str(LINT)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "clean (" in proc.stdout
