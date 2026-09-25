"""Admission sees a GPU holder in ANOTHER process (poindexter#914 P1 follow-on).

Admission estimated the holder's remaining time only from ``_current_owner`` /
``_acquired_at`` — the holder in THIS process. The pipeline's GPU work is
split across containers: content flows run in ``poindexter-prefect-worker``,
media renders (``gpu.lock("video", phase="media_render")``, p90 ~2500 s) run
in ``poindexter-worker``. So a budgeted caller in prefect-worker saw no holder
behind a cross-process render, was granted, burned its whole budget at the
pg-advisory step and ended in ``GpuLockTimeoutError`` plus a warn
``gpu_lock_timeout`` page. That is the doomed wait admission exists to stop.

Now, with no in-process holder on the caller's cards, admission reads the
holders from Postgres (``list_pg_holders``), keeps the ones that block the
caller's own lock keys (``pg_holder_blocks``), and estimates against the
longest of them. Pinned here:

* a cross-process render on the caller's card ⇒ ``GpuBusyError`` BEFORE any wait
* the same render seen from a caller on a disjoint card ⇒ granted (a GPU-1
  judge is never refused because of a GPU-0 render)
* every missing or failed read degrades to "grant", never to a false reject
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from poindexter.services import gpu_scheduler as gs
from poindexter.services.gpu_admission import GpuBusyError
from poindexter.services.gpu_lease_stats import LeaseStats
from poindexter.services.gpu_scheduler import (
    GPU_ADVISORY_LOCK_KEY,
    GPUScheduler,
    _holder_stats_key,
    pg_holder_blocks,
)
from poindexter.services.site_config import SiteConfig

BASE = GPU_ADVISORY_LOCK_KEY
#: Pinned so the suite never depends on whether the RUNNER is containerised:
#: ambient detection returns "" in a container, which fails closed to the
#: whole-GPU key and would quietly turn every scoped case into an unscoped one.
NODE = "test-node"
GPU0 = gs.device_lock_key(NODE, 0)
GPU1 = gs.device_lock_key(NODE, 1)

#: Measured on prod 2026-09-25: video/media_render p90 = 2530 s over 259 holds.
MEDIA_RENDER_P90_MS = 2_530_000.0


def _row(
    owner: str | None,
    phase: str | None,
    *,
    keys: list[int],
    exclusive_keys: list[int],
    held_for_s: float | None = 300.0,
    backend_pid: int = 4242,
) -> dict:
    """One ``list_pg_holders`` row, shaped exactly as the function returns it."""
    tag = f"poindexter-gpu:{owner}:{phase}:pid77" if owner else "psql"
    return {
        "owner": owner,
        "phase": phase,
        "task_id": None,
        "pid": 77 if owner else None,
        "backend_pid": backend_pid,
        "client_addr": "172.18.0.9",
        "application_name": tag,
        "held_for_s": held_for_s,
        "keys": keys,
        "exclusive_keys": exclusive_keys,
        "exclusive": bool(exclusive_keys),
    }


def _render_on_gpu0(**kw) -> dict:
    """A device-scoped render in another container: base SHARED, GPU 0 exclusive."""
    return _row("video", "media_render", keys=[BASE, GPU0], exclusive_keys=[GPU0], **kw)


def _stats(p90_ms: float) -> LeaseStats:
    return LeaseStats(samples=50, ewma_ms=p90_ms / 2, p50_ms=p90_ms / 2, p90_ms=p90_ms)


# ---------------------------------------------------------------------------
# pg_holder_blocks — the overlap rule, pure
# ---------------------------------------------------------------------------


class TestPgHolderBlocks:
    def test_scoped_render_does_not_block_a_judge_on_the_other_card(self):
        assert pg_holder_blocks(_render_on_gpu0(), [GPU1]) is False

    def test_scoped_render_blocks_a_caller_on_its_own_card(self):
        assert pg_holder_blocks(_render_on_gpu0(), [GPU0]) is True

    def test_scoped_render_blocks_a_caller_that_needs_both_cards(self):
        assert pg_holder_blocks(_render_on_gpu0(), [GPU0, GPU1]) is True

    def test_unscoped_caller_is_blocked_by_a_shared_base_holder(self):
        """An unscoped caller takes the base key EXCLUSIVELY, which conflicts
        with a scoped holder's SHARED base — the transition-window case that
        _acquire_pg_advisory_lock's shared base exists to serialise."""
        assert pg_holder_blocks(_render_on_gpu0(), [BASE]) is True

    def test_unscoped_caller_is_blocked_by_an_unscoped_holder(self):
        holder = _row("ollama", "generate_content", keys=[BASE], exclusive_keys=[BASE])
        assert pg_holder_blocks(holder, [BASE]) is True

    def test_scoped_caller_is_blocked_by_an_exclusive_base_holder(self):
        """A process still running unscoped holds the base key exclusively, and
        that blocks EVERY scoped caller, whatever card it wants."""
        holder = _row("ollama", "generate_content", keys=[BASE], exclusive_keys=[BASE])
        assert pg_holder_blocks(holder, [GPU1]) is True

    def test_scoped_waiter_does_not_block_a_scoped_caller(self):
        """A scoped caller parked on its device key already holds the base key
        SHARED, so list_pg_holders lists it. It holds nothing a scoped caller
        conflicts with, so it must not be read as the holder."""
        waiter = _row("ollama", "writer", keys=[BASE], exclusive_keys=[])
        assert pg_holder_blocks(waiter, [GPU0]) is False

    def test_a_caller_that_takes_no_lock_is_never_blocked(self):
        holder = _row("ollama", "x", keys=[BASE], exclusive_keys=[BASE])
        assert pg_holder_blocks(holder, []) is False

    def test_missing_exclusive_keys_reads_as_all_shared(self):
        """Without per-key modes a scoped caller can only be sure of a device-
        key clash. Assuming the base is shared under-counts blockers, which
        fails toward "grant" (admission's contract), never toward a reject."""
        holder = _row("ollama", "x", keys=[BASE], exclusive_keys=[])
        del holder["exclusive_keys"]
        assert pg_holder_blocks(holder, [GPU0]) is False
        # The unscoped caller does not need the mode: any base holder blocks.
        assert pg_holder_blocks(holder, [BASE]) is True


