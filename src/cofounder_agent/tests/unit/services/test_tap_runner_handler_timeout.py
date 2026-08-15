"""Per-handler timeout in ``services.integrations.tap_runner.run_all``.

NOT the embedding-taps runner (``services/taps/runner.py`` — its
``tap_run_timeout_seconds`` guard is covered by
``test_taps_runner_timeout.py``). This is the external_taps walker that
``RunTapsJob`` fires hourly.

2026-08-15 incident: the ``dev_diary_internal_rag`` and
``glad-labs_internal_rag`` handlers wedged on LiteLLM→Ollama embedding
calls after a CUDA OOM, and the ``await registry.dispatch(...)`` had no
timeout — one 02:06 run hung for 80 minutes until Ollama's runner died.
These tests lock the bound: each dispatch is wrapped in
``asyncio.wait_for`` (``tap_handler_timeout_seconds``, DB-tunable,
``<= 0`` disables). A handler that exceeds it is cancelled, recorded as
that tap's failure through the normal ``_record_failure`` path WITH a
reason (feedback_no_silent_defaults), and the walk continues.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from services.integrations import registry as registry_module
from services.integrations import tap_runner

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


class _FakeConn:
    def __init__(self, pool):
        self._pool = pool

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        pass

    async def execute(self, query, *args):
        self._pool.executes.append((query, args))
        return "UPDATE 1"

    async def fetch(self, query, *args):
        return self._pool.next_fetch


class _FakePool:
    def __init__(self):
        self.executes: list[tuple[str, tuple]] = []
        self.next_fetch: list[dict[str, Any]] = []

    def acquire(self):
        return _FakeConn(self)


class _FakeSiteConfig:
    """Minimal SiteConfig stand-in recording ``get_int`` lookups.

    Returns whatever value it was constructed with — the real
    ``SiteConfig.get_int`` returns int, and ``run_all`` floats it, so a
    fractional fake value exercises the same plumbing without a
    multi-second test sleep.
    """

    def __init__(self, timeout_value):
        self._timeout_value = timeout_value
        self.get_int_calls: list[tuple[str, int]] = []

    def get_int(self, key, default=0):
        self.get_int_calls.append((key, default))
        return self._timeout_value


def _tap_row(**overrides):
    base = {
        "id": "00000000-0000-0000-0000-000000000030",
        "name": "hackernews",
        "handler_name": "healthy_handler",
        "tap_type": "hackernews",
        "target_table": "content_tasks",
        "record_handler": None,
        "schedule": "every 1 hour",
        "config": {},
        "state": {},
        "enabled": True,
        "metadata": {},
    }
    base.update(overrides)
    return base


async def _hanging_handler(payload, *, site_config, row, pool):
    await asyncio.Event().wait()  # never resolves


async def _healthy_handler(payload, *, site_config, row, pool):
    return {"records": 7}


async def _sleeping_handler(payload, *, site_config, row, pool):
    await asyncio.sleep(0.05)
    return {"records": 1}


@pytest.fixture(autouse=True)
def _registry_isolation():
    saved = dict(registry_module._REGISTRY)
    registry_module._REGISTRY.clear()
    registry_module._REGISTRY["tap.hang_handler"] = _hanging_handler
    registry_module._REGISTRY["tap.healthy_handler"] = _healthy_handler
    registry_module._REGISTRY["tap.sleeping_handler"] = _sleeping_handler
    yield
    registry_module._REGISTRY.clear()
    registry_module._REGISTRY.update(saved)


class TestHandlerTimeout:
    async def test_hung_handler_cancelled_and_walk_continues(self):
        """A wedged handler is bounded, recorded failed with a reason, and
        the run still reaches the taps after it (per-row isolation)."""
        pool = _FakePool()
        pool.next_fetch = [
            _tap_row(name="wedged", handler_name="hang_handler",
                     id="00000000-0000-0000-0000-000000000001"),
            _tap_row(name="fine", handler_name="healthy_handler",
                     id="00000000-0000-0000-0000-000000000002"),
        ]

        summary = await tap_runner.run_all(pool, handler_timeout_s=0.05)

        by_name = {t.name: t for t in summary.taps}
        assert by_name["wedged"].ok is False
        assert "tap_handler_timeout_seconds" in (by_name["wedged"].error or "")
        # The hang did NOT kill the walk — the next tap still ran.
        assert by_name["fine"].ok is True
        assert by_name["fine"].records == 7
        assert summary.total_failed == 1
        assert summary.total_records == 7

        # The timeout went through the normal _record_failure path, so the
        # external_taps row carries the reason (operator-visible on the
        # Integrations dashboard), and the healthy tap got its success write.
        failure_writes = [
            (q, a) for q, a in pool.executes if "last_run_status = 'failed'" in q
        ]
        assert len(failure_writes) == 1
        _, args = failure_writes[0]
        assert args[0] == "00000000-0000-0000-0000-000000000001"
        assert "tap_handler_timeout_seconds" in args[2]
        assert any(
            "last_run_status = 'success'" in q for q, _ in pool.executes
        )

    async def test_timeout_resolved_from_site_config(self):
        """With no explicit override, the budget comes from
        ``app_settings.tap_handler_timeout_seconds`` — and the resolved
        value is what actually bounds the dispatch."""
        pool = _FakePool()
        pool.next_fetch = [_tap_row(name="wedged", handler_name="hang_handler")]
        site_config = _FakeSiteConfig(0.05)

        summary = await tap_runner.run_all(pool, site_config=site_config)

        assert site_config.get_int_calls == [("tap_handler_timeout_seconds", 600)]
        assert summary.total_failed == 1
        assert "tap_handler_timeout_seconds" in (summary.taps[0].error or "")

    async def test_zero_disables_the_bound(self):
        """``tap_handler_timeout_seconds=0`` means no wait_for wrapper at
        all — a slow handler completes instead of being insta-cancelled
        (which is what naively passing 0 to wait_for would do)."""
        pool = _FakePool()
        pool.next_fetch = [_tap_row(name="slow", handler_name="sleeping_handler")]

        summary = await tap_runner.run_all(pool, handler_timeout_s=0)

        assert summary.total_failed == 0
        assert summary.taps[0].ok is True
        assert summary.taps[0].records == 1

    async def test_explicit_param_pins_over_site_config(self):
        """An explicit ``handler_timeout_s`` wins and the setting is not
        consulted — mirrors the pinned-param semantics of the embedding
        runner's ``tap_timeout_s``."""
        pool = _FakePool()
        pool.next_fetch = [_tap_row(name="wedged", handler_name="hang_handler")]
        site_config = _FakeSiteConfig(999)

        summary = await tap_runner.run_all(
            pool, site_config=site_config, handler_timeout_s=0.05,
        )

        assert site_config.get_int_calls == []
        assert summary.total_failed == 1
