"""Unit tests for ProbeMediaRenderLoopJob (poindexter#1021).

Per-attempt ``render_failed`` findings are warn-level Discord noise, so a
permanent render-failure loop is invisible in aggregate — the 2026-08-24 one
churned model loads for five days and helped OOM the host before any page.
These pin the per-task aggregate watchdog that closes that gap.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from services.jobs.probe_media_render_loop import ProbeMediaRenderLoopJob
from services.site_config import SiteConfig


class _FakePool:
    """fetch() returns per-task failure rows; records the query args."""

    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows
        self.seen_args: tuple | None = None

    def acquire(self):  # type: ignore[no-untyped-def]
        pool = self

        class _Acq:
            async def __aenter__(self):  # type: ignore[no-untyped-def]
                conn = AsyncMock()

                async def _fetch(_sql, *args):  # type: ignore[no-untyped-def]
                    pool.seen_args = args
                    return pool._rows

                conn.fetch = _fetch
                return conn

            async def __aexit__(self, *_a):  # type: ignore[no-untyped-def]
                return False

        return _Acq()


def _cfg(**overrides: str) -> dict:
    return {"_site_config": SiteConfig(initial_config=dict(overrides))}


@pytest.mark.unit
class TestProbeMediaRenderLoop:
    async def test_looping_task_pages_critical(self, monkeypatch):
        findings: list[dict] = []
        monkeypatch.setattr(
            "services.jobs.probe_media_render_loop.emit_finding",
            lambda **kw: findings.append(kw),
        )
        # One structural loop (14 failures — the 08-24 per-task rate) plus a
        # transient one-off that must NOT page.
        pool = _FakePool([
            {"task_id": "d7948d57", "failures": 14},
            {"task_id": "aaaa1111", "failures": 1},
        ])

        result = await ProbeMediaRenderLoopJob().run(pool, _cfg())

        assert result.ok and result.changes_made == 1
        assert len(findings) == 1
        f = findings[0]
        assert f["kind"] == "media_render_loop"
        assert f["severity"] == "critical"
        # the looping task id + count must reach the operator
        assert "d7948d57 (14×)" in f["body"]
        assert "aaaa1111" not in f["body"]
        assert f["extra"]["tasks_looping"] == 1
        assert f["extra"]["tasks_failing"] == 2

    async def test_scattered_transient_failures_are_silent(self, monkeypatch):
        """Failures spread across MANY tasks are outage-shaped, not
        loop-shaped — the infra probes and existing alerts own that case."""
        findings: list[dict] = []
        monkeypatch.setattr(
            "services.jobs.probe_media_render_loop.emit_finding",
            lambda **kw: findings.append(kw),
        )
        pool = _FakePool([
            {"task_id": f"task-{i}", "failures": 2} for i in range(8)
        ])

        result = await ProbeMediaRenderLoopJob().run(pool, _cfg())

        assert result.ok and result.changes_made == 0
        assert findings == []
        assert result.metrics["render_failures"] == 16
        assert result.metrics["tasks_looping"] == 0

    async def test_threshold_and_window_are_db_tunable(self, monkeypatch):
        findings: list[dict] = []
        monkeypatch.setattr(
            "services.jobs.probe_media_render_loop.emit_finding",
            lambda **kw: findings.append(kw),
        )
        pool = _FakePool([{"task_id": "t1", "failures": 3}])

        result = await ProbeMediaRenderLoopJob().run(
            pool,
            _cfg(
                media_render_loop_min_failures="3",
                media_render_loop_window_hours="12",
            ),
        )

        assert result.changes_made == 1
        # window threads into the query; kinds list is the first arg
        assert pool.seen_args is not None
        kinds, window = pool.seen_args
        assert "render_failed" in kinds and "shot_list_invalid" in kinds
        assert window == 12

    async def test_disabled_via_app_settings(self, monkeypatch):
        findings: list[dict] = []
        monkeypatch.setattr(
            "services.jobs.probe_media_render_loop.emit_finding",
            lambda **kw: findings.append(kw),
        )
        pool = _FakePool([{"task_id": "t1", "failures": 99}])

        result = await ProbeMediaRenderLoopJob().run(
            pool, _cfg(media_render_loop_probe_enabled="false"),
        )

        assert result.ok and result.changes_made == 0
        assert findings == []

    async def test_no_pool_fails_loud(self):
        result = await ProbeMediaRenderLoopJob().run(None, _cfg())
        assert not result.ok
