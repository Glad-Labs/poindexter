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

import os
import pathlib
import subprocess
import sys
import textwrap

import pytest

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


def _run(code: str) -> subprocess.CompletedProcess:
    """Run code in a subprocess with _SRC_ROOT pre-inserted and return the result."""
    return subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=60,
    )


def _blocked_import_header() -> str:
    """Return the header that blocks all apscheduler imports in a subprocess."""
    return textwrap.dedent(
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
        """
    )


def test_scheduler_constructor_error_message_mentions_remedy():
    """The ImportError from PluginScheduler without apscheduler must name the fix."""
    code = _blocked_import_header() + textwrap.dedent("""
        from plugins.scheduler import PluginScheduler
        try:
            PluginScheduler(pool=None)
        except ImportError as e:
            msg = str(e).lower()
            assert "apscheduler" in msg, e
            assert "worker" in msg, f"expected remedy mention in: {e}"
        else:
            raise SystemExit("should have raised ImportError")
        print("OK")
    """)
    proc = _run(code)
    assert proc.returncode == 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    assert "OK" in proc.stdout


def test_scheduler_constructor_error_is_chained():
    """ImportError from PluginScheduler must chain the original apscheduler ImportError."""
    code = _blocked_import_header() + textwrap.dedent("""
        from plugins.scheduler import PluginScheduler
        try:
            PluginScheduler(pool=None)
        except ImportError as e:
            assert e.__cause__ is not None, "expected 'raise ... from ...' chaining"
        else:
            raise SystemExit("should have raised ImportError")
        print("OK")
    """)
    proc = _run(code)
    assert proc.returncode == 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    assert "OK" in proc.stdout


def test_plugins_job_protocol_importable_without_apscheduler():
    """Job and JobResult from plugins.__init__ must survive in a lean image."""
    code = _blocked_import_header() + textwrap.dedent("""
        from plugins import Job, JobResult
        print("OK")
    """)
    proc = _run(code)
    assert proc.returncode == 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    assert "OK" in proc.stdout


# ── In-process tests: pure helpers that never need apscheduler ───────────────


def test_is_encrypted_true_for_enc_prefix():
    from plugins.secrets import is_encrypted
    assert is_encrypted("enc:v1:abc123") is True


def test_is_encrypted_false_for_plaintext():
    from plugins.secrets import is_encrypted
    assert is_encrypted("plaintext_value") is False


def test_is_encrypted_false_for_none():
    from plugins.secrets import is_encrypted
    assert is_encrypted(None) is False


def test_is_encrypted_false_for_empty_string():
    from plugins.secrets import is_encrypted
    assert is_encrypted("") is False


def test_key_missing_raises_secrets_error():
    """_key() must raise SecretsError when POINDEXTER_SECRET_KEY is absent."""
    from plugins.secrets import SecretsError, _key
    saved = os.environ.pop("POINDEXTER_SECRET_KEY", None)
    try:
        with pytest.raises(SecretsError, match="POINDEXTER_SECRET_KEY"):
            _key()
    finally:
        if saved is not None:
            os.environ["POINDEXTER_SECRET_KEY"] = saved


def test_stable_stagger_is_deterministic():
    from plugins.scheduler import _stable_stagger_seconds
    assert _stable_stagger_seconds("some_job") == _stable_stagger_seconds("some_job")


def test_stable_stagger_in_range():
    from plugins.scheduler import _FIRST_FIRE_STAGGER_S, _stable_stagger_seconds
    val = _stable_stagger_seconds("some_job")
    assert 0 <= val < _FIRST_FIRE_STAGGER_S


def test_stable_stagger_varies_across_names():
    """Different job names should (with overwhelming probability) produce different offsets."""
    from plugins.scheduler import _stable_stagger_seconds
    values = {_stable_stagger_seconds(f"job_{i}") for i in range(10)}
    assert len(values) > 1, "expected different stagger values across distinct job names"
