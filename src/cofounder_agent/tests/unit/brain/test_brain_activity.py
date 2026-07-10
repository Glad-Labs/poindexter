"""Unit tests — brain cycle liveness. The cycle watchdog brackets each brain
cycle with a best-effort 'brain' live_activity row so the console pulse shows
the brain running, without changing the watchdog's error-propagation contract.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# brain/ is a standalone package outside the cofounder_agent distro.
# Mirror the path-prelude pattern from test_brain_daemon_cycle_watchdog.py.
_REPO_ROOT = next(
    p
    for p in Path(__file__).resolve().parents
    if (p / "pyproject.toml").exists() and (p / "src").exists()
)
_BRAIN_DIR = _REPO_ROOT / "brain"
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
if str(_BRAIN_DIR) not in sys.path:
    sys.path.insert(0, str(_BRAIN_DIR))

from brain import brain_daemon as bd  # noqa: E402

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


async def test_cycle_brackets_activity(monkeypatch):
    calls = []

    async def fake_begin(pool):
        calls.append("begin")
        return 3

    async def fake_finish(pool, aid, status):
        calls.append(("finish", aid, status))

    monkeypatch.setattr(bd, "_brain_activity_begin", fake_begin)
    monkeypatch.setattr(bd, "_brain_activity_finish", fake_finish)

    async def ok_cycle(_pool):
        return None

    await bd._run_cycle_with_watchdog(MagicMock(), cycle_timeout=5, run_cycle_fn=ok_cycle)
    assert calls == ["begin", ("finish", 3, "ok")]


async def test_cycle_failure_marks_fail_and_propagates(monkeypatch):
    calls = []

    async def fake_begin(pool):
        return 5

    async def fake_finish(pool, aid, status):
        calls.append((aid, status))

    monkeypatch.setattr(bd, "_brain_activity_begin", fake_begin)
    monkeypatch.setattr(bd, "_brain_activity_finish", fake_finish)

    async def boom_cycle(_pool):
        raise ValueError("cycle broke")

    with pytest.raises(ValueError, match="cycle broke"):
        await bd._run_cycle_with_watchdog(
            MagicMock(), cycle_timeout=5, run_cycle_fn=boom_cycle
        )
    assert calls == [(5, "fail")]


async def test_activity_begin_swallows_and_returns_none():
    class _BoomPool:
        async def fetchval(self, *a):
            raise RuntimeError("db down")

    assert await bd._brain_activity_begin(_BoomPool()) is None
