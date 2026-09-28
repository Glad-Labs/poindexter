"""The brain container ships only ``poindexter/brain/``; an import of worker
code at any scope either crashes it or (behind a try/except) leaves a feature
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
    assert "at module scope" in found[0][1]


@pytest.mark.unit
def test_flags_lazy_function_and_class_body_imports():
    """A lazy import fails in the brain image just as a top-level one does,
    only later -- brain_daemon.main()'s build_container call did, on every boot."""
    lint = _load()
    src = (
        "async def main(pool):\n"
        "    try:\n"
        "        from poindexter.services.bootstrap import build_container\n"
        "    except ImportError:\n"
        "        return None\n"
        "class K:\n"
        "    import poindexter.plugins.registry\n"
        "    def m(self):\n"
        "        def inner():\n"
        "            import poindexter.modules.content\n"
    )
    found = lint.scan_source(src, "x.py")
    assert [ln for ln, _ in found] == [3, 7, 10]
    assert "in main()" in found[0][1]
    # A class body runs at import time, so it reports as module scope.
    assert "at module scope" in found[1][1]
    assert "in K.m.inner()" in found[2][1]


@pytest.mark.unit
def test_brain_imports_are_allowed_at_any_scope():
    lint = _load()
    src = (
        "from poindexter.brain import bootstrap\n"
        "import poindexter.brain.docker_utils\n"
        "def _resolve():\n"
        "    from poindexter.brain import brain_daemon\n"
        "    return brain_daemon\n"
    )
    assert lint.scan_source(src, "x.py") == []


@pytest.mark.unit
def test_tolerated_function_is_excused_and_only_that_function():
    lint = _load()
    src = (
        "def _resolve_notify_fn():\n"
        "    from poindexter.services.integrations.operator_notify import notify_operator\n"
        "    return notify_operator\n"
        "def _other():\n"
        "    import poindexter.services.bootstrap\n"
    )
    found = lint.scan_source(src, "x.py", frozenset({"_resolve_notify_fn"}))
    assert [ln for ln, _ in found] == [5]


@pytest.mark.unit
def test_escape_hatch_comment_is_honoured():
    lint = _load()
    src = "from poindexter.services.clock import now  # brain-import-ok: pure stdlib module\n"
    assert lint.scan_source(src, "x.py") == []
    src = "def f():\n    import poindexter.services.clock  # brain-import-ok: pure stdlib module\n"
    assert lint.scan_source(src, "x.py") == []


@pytest.mark.unit
def test_stale_tolerated_entry_fails(tmp_path):
    """The tolerated list can only shrink: an entry whose function stopped
    importing worker code is an offence until it is deleted."""
    lint = _load()
    brain = tmp_path / "brain"
    brain.mkdir()
    (brain / "live.py").write_text(
        "def keep():\n    import poindexter.services.bootstrap\n", encoding="utf-8"
    )
    (brain / "fixed.py").write_text("def gone():\n    return None\n", encoding="utf-8")
    sites = {
        ("live.py", "keep"): "still imports",
        ("fixed.py", "gone"): "no longer imports",
        ("renamed.py", "old"): "file is gone",
    }
    scanned, offences = lint.check_tree(brain, tmp_path, sites)
    assert scanned == 2
    assert len(offences) == 2, offences
    assert all("is stale" in o for o in offences)
    assert any("'fixed.py', 'gone'" in o for o in offences)
    assert any("'renamed.py', 'old'" in o for o in offences)


@pytest.mark.unit
def test_lint_passes_on_the_real_brain_tree():
    proc = subprocess.run([sys.executable, str(LINT)], capture_output=True, text=True, cwd=REPO_ROOT)
    assert proc.returncode == 0, proc.stdout + proc.stderr