class TestHolderStatsKey:
    def test_tagged_holder_maps_to_its_owner_and_phase(self):
        assert _holder_stats_key(_render_on_gpu0()) == ("video", "media_render")

    @pytest.mark.parametrize("phase", [None, "", "?"])
    def test_missing_phase_maps_to_the_owner_like_lock_records_it(self, phase):
        """lock() records releases under (owner, phase or owner), and
        _holder_tag writes "?" for a missing phase."""
        row = _row("ollama", phase, keys=[BASE], exclusive_keys=[BASE])
        assert _holder_stats_key(row) == ("ollama", "ollama")

    @pytest.mark.parametrize("owner", [None, "", "?"])
    def test_untagged_holder_has_no_key(self, owner):
        row = _row("ollama", "x", keys=[BASE], exclusive_keys=[BASE])
        row["owner"] = owner
        assert _holder_stats_key(row) is None


# ---------------------------------------------------------------------------
# End-to-end through gpu.lock()
# ---------------------------------------------------------------------------


class _FakeRegistry:
    """Plenty of free VRAM on both cards, so only the ETA gate can reject."""

    async def free_gb(self, idx):
        return 30.0

    async def evictable_ollama_gb(self, idx):
        return 0.0

    async def reclaimable_sidecar_gb(self, idx):
        return 0.0


