"""``_init_sentry`` must never block or break connector startup.

The connector runs as a systemd unit on the host; a missing SDK, an unreachable
database or an empty DSN each have to degrade to "run without Sentry" -- a
telemetry helper that can take the connector down would be worse than the gap
it closes. Runs in a fresh interpreter with a dead DATABASE_URL and no
bootstrap file so nothing on the developer machine can make it pass by
accident.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent

SCRIPT = r"""
import logging, sys
logging.basicConfig(level=logging.INFO)
sys.path.insert(0, %r)
import http_server
result = http_server._init_sentry()
print("RESULT", result)
"""


def test_init_sentry_degrades_without_a_reachable_database(tmp_path: Path) -> None:
    env = dict(os.environ)
    env.update({
        "HOME": str(tmp_path),  # no ~/.poindexter/bootstrap.toml
        "DATABASE_URL": "postgresql://nobody:nothing@127.0.0.1:1/nowhere",
        "POINDEXTER_SECRET_KEY": "unit-test-key",
    })
    started = time.monotonic()
    proc = subprocess.run(
        [sys.executable, "-c", SCRIPT % str(HERE)],
        capture_output=True, text=True, env=env, timeout=60, cwd=str(HERE),
    )
    elapsed = time.monotonic() - started
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "RESULT False" in proc.stdout
    assert "Traceback" not in proc.stderr
    assert elapsed < 30, f"startup must not hang on telemetry ({elapsed:.1f}s)"
