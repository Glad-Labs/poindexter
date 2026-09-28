"""Importing ``brain_daemon`` has no side effects; ``run()`` owns them (2026-09-28).

Until 2026-09-28 the module did three things at import. It created
``~/.content-pipeline``, handed ``logging.basicConfig`` a ``FileHandler`` on
``brain.log`` plus a stdout ``StreamHandler``, and called
``require_database_url()``, which pages the operator and exits 2 when no DSN
resolves. Every importer paid for that, not only the daemon. Under pytest,
``basicConfig`` ignores the handlers it is given because pytest's capture
handlers are already on the root logger, so the ``FileHandler`` was opened on
the developer's real ``~/.content-pipeline/brain.log`` and dropped unclosed:
``ResourceWarning: unclosed file`` in ``pytest tests/unit/brain -W default``.

Now ``run()``, the entry point ``python -m poindexter.brain.brain_daemon``
calls, configures logging and then resolves the DSN.

The import checks run in a FRESH interpreter. In this process the module is
already imported (much of the brain suite imports it at collection), so
importing it again here would prove nothing. The child gets an allowlisted
environment: ``HOME`` is a tmp dir, so no bootstrap file supplies a DSN and
anything written under ``~`` lands there, and there is no ``DATABASE_URL`` and
no Telegram/Discord credential, so even a regression that pages on import
cannot reach a real channel.
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

import poindexter
from poindexter.brain import brain_daemon as bd

# The src/cofounder_agent root of the tree under test. Taken from the imported
# package rather than a parents[N] walk, so the child imports exactly the code
# this process imports (a worktree, the CI checkout), never whatever an
# editable install elsewhere points at.
_SRC_ROOT = str(Path(poindexter.__file__).resolve().parent.parent)

_RESULT = "RESULT="

# Pinned literally, not read from bd.BRAIN_LOG_FORMAT: the point is that the
# daemon's log lines keep the shape they have always had.
_FORMAT = "%(asctime)s [%(levelname)s] %(message)s"


def _child_env(home: Path) -> dict[str, str]:
    """An allowlisted environment: no DSN, no notifier credentials, HOME in tmp."""
    return {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(home),
        "PYTHONPATH": _SRC_ROOT,
        # No .pyc writes either, so every write-mode open the import probe
        # records is one the imported code made.
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONIOENCODING": "utf-8",
    }


def _run_child(args: list[str], home: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=home,
        env=_child_env(home),
        timeout=120,
    )


def _child_result(code: str, home: Path) -> tuple[dict, subprocess.CompletedProcess[str]]:
    proc = _run_child(["-c", textwrap.dedent(code)], home)
    assert proc.returncode == 0, (
        f"child exited {proc.returncode}\nstdout:\n{proc.stdout[-3000:]}\n"
        f"stderr:\n{proc.stderr[-3000:]}"
    )
    line = next((ln for ln in proc.stdout.splitlines() if ln.startswith(_RESULT)), None)
    assert line is not None, f"child printed no result line:\n{proc.stdout[-3000:]}"
    return json.loads(line[len(_RESULT):]), proc


_IMPORT_PROBE = """
    import json, logging, os, sys

    WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_APPEND | os.O_TRUNC
    writes, mkdirs = [], []

    def audit(event, args):
        if event == "open":
            path, mode, flags = args
            if (isinstance(mode, str) and any(c in mode for c in "wax+")) or (
                isinstance(flags, int) and flags & WRITE_FLAGS
            ):
                writes.append(str(path))
        elif event == "os.mkdir":
            mkdirs.append(str(args[0]))

    root = logging.getLogger()
    handlers_before = list(root.handlers)
    level_before = root.level
    sys.addaudithook(audit)

    import poindexter.brain.brain_daemon  # the import under test

    print("RESULT=" + json.dumps({
        "writes": list(writes),
        "mkdirs": list(mkdirs),
        "handlers_added": [repr(h) for h in root.handlers if h not in handlers_before],
        "level_before": level_before,
        "level_after": root.level,
        "dsn_env": [
            k for k in ("DATABASE_URL", "LOCAL_DATABASE_URL", "POINDEXTER_MEMORY_DSN")
            if os.environ.get(k)
        ],
        "bootstrap_toml": os.path.exists(
            os.path.expanduser("~/.poindexter/bootstrap.toml")
        ),
    }))