@pytest.fixture
def scheduler_env(monkeypatch):
    """A scheduler with device scoping + admission on, and no real I/O.

    Returns ``(gpu, holders, stats_reader)``: ``holders`` is the list the
    patched ``list_pg_holders`` returns, ``stats_reader`` the AsyncMock behind
    ``read_stats_many``, answering from a dict the test fills in.
    """

    def _apply(*, scoped: bool = True, stats: dict | None = None):
        cfg = SiteConfig(initial_config={
            "gpu_sched_enabled": "true",
            "gpu_lock_node_id": NODE,
            "gpu_lock_per_device_enabled": "true" if scoped else "false",
            "gpu_lock_scopes": json.dumps(
                {"render": [0], "qa_judge": [1], "llm_primary": [0]}
            ),
            "ollama_gpu_indexes": "0,1",
            "plugin.llm_provider.litellm": json.dumps(
                {"config": {"model_api_base_overrides": {
                    "ollama/judge-model": "http://x:11435"}}}
            ),
        })
        monkeypatch.setattr(gs, "_sc", lambda: cfg)

        holders: list[dict] = []
        monkeypatch.setattr(gs, "list_pg_holders", AsyncMock(return_value=holders))
        def _read(keys):
            wanted = set(keys)
            return {k: v for k, v in (stats or {}).items() if k in wanted}

        stats_reader = AsyncMock(side_effect=_read)
        monkeypatch.setattr(
            "poindexter.services.gpu_lease_stats.read_stats_many", stats_reader
        )
        # Nothing is sized: an unknown estimate skips the fit gate, so these
        # tests isolate the ETA gate.
        monkeypatch.setattr(
            "poindexter.services.llm_providers.dispatcher._read_arch_for_budget",
            AsyncMock(return_value=None),
        )

        gpu = GPUScheduler()
        gpu._registry = _FakeRegistry()
        gpu._acquire_pg_advisory_lock = AsyncMock()
        gpu._release_pg_advisory_lock = AsyncMock()
        gpu._wait_for_gaming_clear = AsyncMock()
        gpu._unload_ollama_models = AsyncMock()
        gpu._emit_admission_rejected_finding = MagicMock()
        gpu._emit_lock_timeout_finding = MagicMock()
        return gpu, holders, stats_reader

    return _apply


class TestCrossProcessHolderThroughLock:
    async def test_render_on_the_callers_card_rejects_before_any_wait(self, scheduler_env):
        """THE fix. A media stage in prefect-worker (llm_primary → GPU 0, 120 s
        budget) behind a render in poindexter-worker used to be granted, then
        spend its 120 s at the pg step. Now it is refused up front."""
        gpu, holders, _ = scheduler_env(
            stats={("video", "media_render"): _stats(MEDIA_RENDER_P90_MS)}
        )
        holders.append(_render_on_gpu0(held_for_s=300.0))

        with pytest.raises(GpuBusyError) as err:
            async with gpu.lock(
                "ollama", model="writer", phase="media_scripts", max_wait_s=120.0
            ):
                pytest.fail("admission must refuse before the lock is taken")

        assert err.value.reason == "eta_exceeds_budget"
        assert err.value.eta_seconds == pytest.approx(2530.0 - 300.0)
        # Pre-wait: no gate and no pg lock was ever taken.
        gpu._acquire_pg_advisory_lock.assert_not_awaited()
        assert not gpu._any_gate_locked()
        # The warn-level page this fix exists to replace never fires.
        gpu._emit_lock_timeout_finding.assert_not_called()
        # The info finding names the holder and says it lives elsewhere.
        kwargs = gpu._emit_admission_rejected_finding.call_args.kwargs
        assert kwargs["holder_owner"] == "video"
        assert kwargs["holder_phase"] == "media_render"
        assert kwargs["holder_source"] == "postgres"
        assert kwargs["holder_elapsed_s"] == 300.0

    async def test_render_on_a_disjoint_card_does_not_refuse_a_judge(self, scheduler_env):
        """A QA judge pinned to GPU 1 (45 s budget) must run straight through a
        GPU-0 render. Being refused there would undo device scoping."""
        gpu, holders, stats_reader = scheduler_env(
            stats={("video", "media_render"): _stats(MEDIA_RENDER_P90_MS)}
        )
        holders.append(_render_on_gpu0(held_for_s=300.0))

        entered = False
        async with gpu.lock(
            "ollama", model="judge-model", phase="qa_ragas_judge", max_wait_s=45.0
        ):
            entered = True

        assert entered
        gs.list_pg_holders.assert_awaited_once()
        # No blocker survived the overlap filter, so no stats were read.
        stats_reader.assert_not_awaited()
        gpu._emit_admission_rejected_finding.assert_not_called()

    async def test_unscoped_process_is_refused_behind_a_scoped_render(self, scheduler_env):
        """A process whose scoping is OFF takes the base key exclusively, so a
        render sharing that key in another process does block it."""
        gpu, holders, _ = scheduler_env(
            scoped=False,
            stats={("video", "media_render"): _stats(MEDIA_RENDER_P90_MS)},
        )
        holders.append(_render_on_gpu0(held_for_s=100.0))

        with pytest.raises(GpuBusyError) as err:
            async with gpu.lock("ollama", model="judge-model", max_wait_s=45.0):
                pytest.fail("admission must refuse before the lock is taken")
        assert err.value.eta_seconds == pytest.approx(2430.0)

    async def test_holder_about_to_finish_is_waited_for(self, scheduler_env):
        """p90 − elapsed inside the budget: a short wait worth taking."""
        gpu, holders, _ = scheduler_env(
            stats={("ollama", "topic_ranking"): _stats(10_000.0)}
        )
        holders.append(
            _row("ollama", "topic_ranking", keys=[BASE, GPU0], exclusive_keys=[GPU0],
                 held_for_s=4.0)
        )

        async with gpu.lock("ollama", model="writer", max_wait_s=120.0):
            pass
        gpu._emit_admission_rejected_finding.assert_not_called()


