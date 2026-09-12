"""The brain container ships only ``poindexter/brain/``; a module-scope import
of worker code either crashes it or (behind a try/except) leaves a feature
silently inert in production. This lint pins that boundary."""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[5]
LINT = REPO_ROOT / "scripts" / "ci" / "brain_import_isolation_lint.py"


def _load():
    spec = importlib.util.spec_from_file_location("brain_import_isolation_lint", LINT)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.unit
def test_flags_module_scope_imports_even_inside_try():
    lint = _load()
    src = (
        "import asyncpg\n"
        "try:\n"
        "    from poindexter.services.settings_defaults import METADATA\n"
        "except Exception:\n"
        "    METADATA = {}\n"
        "import poindexter.utils.findings\n"
    )
    found = lint.scan_source(src, "x.py")
    assert [ln for ln, _ in found] == [3, 6]
    assert "poindexter.services.settings_defaults" in found[0][1]


@pytest.mark.unit
def test_function_scope_imports_and_brain_imports_are_allowed():
    lint = _load()
    src = (
        "from poindexter.brain import bootstrap\n"
        "import poindexter.brain.docker_utils\n"
        "def _resolve():\n"
        "    try:\n"
        "        from poindexter.services.integrations.operator_notify import notify_operator\n"
        "    except Exception:\n"
        "        return None\n"
        "    return notify_operator\n"
        "class K:\n"
        "    def m(self):\n"
        "        import poindexter.services.bootstrap\n"
    )
    assert lint.scan_source(src, "x.py") == []


@pytest.mark.unit
def test_escape_hatch_comment_is_honoured():
    lint = _load()
    src = "from poindexter.services.clock import now  # brain-import-ok: pure stdlib module\n"
    assert lint.scan_source(src, "x.py") == []


@pytest.mark.unit
def test_lint_passes_on_the_real_brain_tree():
    proc = subprocess.run([sys.executable, str(LINT)], capture_output=True, text=True, cwd=REPO_ROOT)
    assert proc.returncode == 0, proc.stdout + proc.stderr
