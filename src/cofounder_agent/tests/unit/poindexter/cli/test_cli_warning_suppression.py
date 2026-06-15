"""Regression: the poindexter CLI must not spam the langgraph / langchain-core
``allowed_objects`` PendingDeprecationWarning on every invocation.

langgraph's Postgres checkpointer constructs a module-level
``langchain_core.load.Reviver()`` at import time
(``langgraph/checkpoint/serde/jsonplus.py``), and langchain-core >=1.3.3 warns
when ``allowed_objects`` is left at its default. The call site lives inside the
dependency, so we cannot pass the argument ourselves — instead the CLI entry
(``poindexter/cli/__init__.py``) registers a scoped ``ignore`` filter.

The ordering is load-bearing: langchain-core's
``surface_langchain_deprecation_warnings()`` runs when langchain-core is first
imported and *prepends* a ``"default"`` filter for this category. Because
``warnings.filters`` is LIFO (last writer wins), our ``ignore`` must be
registered *after* that, so the suppression imports langchain-core first and
lands ahead of it in the stack.

This MUST run in a subprocess: warnings filters and import caching are
process-global, the triggering import already ran (and is cached) in the pytest
process, and pytest's own ``filterwarnings`` config silences
PendingDeprecationWarning in-process — none of which reflect the real CLI
terminal condition that the user actually sees.
"""

from __future__ import annotations

import os
import pathlib
import subprocess
import sys

import pytest

# <root>/src/cofounder_agent — anchor the subprocess to THIS worktree's
# poindexter package (the editable install may point at a different tree).
_SRC_ROOT = str(pathlib.Path(__file__).resolve().parents[4])

# Distinctive fragment of the warning message we expect to be suppressed.
_WARNING_NEEDLE = "allowed_objects"


def _run_cli_help() -> subprocess.CompletedProcess[str]:
    """Run ``python -m poindexter.cli --help`` in a fresh interpreter."""
    env = {
        **os.environ,
        "PYTHONPATH": os.pathsep.join(
            [_SRC_ROOT, os.environ.get("PYTHONPATH", "")]
        ),
    }
    return subprocess.run(
        [sys.executable, "-m", "poindexter.cli", "--help"],
        capture_output=True,
        text=True,
        timeout=120,
        env=env,
    )


@pytest.mark.unit
def test_cli_help_exits_cleanly():
    """Sanity: the CLI entry point still works (regression guard for the fix)."""
    proc = _run_cli_help()
    assert proc.returncode == 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    assert "poindexter" in proc.stdout


@pytest.mark.unit
def test_cli_help_does_not_emit_allowed_objects_warning():
    """The langchain-core ``allowed_objects`` PendingDeprecationWarning must not
    reach the operator's terminal on a plain CLI invocation."""
    proc = _run_cli_help()
    assert _WARNING_NEEDLE not in proc.stderr, (
        "CLI leaked the langchain-core allowed_objects PendingDeprecationWarning "
        f"to stderr:\n{proc.stderr}"
    )
