"""Unit tests for brain/comfyui_ram_watch.py.

The ComfyUI RAM watch is the host-RAM twin of the #999 VRAM-ghost lever:
the render sidecar's main python accumulates RSS+swap across renders
(28.6 GB observed 2026-08-26) and only a process exit returns it, so the
brain ``docker restart``s poindexter-comfyui — but ONLY with a verifiably
idle ``/queue``, re-checked immediately before the restart
(poindexter#3094 posture: a running renderer must never be bounced by a
reclaim lever, and an unknowable queue counts as busy).

States covered: disabled short-circuit, sidecar-unreachable no-op, busy
no-op, below-watermark no-op, the recycle (finding + cooldown stamp), the
pre-restart re-check deferring on busy/unreachable, cooldown suppression
+ expiry, restart failure (warn finding, not silent), stats-read failure,
and the /proc/<pid>/status parser.

All external I/O (the queue read, the docker exec memory read, ``docker
restart``, the asyncpg pool) is injected/mocked — nothing really
restarts.
"""
from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

# pythonpath in pyproject.toml includes "../.." so the brain package resolves
# the same way the postiz_queue_watch tests import it.
from brain import comfyui_ram_watch as cw


def _make_pool(*, setting_values=None, executed=None):
    """Pool mock serving app_settings reads and recording executes."""
    pool = MagicMock()
    settings = {
        cw.ENABLED_KEY: "true",
        cw.WATERMARK_GB_KEY: "20",
        cw.COOLDOWN_MINUTES_KEY: "60",
        cw.SERVER_URL_KEY: "http://comfyui:8188",
        **(setting_values or {}),
    }

    async def _fetchval(query, *args):
        if "app_settings" in query and args:
            return settings.get(args[0])
        return None

    async def _execute(query, *args):
        if executed is not None:
            executed.append((query, args))

    pool.fetchval = AsyncMock(side_effect=_fetchval)
    pool.execute = AsyncMock(side_effect=_execute)
    return pool


def _findings(executed, kind):
    """Finding rows of the given kind captured by the execute recorder."""
    rows = []
    for query, args in executed:
        if "audit_log" not in query or "'finding'" not in query:
            continue
        details = json.loads(args[1])
        if details.get("kind") == kind:
            rows.append((args, details))
    return rows


@pytest.fixture(autouse=True)
def _reset():
    cw._reset_recycle_state()
    yield
    cw._reset_recycle_state()


def test_disabled_short_circuits():
    pool = _make_pool(setting_values={cw.ENABLED_KEY: "false"})
    summary = asyncio.run(cw.run_comfyui_ram_watch_probe(pool))
    assert summary["status"] == "disabled"


def test_sidecar_unreachable_is_a_noop():
    """Profile not up / sidecar down => nothing to recycle, never restart.

    Also the deliberate hung-sidecar posture: a queue we cannot read is a
    queue we cannot prove idle, so this probe never touches the container.
    """
    pool = _make_pool()
    mem = MagicMock(side_effect=AssertionError("must not read memory"))
    restart = MagicMock(side_effect=AssertionError("must not restart"))
    summary = asyncio.run(
        cw.run_comfyui_ram_watch_probe(
            pool,
            queue_fn=AsyncMock(return_value=None),
            mem_fn=mem,
            restart_fn=restart,
        )
    )
    assert summary["ok"] is True
    assert summary["status"] == "unreachable"


def test_busy_queue_never_restarts():
    """#3094 posture: a running/pending render blocks the recycle outright."""
    pool = _make_pool()
    restart = MagicMock(side_effect=AssertionError("must not restart"))
    summary = asyncio.run(
        cw.run_comfyui_ram_watch_probe(
            pool,
            queue_fn=AsyncMock(return_value=True),
            mem_fn=MagicMock(side_effect=AssertionError("must not read memory")),
            restart_fn=restart,
        )
    )
    assert summary["ok"] is True
    assert summary["status"] == "busy"