class TestFailOpen:
    """Every missing or failed read degrades to "grant" — the caller then waits
    at the lock, bounded by its budget, exactly as before the lookup existed."""

    async def test_holder_lookup_that_raises_grants(self, scheduler_env):
        gpu, _, _ = scheduler_env()
        gs.list_pg_holders.side_effect = RuntimeError("regressed row mapping")

        async with gpu.lock("ollama", model="writer", max_wait_s=45.0):
            pass
        gpu._emit_admission_rejected_finding.assert_not_called()

    async def test_unprofiled_holder_grants_instead_of_the_fallback_eta(self, scheduler_env):
        """The brain's probes take the base key for seconds and are never in
        gpu_lease_stats. The 120 s fallback ETA would refuse a 45 s rail behind
        a five-second probe, so an unprofiled cross-process holder is no holder."""
        gpu, holders, stats_reader = scheduler_env(stats={})
        holders.append(
            _row("brain_probe", "content_gen", keys=[BASE], exclusive_keys=[BASE],
                 held_for_s=2.0)
        )

        async with gpu.lock("ollama", model="judge-model", max_wait_s=45.0):
            pass
        stats_reader.assert_awaited_once()
        gpu._emit_admission_rejected_finding.assert_not_called()

    async def test_stats_read_failure_grants(self, scheduler_env):
        gpu, holders, stats_reader = scheduler_env()
        holders.append(_render_on_gpu0())
        stats_reader.side_effect = None
        stats_reader.return_value = {}  # read_stats_many's own failure shape

        async with gpu.lock("ollama", model="writer", max_wait_s=45.0):
            pass
        gpu._emit_admission_rejected_finding.assert_not_called()

    async def test_untagged_holder_grants_without_a_stats_read(self, scheduler_env):
        gpu, holders, stats_reader = scheduler_env()
        holders.append(_row(None, None, keys=[BASE], exclusive_keys=[BASE]))

        async with gpu.lock("ollama", model="writer", max_wait_s=45.0):
            pass
        stats_reader.assert_not_awaited()
        gpu._emit_admission_rejected_finding.assert_not_called()

    async def test_holder_without_timing_grants(self, scheduler_env):
        gpu, holders, stats_reader = scheduler_env(
            stats={("video", "media_render"): _stats(MEDIA_RENDER_P90_MS)}
        )
        holders.append(_render_on_gpu0(held_for_s=None))

        async with gpu.lock("ollama", model="writer", max_wait_s=45.0):
            pass
        stats_reader.assert_not_awaited()


# ---------------------------------------------------------------------------
# Holder resolution order and the multi-holder choice
# ---------------------------------------------------------------------------