"""


@pytest.mark.unit
def test_import_adds_no_root_handler_and_opens_no_file(tmp_path):
    result, _ = _child_result(_IMPORT_PROBE, tmp_path)

    assert result["handlers_added"] == [], "importing brain_daemon configured root logging"
    assert result["level_after"] == result["level_before"], "importing brain_daemon moved the root level"
    assert result["writes"] == [], f"importing brain_daemon opened files for writing: {result['writes']}"
    assert result["mkdirs"] == [], f"importing brain_daemon created directories: {result['mkdirs']}"
    assert not (tmp_path / ".content-pipeline").exists()


@pytest.mark.unit
def test_import_needs_no_database_url(tmp_path):
    """The import succeeded (``_child_result`` asserts rc 0) with nothing to resolve.

    Before 2026-09-28 this exact environment exited 2 during the import.
    """
    result, _ = _child_result(_IMPORT_PROBE, tmp_path)

    assert result["dsn_env"] == []
    assert result["bootstrap_toml"] is False


_CONFIGURE_PROBE = """
    import json, logging, sys
    import poindexter.brain.brain_daemon as bd

    path = bd.configure_logging()
    root = logging.getLogger()
    handlers = [
        {
            "type": type(h).__name__,
            "file": getattr(h, "baseFilename", None),
            "stdout": getattr(h, "stream", None) is sys.stdout,
            "format": h.formatter._fmt if h.formatter else None,
        }
        for h in root.handlers
    ]
    logging.getLogger("brain").info("[BRAIN] marker: configured")
    logging.getLogger("brain").debug("[BRAIN] marker: below INFO")
    for h in root.handlers:
        h.flush()
    print("RESULT=" + json.dumps({"path": path, "handlers": handlers, "level": root.level}))
"""


@pytest.mark.unit
def test_configure_logging_keeps_the_daemons_file_stream_and_format(tmp_path):
    result, proc = _child_result(_CONFIGURE_PROBE, tmp_path)
    log_file = tmp_path / ".content-pipeline" / "brain.log"

    assert result["path"] == str(log_file)
    assert result["level"] == logging.INFO
    assert result["handlers"] == [
        {"type": "FileHandler", "file": str(log_file), "stdout": False, "format": _FORMAT},
        {"type": "StreamHandler", "file": None, "stdout": True, "format": _FORMAT},
    ]
    marker = re.compile(
        r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3} \[INFO\] \[BRAIN\] marker: configured$",
        re.MULTILINE,
    )
    log_text = log_file.read_text(encoding="utf-8")
    assert marker.search(log_text), log_text
    assert marker.search(proc.stdout), proc.stdout
    assert "below INFO" not in log_text
    assert "below INFO" not in proc.stdout


@pytest.mark.unit
def test_brain_log_file_honours_app_log_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("APP_LOG_DIR", raising=False)
    assert bd.brain_log_file() == str(tmp_path / ".content-pipeline" / "brain.log")

    monkeypatch.setenv("APP_LOG_DIR", "custom-logs")
    assert bd.brain_log_file() == str(tmp_path / "custom-logs" / "brain.log")


@pytest.mark.unit
def test_daemon_without_a_database_url_logs_the_page_and_exits_2(tmp_path):
    """The container path end to end: ``python -m`` with no DSN anywhere.

    The operator page must land in brain.log and on stdout, which it only does
    if ``run()`` configured logging BEFORE resolving the DSN; that is the order
    the import-time code had.
    """
    proc = _run_child(["-m", "poindexter.brain.brain_daemon"], tmp_path)

    assert proc.returncode == 2, (
        f"expected the fail-loud exit 2, got {proc.returncode}\n"
        f"stdout:\n{proc.stdout[-3000:]}\nstderr:\n{proc.stderr[-3000:]}"
    )
    assert "Poindexter cannot start — no database URL configured" in proc.stderr
    log_text = (tmp_path / ".content-pipeline" / "brain.log").read_text(encoding="utf-8")
    for sink in (log_text, proc.stdout):
        assert "[CRITICAL] [operator_notifier]" in sink
        assert "Poindexter cannot start — no database URL configured" in sink
        assert "Source: brain_daemon" in sink


@pytest.mark.unit
def test_run_configures_logging_then_resolves_the_dsn_then_runs_main(monkeypatch):
    calls: list[object] = []

    def fake_configure_logging() -> str:
        calls.append("configure_logging")
        return "/unused/brain.log"

    def fake_require_database_url(*, source: str) -> str:
        calls.append(("require_database_url", source))
        return "postgresql://brain:secret@db:5432/brain"

    async def fake_main(db_url: str) -> None:
        calls.append(("main", db_url))

    monkeypatch.setattr(bd, "configure_logging", fake_configure_logging)
    monkeypatch.setattr(bd, "require_database_url", fake_require_database_url)
    monkeypatch.setattr(bd, "main", fake_main)

    bd.run()

    assert calls == [
        "configure_logging",
        ("require_database_url", "brain_daemon"),
        ("main", "postgresql://brain:secret@db:5432/brain"),
    ]


@pytest.mark.unit
def test_run_logs_an_interrupt_instead_of_raising(monkeypatch, caplog):
    async def interrupted(db_url: str) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(bd, "configure_logging", lambda: "/unused/brain.log")
    monkeypatch.setattr(bd, "require_database_url", lambda *, source: "postgresql://x@db/brain")
    monkeypatch.setattr(bd, "main", interrupted)

    with caplog.at_level(logging.INFO, logger="brain"):
        bd.run()

    assert "[BRAIN] Interrupted, exiting" in caplog.text