def test_below_watermark_no_restart():
    pool = _make_pool()
    restart = MagicMock(side_effect=AssertionError("must not restart"))
    summary = asyncio.run(
        cw.run_comfyui_ram_watch_probe(
            pool,
            queue_fn=AsyncMock(return_value=False),
            mem_fn=MagicMock(return_value=(10.0, 5.0)),
            restart_fn=restart,
        )
    )
    assert summary["status"] == "below_watermark"
    assert summary["footprint_gb"] == 15.0


def test_above_watermark_recycles_and_emits_info_finding():
    executed = []
    pool = _make_pool(executed=executed)
    restart = MagicMock(return_value=(True, "Restarted poindexter-comfyui"))
    queue = AsyncMock(return_value=False)
    summary = asyncio.run(
        cw.run_comfyui_ram_watch_probe(
            pool,
            queue_fn=queue,
            mem_fn=MagicMock(return_value=(13.9, 14.8)),
            restart_fn=restart,
        )
    )
    assert summary["ok"] is True
    assert summary["status"] == "recycled"
    assert summary["footprint_gb"] == pytest.approx(28.7)
    restart.assert_called_once_with("poindexter-comfyui")
    # Qualify + immediate pre-restart re-check (#3094) = two queue reads.
    assert queue.await_count == 2

    rows = _findings(executed, "comfyui_ram_recycled")
    assert rows, "expected a comfyui_ram_recycled finding row"
    args, details = rows[0]
    assert args[0] == "brain.comfyui_ram_watch"  # source
    assert args[2] == "info"  # severity — board-visible, never routed
    assert details["dedup_key"] == "comfyui_ram_recycled:poindexter-comfyui"
    assert details["extra"]["rss_gb"] == 13.9
    assert details["extra"]["swap_gb"] == 14.8


def test_recheck_busy_defers_the_restart():
    """A render enqueued between qualify and restart defers the recycle —
    the immediate pre-restart re-check is the #3094 posture's second half."""
    pool = _make_pool()
    restart = MagicMock(side_effect=AssertionError("must not restart"))
    summary = asyncio.run(
        cw.run_comfyui_ram_watch_probe(
            pool,
            queue_fn=AsyncMock(side_effect=[False, True]),
            mem_fn=MagicMock(return_value=(20.0, 9.0)),
            restart_fn=restart,
        )
    )
    assert summary["ok"] is True
    assert summary["status"] == "busy_at_recheck"


def test_recheck_unreachable_defers_the_restart():
    """Queue unreadable at the re-check counts as busy, not as idle."""
    pool = _make_pool()
    restart = MagicMock(side_effect=AssertionError("must not restart"))
    summary = asyncio.run(
        cw.run_comfyui_ram_watch_probe(
            pool,
            queue_fn=AsyncMock(side_effect=[False, None]),
            mem_fn=MagicMock(return_value=(20.0, 9.0)),
            restart_fn=restart,
        )
    )
    assert summary["status"] == "busy_at_recheck"


def test_cooldown_suppresses_then_expires():
    """A recycle stamps the cooldown; the next over-watermark cycle inside
    the window no-ops, and one past the window recycles again."""
    pool = _make_pool()
    restart = MagicMock(return_value=(True, "Restarted poindexter-comfyui"))
    clock = {"now": 1000.0}

    def _run():
        return asyncio.run(
            cw.run_comfyui_ram_watch_probe(
                pool,
                queue_fn=AsyncMock(return_value=False),
                mem_fn=MagicMock(return_value=(25.0, 5.0)),
                restart_fn=restart,
                now_fn=lambda: clock["now"],
            )
        )

    assert _run()["status"] == "recycled"
    restart.assert_called_once()

    clock["now"] += 10 * 60  # 10 minutes < 60m cooldown
    summary = _run()
    assert summary["status"] == "cooldown"
    restart.assert_called_once()  # still exactly one restart

    clock["now"] += 55 * 60  # now 65 minutes since the recycle
    assert _run()["status"] == "recycled"
    assert restart.call_count == 2


