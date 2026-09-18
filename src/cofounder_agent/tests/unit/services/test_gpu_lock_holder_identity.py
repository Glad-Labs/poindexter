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
