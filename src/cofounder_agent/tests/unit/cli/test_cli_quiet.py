"""poindexter.cli._quiet: a new user's terminal shows the command, not plugin notes.

The first recorded how-to (2026-10-18) showed a plain install's
``poindexter tasks list`` opening with Redis, stripped-module and LiteLLM
notes, all logged while the CLI was still loading.
"""

from __future__ import annotations

import logging
import subprocess
import sys
from pathlib import Path

import pytest

from poindexter.cli import _quiet

_PKG_ROOT = Path(__file__).resolve().parents[3]


@pytest.mark.parametrize(
    ("args", "verbose"),
    [([], False), (["tasks", "list"], False), (["-v", "tasks"], True),
     (["--verbose", "tasks"], True), (["tasks", "-v"], False)],
)
def test_only_a_root_verbose_flag_counts(args, verbose):
    assert _quiet.root_verbose(args) is verbose


def test_it_acts_only_in_the_cli_process(monkeypatch):
    monkeypatch.setattr(logging.getLogger(_quiet.REGISTRY_LOGGER), "level", logging.NOTSET)
    env: dict[str, str] = {}
    assert _quiet.apply(["/usr/lib/python3/site-packages/pytest/__main__.py", "-q"], env) is False
    assert env == {} and logging.getLogger(_quiet.REGISTRY_LOGGER).level == logging.NOTSET


@pytest.mark.parametrize("prog", ["/home/u/.local/bin/poindexter", "-m"])
def test_the_cli_process_defaults_to_warning_and_hides_plugin_notes(monkeypatch, prog):
    registry = logging.getLogger(_quiet.REGISTRY_LOGGER)
    monkeypatch.setattr(registry, "level", logging.NOTSET)
    env: dict[str, str] = {}
    assert _quiet.apply([prog, "tasks", "list"], env) is True
    assert env == {"LOG_LEVEL": "WARNING"}
    assert registry.level == logging.ERROR


def test_verbose_keeps_everything_and_an_explicit_level_wins(monkeypatch):
    registry = logging.getLogger(_quiet.REGISTRY_LOGGER)
    monkeypatch.setattr(registry, "level", logging.NOTSET)
    env = {"LOG_LEVEL": "DEBUG"}
    _quiet.apply(["poindexter", "-v", "tasks"], env)
    assert env == {"LOG_LEVEL": "DEBUG"} and registry.level == logging.NOTSET


def _run_cli(tmp_path: Path, *args: str) -> str:
    """The real CLI in a fresh process, with redis and litellm made unimportable
    the way a plain `pip install` leaves them."""
    for pkg in ("redis", "litellm"):
        (tmp_path / pkg).mkdir()
        (tmp_path / pkg / "__init__.py").write_text('raise ImportError("not installed")\n')
    env = {"PYTHONPATH": f"{tmp_path}:{_PKG_ROOT}", "PATH": "/usr/bin:/bin", "HOME": str(tmp_path)}
    proc = subprocess.run(
        [sys.executable, "-m", "poindexter.cli", *args, "tasks", "--help"],
        capture_output=True, text=True, env=env, timeout=180, check=False,
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
    return proc.stdout + proc.stderr


@pytest.mark.skipif(sys.platform != "linux", reason="PYTHONPATH form")
def test_a_plain_install_prints_no_plugin_notes(tmp_path):
    out = _run_cli(tmp_path)
    assert "Redis SDK not installed" not in out
    assert "LiteLLMProvider" not in out
    assert " WARNING " not in out and " INFO " not in out


@pytest.mark.skipif(sys.platform != "linux", reason="PYTHONPATH form")
def test_verbose_still_shows_them(tmp_path):
    assert "Redis SDK not installed" in _run_cli(tmp_path, "-v")