def test_zero_cooldown_disables_suppression():
    pool = _make_pool(setting_values={cw.COOLDOWN_MINUTES_KEY: "0"})
    restart = MagicMock(return_value=(True, "Restarted poindexter-comfyui"))

    def _run():
        return asyncio.run(
            cw.run_comfyui_ram_watch_probe(
                pool,
                queue_fn=AsyncMock(return_value=False),
                mem_fn=MagicMock(return_value=(25.0, 5.0)),
                restart_fn=restart,
            )
        )

    assert _run()["status"] == "recycled"
    assert _run()["status"] == "recycled"
    assert restart.call_count == 2


def test_restart_failure_emits_warn_finding():
    """A broken lever must not fail silent: warn-severity finding routes
    per the findings.<kind> policy instead of vanishing."""
    executed = []
    pool = _make_pool(executed=executed)
    restart = MagicMock(return_value=(False, "docker CLI not on PATH"))
    summary = asyncio.run(
        cw.run_comfyui_ram_watch_probe(
            pool,
            queue_fn=AsyncMock(return_value=False),
            mem_fn=MagicMock(return_value=(20.0, 9.0)),
            restart_fn=restart,
        )
    )
    assert summary["ok"] is False
    assert summary["status"] == "restart_failed"

    rows = _findings(executed, "comfyui_ram_recycle_failed")
    assert rows, "expected a comfyui_ram_recycle_failed finding row"
    args, details = rows[0]
    assert args[2] == "warn"
    assert "docker CLI not on PATH" in details["extra"]["error"]
    # A failed restart must NOT stamp the cooldown — the next cycle retries.
    assert cw._last_recycle_monotonic is None


def test_stats_failure_is_surfaced_not_silent():
    pool = _make_pool()
    restart = MagicMock(side_effect=AssertionError("must not restart"))
    summary = asyncio.run(
        cw.run_comfyui_ram_watch_probe(
            pool,
            queue_fn=AsyncMock(return_value=False),
            mem_fn=MagicMock(return_value=None),
            restart_fn=restart,
        )
    )
    assert summary["ok"] is False
    assert summary["status"] == "stats_failed"


def test_watermark_reads_from_app_settings():
    """A float watermark below the footprint triggers; above it doesn't."""
    pool = _make_pool(setting_values={cw.WATERMARK_GB_KEY: "17.5"})
    restart = MagicMock(return_value=(True, "Restarted poindexter-comfyui"))
    summary = asyncio.run(
        cw.run_comfyui_ram_watch_probe(
            pool,
            queue_fn=AsyncMock(return_value=False),
            mem_fn=MagicMock(return_value=(10.0, 8.0)),
            restart_fn=restart,
        )
    )
    assert summary["status"] == "recycled"
    assert summary["watermark_gb"] == 17.5


def test_parse_status_rss_swap_gb():
    """VmRSS/VmSwap kB lines -> GB; missing VmSwap = 0; no VmRSS = None."""
    blob = (
        "Name:\tpython\n"
        "VmPeak:\t30000000 kB\n"
        "VmRSS:\t14574940 kB\n"
        "VmSwap:\t15518925 kB\n"
    )
    parsed = cw._parse_status_rss_swap_gb(blob)
    assert parsed is not None
    rss_gb, swap_gb = parsed
    assert rss_gb == pytest.approx(13.9, abs=0.01)
    assert swap_gb == pytest.approx(14.8, abs=0.01)

    swapless = cw._parse_status_rss_swap_gb("VmRSS:\t1048576 kB\n")
    assert swapless == (1.0, 0.0)

    assert cw._parse_status_rss_swap_gb("Name:\tpython\n") is None
    assert cw._parse_status_rss_swap_gb("") is None
    assert cw._parse_status_rss_swap_gb("VmRSS:\tgarbage kB\n") is None