class TestHolderResolution:
    async def test_in_process_holder_on_the_callers_card_skips_the_db_lookup(
        self, scheduler_env, monkeypatch,
    ):
        """This process's own session answers for free — no extra round-trip."""
        gpu, _, _ = scheduler_env()
        read_stats = AsyncMock(return_value=_stats(60_000.0))
        monkeypatch.setattr("poindexter.services.gpu_lease_stats.read_stats", read_stats)
        inside, release = asyncio.Event(), asyncio.Event()

        async def render():
            async with gpu.lock("image_gen", model="sdxl", phase="featured_image"):
                inside.set()
                await release.wait()

        task = asyncio.create_task(render())
        await inside.wait()
        try:
            inputs = await gpu._assemble_admission_inputs(
                model=None, max_wait_s=45.0,
                lock_keys=gs.resolve_lock_keys("ollama", "writer"),
            )
        finally:
            release.set()
            await task

        assert inputs.holder_key == ("image_gen", "featured_image")
        assert inputs.holder_source == "in_process"
        gs.list_pg_holders.assert_not_awaited()
        read_stats.assert_awaited_once_with("image_gen", "featured_image")

    async def test_in_process_holder_on_another_card_is_not_the_holder(
        self, scheduler_env,
    ):
        """An in-process judge on GPU 1 does not block a GPU-0 caller, so the
        lookup goes on to Postgres and finds the render that does."""
        gpu, holders, _ = scheduler_env(
            stats={("video", "media_render"): _stats(MEDIA_RENDER_P90_MS)}
        )
        holders.append(_render_on_gpu0(held_for_s=60.0))
        inside, release = asyncio.Event(), asyncio.Event()

        async def judge():
            async with gpu.lock("ollama", model="judge-model", phase="qa_judge"):
                inside.set()
                await release.wait()

        task = asyncio.create_task(judge())
        await inside.wait()
        try:
            inputs = await gpu._assemble_admission_inputs(
                model=None, max_wait_s=45.0,
                lock_keys=gs.resolve_lock_keys("ollama", "writer"),
            )
        finally:
            release.set()
            await task

        assert inputs.holder_key == ("video", "media_render")
        assert inputs.holder_source == "postgres"

    async def test_longest_remaining_blocker_is_the_holder(self, scheduler_env):
        """An unscoped caller must outlast every blocker, so the one with the
        most time left is what the ETA gate weighs — in ONE stats round-trip."""
        gpu, holders, stats_reader = scheduler_env(
            scoped=False,
            stats={
                ("ollama", "qa_ragas_judge"): _stats(33_000.0),
                ("video", "media_render"): _stats(MEDIA_RENDER_P90_MS),
            },
        )
        holders.append(
            _row("ollama", "qa_ragas_judge", keys=[BASE, GPU1], exclusive_keys=[GPU1],
                 held_for_s=3.0, backend_pid=1)
        )
        holders.append(_render_on_gpu0(held_for_s=1000.0, backend_pid=2))

        inputs = await gpu._assemble_admission_inputs(
            model=None, max_wait_s=45.0, lock_keys=[BASE],
        )

        assert inputs.holder_key == ("video", "media_render")
        assert inputs.holder_elapsed_s == 1000.0
        stats_reader.assert_awaited_once()
        (keys_arg,), _ = stats_reader.await_args
        assert set(keys_arg) == {("ollama", "qa_ragas_judge"), ("video", "media_render")}

    async def test_no_keys_named_keeps_the_in_process_only_view(self, scheduler_env):
        """A caller that does not pass lock_keys cannot have overlap judged, so
        admission keeps its old in-process-only view and never asks Postgres."""
        gpu, holders, _ = scheduler_env()
        holders.append(_render_on_gpu0())

        inputs = await gpu._assemble_admission_inputs(model=None, max_wait_s=45.0)

        assert inputs.holder_key is None
        gs.list_pg_holders.assert_not_awaited()

    async def test_caller_that_takes_no_lock_never_asks_postgres(self, scheduler_env):
        gpu, holders, _ = scheduler_env()
        holders.append(_render_on_gpu0())

        inputs = await gpu._assemble_admission_inputs(
            model=None, max_wait_s=45.0, lock_keys=[],
        )

        assert inputs.holder_key is None
        gs.list_pg_holders.assert_not_awaited()

    async def test_budgetless_caller_never_asks_postgres(self, scheduler_env):
        """The extra round-trip is on the budgeted path only: a caller with no
        max_wait_s never runs admission at all."""
        gpu, holders, _ = scheduler_env()
        holders.append(_render_on_gpu0())
        async with gpu.lock("ollama", model="writer"):
            pass
        gs.list_pg_holders.assert_not_awaited()


