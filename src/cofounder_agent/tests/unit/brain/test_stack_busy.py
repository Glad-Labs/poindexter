"""brain/stack_busy.py — one definition of "a media render is in flight".

The host deploy sync and the brain's migration-drift auto-recover both
restart the worker. The deploy sync learned to wait for renders; the brain
did not, and restarted the worker through one (poindexter#1120). These tests
pin that the brain's signals are the deploy sync's signals, so the two can't
drift apart again.
"""

from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from poindexter.brain import stack_busy as sb


def _repo_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / "scripts" / "linux" / "deploy-checkout-sync.sh").is_file():
            return parent
    pytest.skip("deploy-checkout-sync.sh is not in this tree")


def _bash_function(script: str, name: str) -> str:
    match = re.search(rf"^{name}\(\) \{{\n(.*?)^\}}", script, re.S | re.M)
    assert match, f"{name}() is gone from deploy-checkout-sync.sh"
    return match.group(1)


def _squash(text: str) -> str:
    return re.sub(r"\s+", " ", text).replace("'", '"')


# What each bash function looks for, and the same fact in the brain's SQL.
_SIGNALS = {
    "gpu_work_running": ['locktype="advisory" AND granted'],
    "media_render_running": ["media_pipeline_dispatched_at", 'ma.type = "video"'],
    "media_live_activity_running": ['kind = "media"', "finished_at IS NULL", "updated_at >"],
}


def test_the_deploy_sync_and_the_brain_check_the_same_render_signals():
    script = (_repo_root() / "scripts" / "linux" / "deploy-checkout-sync.sh").read_text()
    busy = re.search(r"^stack_busy\(\) \{(.*?)\}", script, re.M)
    assert busy, "stack_busy() is gone from deploy-checkout-sync.sh"
    for name in _SIGNALS:
        assert name in busy.group(1), f"stack_busy() no longer calls {name}"
    brain_sql = _squash(sb.RENDER_SIGNALS_SQL).replace("locktype = ", "locktype=")
    for name, facts in _SIGNALS.items():
        bash = _squash(_bash_function(script, name))
        for fact in facts:
            assert fact in bash, f"{name}() no longer checks {fact!r}; update brain/stack_busy.py too"
            assert fact in brain_sql, f"brain/stack_busy.py no longer checks {fact!r} like {name}()"


def test_the_window_defaults_match_the_deploy_sync():
    script = (_repo_root() / "scripts" / "linux" / "deploy-checkout-sync.sh").read_text()
    assert f"SYNC_MEDIA_CLAIM_MAX_MIN:-{sb.DEFAULT_RENDER_CLAIM_MAX_MINUTES}" in script
    assert f"SYNC_MEDIA_LIVE_HEARTBEAT_MAX_MIN:-{sb.DEFAULT_RENDER_HEARTBEAT_MAX_MINUTES}" in script


@pytest.mark.asyncio
async def test_signals_are_read_in_one_round_trip_with_the_windows():
    pool = AsyncMock()
    pool.fetchrow = AsyncMock(return_value={"advisory_locks": 0, "render_claims": 2, "media_heartbeats": 1})
    got = await sb.render_signals(pool, claim_max_minutes=45, heartbeat_max_minutes=5)
    assert got == sb.RenderSignals(0, 2, 1) and got.busy
    assert pool.fetchrow.await_args.args[1:] == (45, 5)
    assert got.describe() == "2 media render claim(s) open, 1 live media render heartbeat(s)"


@pytest.mark.asyncio
@pytest.mark.parametrize("row", [None, {}, {"advisory_locks": None}])
async def test_nothing_to_read_is_not_busy(row):
    pool = AsyncMock()
    pool.fetchrow = AsyncMock(return_value=row)
    got = await sb.render_signals(pool)
    assert not got.busy and got.describe() == "no render in flight"


@pytest.mark.asyncio
async def test_a_failed_read_fails_open(caplog):
    pool = AsyncMock()
    pool.fetchrow = AsyncMock(side_effect=RuntimeError("db down"))
    with caplog.at_level("WARNING"):
        got = await sb.render_signals(pool)
    assert not got.busy
    assert "treating as not busy" in caplog.text
