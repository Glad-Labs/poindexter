"""The GPU-lock timeout must name its holder (poindexter#1018).

An operator image action that lost the race failed with

    503 gpu_busy: gpu.lock('image_gen') timed out after 150.0s waiting for
    in-process holder None (None)

``holder None`` reads like a wedge, so the operator's response was to pause
the Prefect deployment and wait out the in-flight run — for a lock that was
simply in use. ``_current_owner`` describes THIS process, and a cross-process
holder is by definition somewhere else, so it can only ever be None here.

Postgres already knows: the advisory lock sits on a dedicated connection, so
stamping that connection's ``application_name`` makes the holder
self-describing and any waiter can read it back from ``pg_stat_activity``.

#1018 wired that into the ``pg_advisory``-stage timeout ONLY, so the exact
message it set out to kill kept shipping from the ``in_process`` stage — which
is the stage that produces it, because a gate-holder still parked at the pg
step has not set ``_current_owner`` yet. The last class here pins both stages.
"""

from __future__ import annotations

import os

import pytest

from poindexter.services.gpu_scheduler import (
    _describe_pg_holder,
    _holder_tag,
    _parse_holder_tag,
)

pytestmark = pytest.mark.unit


class TestHolderTag:
    def test_carries_owner_phase_task_and_pid(self):
        tag = _holder_tag("image_gen", "regen_featured", "4a23f39e-0000-1111")
        name = tag["application_name"]
        assert name.startswith("poindexter-gpu:image_gen:regen_featured:")
        assert "4a23f39e" in name, "short task id so the operator can grep it"
        assert f"pid{os.getpid()}" in name

    def test_fits_postgres_application_name_limit(self):
        """NAMEDATALEN is 63 bytes — Postgres silently truncates past it, which
        would corrupt the trailing pid rather than fail loudly."""
        tag = _holder_tag("a" * 40, "b" * 40, "c" * 40)
        assert len(tag["application_name"]) <= 63

    def test_missing_fields_do_not_produce_a_broken_tag(self):
        name = _holder_tag(None, None, None)["application_name"]
        assert name.startswith("poindexter-gpu:?:?:")
        assert f"pid{os.getpid()}" in name


class TestParseHolderTag:
    def test_reads_back_our_own_tag(self):
        parsed = _parse_holder_tag(
            f"poindexter-gpu:image_gen:regen_featured:4a23f39e:pid{os.getpid()}"
        )
        assert "image_gen" in parsed
        assert "phase=regen_featured" in parsed
        assert "4a23f39e" in parsed

    @pytest.mark.parametrize("app", ["psql", "pgAdmin 4", ""])
    def test_foreign_sessions_pass_through(self, app):
        """A lock held by something that is not us still has to be reportable —
        that is the case where the operator most needs to know."""
        out = _parse_holder_tag(app)
        assert out == (app or "an untagged session")


class TestDescribeHolderIsFailSoft:
    """This runs on a path that is ALREADY failing. A diagnostics error must
    degrade the message, never replace the timeout with its own crash."""

    async def test_unreachable_database_degrades_the_message(self, monkeypatch):
        async def _refuse(*a, **k):
            raise OSError("connection refused")

        monkeypatch.setattr("asyncpg.connect", _refuse)
        out = await _describe_pg_holder("postgresql://x/y", [7_777_777_777])
        assert "holder unidentified" in out

    async def test_query_failure_degrades_the_message(self, monkeypatch):
        class _Conn:
            async def fetch(self, *a, **k):
                raise RuntimeError("relation pg_locks does not exist")

            async def close(self):
                return None

        async def _connect(*a, **k):
            return _Conn()

        monkeypatch.setattr("asyncpg.connect", _connect)
        out = await _describe_pg_holder("postgresql://x/y", [1])
        assert "holder query failed" in out

    async def test_no_holder_rows_reads_as_a_lost_race_not_a_wedge(self, monkeypatch):
        """The holder released between our timeout and the lookup. Telling the
        operator to retry is the whole point — 'wedge' sends them to restart a
        worker that was working."""
        class _Conn:
            async def fetch(self, *a, **k):
                return []

            async def close(self):
                return None

        async def _connect(*a, **k):
            return _Conn()

        monkeypatch.setattr("asyncpg.connect", _connect)
        out = await _describe_pg_holder("postgresql://x/y", [1])
        assert "not a wedge" in out and "retry" in out

    async def test_names_the_holder_when_postgres_reports_one(self, monkeypatch):
        class _Conn:
            async def fetch(self, *a, **k):
                return [{
                    "app": "poindexter-gpu:writer:generate_draft:abc12345:pid99",
                    "pid": 99,
                    "held_s": 212,
                }]

            async def close(self):
                return None

        async def _connect(*a, **k):
            return _Conn()

        monkeypatch.setattr("asyncpg.connect", _connect)
        out = await _describe_pg_holder("postgresql://x/y", [1])
        assert "writer" in out
        assert "phase=generate_draft" in out
        assert "212s" in out