# ---------------------------------------------------------------------------
# The reject finding names the holder
# ---------------------------------------------------------------------------


def test_reject_finding_names_a_cross_process_holder():
    gpu = GPUScheduler()
    captured: dict = {}

    def _fake_emit(**kwargs):
        captured.update(kwargs)

    with patch("poindexter.utils.findings.emit_finding", _fake_emit):
        gpu._emit_admission_rejected_finding(
            owner="ollama", phase="media_scripts", reason="eta_exceeds_budget",
            eta_seconds=2230.0, max_wait_s=120.0,
            holder_owner="video", holder_phase="media_render",
            holder_source="postgres", holder_elapsed_s=300.0,
        )

    assert "behind video/media_render (another process, held 300s)" in captured["body"]
    assert captured["extra"]["holder_source"] == "postgres"
    assert captured["extra"]["holder_owner"] == "video"
    # Dedup stays keyed on the CALLER, so one busy render window is one row
    # per (caller, reason), not one per holder.
    assert captured["dedup_key"] == "gpu-admission:ollama:media_scripts:eta_exceeds_budget"


def test_in_process_overlap_is_judged_by_the_sessions_own_keys():
    gpu = GPUScheduler()
    gpu._current_owner = "image_gen"
    gpu._held_keys = [GPU0]
    assert gpu._overlapping_in_process_holder([GPU0]) is True
    assert gpu._overlapping_in_process_holder([GPU1]) is False
    # A gate-holder still parked at the pg step has not named itself yet: it
    # is a waiter, not the holder.
    gpu._current_owner = None
    assert gpu._overlapping_in_process_holder([GPU0]) is False


# ---------------------------------------------------------------------------
# list_pg_holders carries per-key modes (the input pg_holder_blocks needs)
# ---------------------------------------------------------------------------


class TestListPgHoldersCarriesModes:
    async def test_exclusive_keys_are_mapped_and_null_reads_as_none_held(self, monkeypatch):
        # The function is hermetic under pytest by design; this test exercises
        # the row mapping, against a fake connection, so it lifts the guard.
        monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)

        class _Conn:
            async def fetch(self, _sql, key):
                assert key == BASE
                return [
                    {   # a scoped render: base shared, GPU 0 exclusive
                        "backend_pid": 11,
                        "app": "poindexter-gpu:video:media_render:pid7",
                        "client_addr": "172.18.0.9",
                        "held_for_s": 300.0,
                        "keys": [BASE, GPU0],
                        "exclusive_keys": [GPU0],
                        "exclusive": True,
                    },
                    {   # a scoped waiter: only the shared base, so the
                        # FILTER matched nothing and Postgres returns NULL
                        "backend_pid": 12,
                        "app": "poindexter-gpu:ollama:writer:pid8",
                        "client_addr": "172.18.0.10",
                        "held_for_s": 4.0,
                        "keys": [BASE],
                        "exclusive_keys": None,
                        "exclusive": False,
                    },
                ]

            async def close(self):
                return None

        async def _connect(*_a, **_k):
            return _Conn()

        monkeypatch.setattr("asyncpg.connect", _connect)
        render, waiter = await gs.list_pg_holders(dsn="postgresql://x/y")

        assert render["exclusive_keys"] == [GPU0]
        assert waiter["exclusive_keys"] == []
        # And the overlap rule reads them the way the modes mean.
        assert pg_holder_blocks(render, [GPU0]) is True
        assert pg_holder_blocks(waiter, [GPU0]) is False

    def test_query_asks_postgres_for_the_exclusive_keys(self):
        """The mapping above is only as good as the SQL feeding it."""
        sql = " ".join(gs._PG_HOLDERS_SQL.split())
        assert "FILTER (WHERE l.mode = 'ExclusiveLock') AS exclusive_keys" in sql
