"""Regression: plugins.secrets must import without apscheduler (#1006).

The lean voice-agent image ships no ``apscheduler`` (only the worker runs the
PluginScheduler). But the voice agent reads its host-brain bearer token via
``site_config.get_secret`` -> ``from plugins.secrets import get_secret``, which
runs ``plugins/__init__.py`` -> ``from .scheduler import PluginScheduler``. Before
the import-gating fix, that pulled ``apscheduler`` and blew up with
``ModuleNotFoundError`` in the slim image, so the container couldn't read ANY
secret (the token fell back to empty -> host turns 401'd).

This test runs a subprocess with ``apscheduler`` import blocked and asserts:
  * ``from plugins.secrets import get_secret`` succeeds, and
  * constructing ``PluginScheduler`` still fails LOUD (not silently) — you only
    pay the apscheduler requirement when you actually run the scheduler.
"""

from __future__ import annotations

import pathlib
import subprocess
import sys
import textwrap

# <root>/src/cofounder_agent — anchor the subprocess to THIS tree's plugins
# (not whatever happens to be on the subprocess cwd), so the test exercises
# the code under test in both CI and a local worktree.
_SRC_ROOT = str(pathlib.Path(__file__).resolve().parents[3])


def test_plugins_secrets_imports_without_apscheduler():
    code = textwrap.dedent(
        f"""
        import sys
        sys.path.insert(0, {_SRC_ROOT!r})
        import builtins
        _real = builtins.__import__

        def _blocked(name, *a, **k):
            if name == "apscheduler" or name.startswith("apscheduler."):
                raise ImportError("No module named 'apscheduler'")
            return _real(name, *a, **k)

        builtins.__import__ = _blocked

        # The exact path the voice agent's get_secret takes — must succeed.
        from plugins.secrets import get_secret  # noqa: F401

        # The scheduler module still imports (symbols gated to None)...
        from plugins.scheduler import PluginScheduler

        # ...but constructing it without apscheduler must fail LOUD, not silently.
        try:
            PluginScheduler(pool=None)
        except ImportError as e:
            assert "apscheduler" in str(e), e
        else:
            raise SystemExit("PluginScheduler should fail loud without apscheduler")

        print("OK")
        """
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    assert "OK" in proc.stdout