class TestInProcessStageNamesTheCrossProcessHolder:
    """The in_process branch is where "holder None (None)" actually came from.

    `_current_owner` is set only AFTER both the gate and the pg lock are held.
    So whenever the gate-holder in this process is itself queued behind another
    container, every later caller times out against a gate whose owner reads
    None — and the operator is told a wedge is in progress by the one message
    that could have named the render responsible.
    """

    @pytest.mark.asyncio
    async def test_message_names_the_other_process(self, monkeypatch):
        from unittest.mock import AsyncMock

        from poindexter.services.gpu_scheduler import GpuLockTimeoutError, GPUScheduler

        gpu = GPUScheduler()
        gpu._wait_for_gaming_clear = AsyncMock()
        gpu._unload_ollama_models = AsyncMock()
        gpu._acquire_pg_advisory_lock = AsyncMock()
        gpu._release_pg_advisory_lock = AsyncMock()
        monkeypatch.setattr(
            "poindexter.services.gpu_scheduler._cfg_int", lambda key, default: 1
        )
        monkeypatch.setattr(
            "poindexter.services.gpu_scheduler.list_pg_holders",
            AsyncMock(
                return_value=[
                    {
                        "owner": "video",
                        "phase": "media_render",
                        "task_id": "f555bedc",
                        "pid": 7564,
                        "backend_pid": 245475,
                        "application_name": "poindexter-gpu:video:media_render:pid7564",
                        "held_for_s": 1419.0,
                        "keys": [7777777777],
                        "exclusive": True,
                    }
                ]
            ),
        )

        import asyncio

        entered = asyncio.Event()
        release = asyncio.Event()

        async def gate_holder():
            # Takes the in-process gate, then parks — exactly the shape of a
            # caller blocked inside the pg step. `_current_owner` stays None.
            await gpu._acquire_gates([7777777777], rank=0, timeout_s=None)
            entered.set()
            await release.wait()
            gpu._release_gates()

        h = asyncio.create_task(gate_holder())
        await entered.wait()
        assert gpu._current_owner is None, "precondition: the bug's shape"

        with pytest.raises(GpuLockTimeoutError) as err:
            async with gpu.lock("ollama"):
                pass  # pragma: no cover — never acquired

        message = str(err.value)
        assert "video" in message and "media_render" in message
        assert "holder None (None)" not in message

        release.set()
        await h

    @pytest.mark.asyncio
    async def test_in_process_holder_is_still_named_when_known(self, monkeypatch):
        """When this process DOES own the lock, say so plainly — the pg lookup
        is for the case the local view cannot answer, not a replacement."""
        from unittest.mock import AsyncMock

        from poindexter.services.gpu_scheduler import GpuLockTimeoutError, GPUScheduler

        gpu = GPUScheduler()
        gpu._wait_for_gaming_clear = AsyncMock()
        gpu._unload_ollama_models = AsyncMock()
        gpu._acquire_pg_advisory_lock = AsyncMock()
        gpu._release_pg_advisory_lock = AsyncMock()
        monkeypatch.setattr(
            "poindexter.services.gpu_scheduler._cfg_int", lambda key, default: 1
        )

        import asyncio

        entered = asyncio.Event()
        release = asyncio.Event()

        async def holder():
            async with gpu.lock("image_gen", model="z-image"):
                entered.set()
                await release.wait()

        h = asyncio.create_task(holder())
        await entered.wait()

        with pytest.raises(GpuLockTimeoutError) as err:
            async with gpu.lock("ollama"):
                pass  # pragma: no cover — never acquired

        assert "in-process holder 'image_gen' ('z-image')" in str(err.value)

        release.set()
        await h
