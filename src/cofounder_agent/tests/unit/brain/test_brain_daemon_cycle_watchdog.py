"""Unit tests — brain_daemon cycle watchdog + DB command_timeout.

2026-06-29 follow-up (sibling of the docker_port_forward alert-only fix). A
stuck ``await`` inside ``run_cycle`` — a DB query on a wedged Docker host-port
proxy — parked the daemon's only thread in ``epoll_wait`` for ~37 min. The
cycle's ``try/except`` catches *exceptions*, but a hang raises nothing, so the
heartbeat went stale, the dead-man's switch fired, and the brain never
recovered on its own.

These pin the two guards that make the cycle hang-proof:

* ``_create_brain_pool`` sets an asyncpg ``command_timeout`` so every query is
  bounded *client-side* — the timer fires even when the wedged proxy means the
  server never sees the query (the exact 2026-06-29 mechanism).
* ``_run_cycle_with_watchdog`` wraps the cycle in ``asyncio.wait_for``,
  converting a hang into a ``TimeoutError`` the main loop can account for and
  recover from (cancel the cycle, page if persistent, retry next cycle).
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# brain/ is a standalone package outside the cofounder_agent distro.
# Mirror the path-prelude pattern from test_brain_daemon_silent_failures.py.
_REPO_ROOT = next(
    p for p in Path(__file__).resolve().parents
    if (p / "pyproject.toml").exists() and (p / "src").exists()
)
_BRAIN_DIR = _REPO_ROOT / "brain"
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
if str(_BRAIN_DIR) not in sys.path:
    sys.path.insert(0, str(_BRAIN_DIR))

from brain import brain_daemon as bd  # noqa: E402


@pytest.mark.unit
@pytest.mark.asyncio
class TestCreateBrainPoolSetsCommandTimeout:
    async def test_pool_created_with_command_timeout(self, monkeypatch):
        captured: dict = {}

        async def fake_create_pool(dsn, **kwargs):
            captured["dsn"] = dsn
            captured["kwargs"] = kwargs
            return MagicMock(name="pool")

        monkeypatch.setattr(bd.asyncpg, "create_pool", fake_create_pool)

        pool = await bd._create_brain_pool("postgresql://x/y")

        assert pool is not None
        # The whole point: a per-query timeout so a wedged connection can't park
        # the daemon's only thread forever (2026-06-29).
        ct = captured["kwargs"].get("command_timeout")
        assert ct == bd.BRAIN_DB_COMMAND_TIMEOUT_SECONDS
        assert ct and ct > 0
        # Existing pool sizing preserved.
        assert captured["kwargs"].get("min_size") == 1
        assert captured["kwargs"].get("max_size") == 3
        assert captured["dsn"] == "postgresql://x/y"


@pytest.mark.unit
@pytest.mark.asyncio
class TestRunCycleWithWatchdog:
    async def test_hung_cycle_raises_timeout_not_hang(self):
        """A run_cycle that never returns must raise TimeoutError within the
        watchdog window — NOT park forever (the 2026-06-29 failure mode)."""
        cancelled = {"v": False}

        async def hung_cycle(_pool):
            try:
                await asyncio.sleep(60)  # never completes in the 0.05s window
            except asyncio.CancelledError:
                cancelled["v"] = True
                raise

        with pytest.raises(TimeoutError):
            await bd._run_cycle_with_watchdog(
                MagicMock(), cycle_timeout=0.05, run_cycle_fn=hung_cycle,
            )
        # The stuck cycle was actually torn down, not left detached — that's
        # what frees the daemon's thread to run the next cycle.
        assert cancelled["v"] is True

    async def test_normal_cycle_returns(self):
        ran = {"v": False}

        async def ok_cycle(_pool):
            ran["v"] = True

        await bd._run_cycle_with_watchdog(
            MagicMock(), cycle_timeout=5, run_cycle_fn=ok_cycle,
        )
        assert ran["v"] is True

    async def test_cycle_exception_propagates(self):
        """A real error inside the cycle must propagate unchanged so the main
        loop's existing ``except Exception`` accounting still fires — the
        watchdog adds a timeout, it does not swallow errors."""
        async def boom_cycle(_pool):
            raise ValueError("monitor exploded")

        with pytest.raises(ValueError, match="monitor exploded"):
            await bd._run_cycle_with_watchdog(
                MagicMock(), cycle_timeout=5, run_cycle_fn=boom_cycle,
            )

    async def test_defaults_to_module_run_cycle(self, monkeypatch):
        """With no fn injected it runs the module's ``run_cycle`` — so the
        production call site needs no extra argument."""
        called = {"v": False}

        async def fake_run_cycle(_pool):
            called["v"] = True

        monkeypatch.setattr(bd, "run_cycle", fake_run_cycle)
        await bd._run_cycle_with_watchdog(MagicMock(), cycle_timeout=5)
        assert called["v"] is True
