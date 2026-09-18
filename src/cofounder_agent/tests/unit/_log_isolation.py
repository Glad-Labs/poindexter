"""Snapshot + restore the process-wide logging level around a block.

``logging``'s root level is process state. ``caplog`` sees a record only if the
emitting logger let it through, and a logger with no level of its own inherits
the root's — so the root level silently decides the outcome of every test that
asserts on a log line.

Production code moves it, correctly. ``poindexter.cli.pipeline._run`` calls
``_quiet_service_logging()``, forcing the root to WARNING so ``poindexter
pipeline resume`` doesn't flood the operator's terminal with service internals.
Right for a CLI, wrong for a test process: exercising any ``pipeline``
subcommand through ``CliRunner`` left every later test in that worker at
WARNING. That is what made
``test_topic_sources_igdb.py::test_skips_when_credentials_missing``
intermittent on 2026-09-18 — its "not configured" line is INFO, so the record
was never emitted and the assertion failed, while an isolated re-run passed.
Under xdist it turned on which worker drew the CLI tests first, which reads as
flake rather than as pollution.

Imported by ``tests/unit/conftest.py`` as an autouse fixture and exercised
directly by ``tests/unit/test_log_level_isolation.py``. It lives here rather
than in conftest so a test can import it without importing conftest a second
time under a different module name (see conftest's Layer 3.5 on sys.modules
husks).

Deliberately narrow — the root level and the global ``logging.disable`` floor,
nothing else. Per-logger levels are not snapshotted because production sets
exactly one (``telemetry.py`` quieting ``urllib3.connectionpool`` at import),
so walking ``loggerDict`` on every one of ~17k tests would buy nothing.
"""

from __future__ import annotations

import contextlib
import logging
from collections.abc import Iterator


@contextlib.contextmanager
def root_log_level_restored() -> Iterator[None]:
    """Put the root logger's level and the global disable floor back on exit.

    Restores unconditionally, including when the block raises — a test that
    fails after moving the level must not take its neighbours down with it.
    """
    root = logging.getLogger()
    level = root.level
    disable_floor = logging.root.manager.disable
    try:
        yield
    finally:
        root.setLevel(level)
        logging.disable(disable_floor)
