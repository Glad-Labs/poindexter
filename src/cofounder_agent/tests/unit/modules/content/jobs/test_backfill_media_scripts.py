"""Unit tests for BackfillMediaScriptsJob (2026-08-15).

The scripts-side sibling of the shot-list backfill. Same harmlessness
invariants — a piece must never leave this job worse than it entered, and a
run that heals nothing must not look like progress — plus the two population
guards that keep it surgical: media-bearing pieces only, and never over an
operator-approved video.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from modules.content.jobs.backfill_media_scripts import (
    _STRANDED_SQL,
    BackfillMediaScriptsJob,
)
from modules.content.media_regen import RegenOutcome
from services.site_config import SiteConfig

_TASK = "11111111-2222-3333-4444-555555555555"


class _FakePool:
    def __init__(self, rows: list) -> None:
        self._rows = rows
        self.seen_batch: int | None = None

    async def fetch(self, _sql, *args):  # type: ignore[no-untyped-def]
        self.seen_batch = args[0] if args else None
        return self._rows


def _cfg(**over):
    return {"_site_config": SiteConfig(initial_config=dict(over))}


def _patch_deps(monkeypatch, *, outcome, platform=object()):
    monkeypatch.setattr(
        "services.di_wiring.build_platform_for_subprocess",
        lambda *a, **k: platform,
    )
    regen = AsyncMock(return_value=outcome)
    monkeypatch.setattr("modules.content.media_regen.regen_video_scripts", regen)
    return regen


@pytest.mark.unit
class TestBackfillMediaScripts:
    async def test_successful_regen_counts_and_emits(self, monkeypatch):
        findings: list[dict] = []
        monkeypatch.setattr(
            "modules.content.jobs.backfill_media_scripts.emit_finding",
            lambda **kw: findings.append(kw),
        )
        regen = _patch_deps(
            monkeypatch,
            outcome=RegenOutcome(
                ok=True, detail="long=420w short=60w shots=8+4",
                long_words=420, short_words=60, long_shots=8, short_shots=4,
            ),
        )
        pool = _FakePool([{"task_id": _TASK}])

        result = await BackfillMediaScriptsJob().run(pool, _cfg())

        assert result.ok and result.changes_made == 1
        assert regen.call_args.kwargs["apply"] is True
        assert len(findings) == 1
        assert findings[0]["kind"] == "media_scripts_backfilled"
        assert findings[0]["severity"] == "info"

    async def test_failed_regen_is_a_true_noop(self, monkeypatch):
        """GPU busy again → the core wrote nothing; the job must report zero
        progress and emit nothing."""
        findings: list[dict] = []
        monkeypatch.setattr(
            "modules.content.jobs.backfill_media_scripts.emit_finding",
            lambda **kw: findings.append(kw),
        )
        _patch_deps(
            monkeypatch,
            outcome=RegenOutcome(ok=False, detail="regen produced no video_long_script"),
        )

        result = await BackfillMediaScriptsJob().run(
            _FakePool([{"task_id": _TASK}]), _cfg(),
        )

        assert result.ok and result.changes_made == 0
        assert findings == []

    async def test_regen_exception_does_not_abort_the_batch(self, monkeypatch):
        monkeypatch.setattr(
            "modules.content.jobs.backfill_media_scripts.emit_finding", lambda **kw: None,
        )
        monkeypatch.setattr(
            "services.di_wiring.build_platform_for_subprocess", lambda *a, **k: object(),
        )
        calls: list[str] = []

        async def _boom_then_ok(task_id, **_kw):
            calls.append(task_id)
            if len(calls) == 1:
                raise RuntimeError("boom")
            return RegenOutcome(ok=True, detail="ok")

        monkeypatch.setattr(
            "modules.content.media_regen.regen_video_scripts", _boom_then_ok,
        )

        result = await BackfillMediaScriptsJob().run(
            _FakePool([{"task_id": "a" * 36}, {"task_id": "b" * 36}]),
            _cfg(backfill_media_scripts_batch="2"),
        )

        assert len(calls) == 2  # second piece still attempted
        assert result.changes_made == 1

    async def test_no_platform_handle_skips_loud(self, monkeypatch):
        regen = _patch_deps(monkeypatch, outcome=None, platform=None)

        result = await BackfillMediaScriptsJob().run(
            _FakePool([{"task_id": _TASK}]), _cfg(),
        )

        assert result.ok is False
        assert "platform" in result.detail
        regen.assert_not_called()

    async def test_no_stranded_pieces_reports_zero_metrics(self, monkeypatch):
        _patch_deps(monkeypatch, outcome=None)
        result = await BackfillMediaScriptsJob().run(_FakePool([]), _cfg())
        assert result.ok and result.metrics == {"backfilled": 0, "attempted": 0}

    async def test_disabled_via_settings(self, monkeypatch):
        regen = _patch_deps(monkeypatch, outcome=None)
        result = await BackfillMediaScriptsJob().run(
            _FakePool([{"task_id": _TASK}]),
            _cfg(backfill_media_scripts_enabled="false"),
        )
        assert result.ok and result.changes_made == 0
        regen.assert_not_called()

    async def test_batch_setting_threads_to_query(self, monkeypatch):
        _patch_deps(monkeypatch, outcome=RegenOutcome(ok=False, detail="skip"))
        pool = _FakePool([])

        await BackfillMediaScriptsJob().run(
            pool, _cfg(backfill_media_scripts_batch="3"),
        )

        assert pool.seen_batch == 3

    async def test_no_pool_fails_loud(self):
        result = await BackfillMediaScriptsJob().run(None, _cfg())
        assert result.ok is False


@pytest.mark.unit
class TestStrandedPopulation:
    """The SQL itself is exercised in integration; here pin the two guards
    the incident demands, as text contracts a refactor would have to face."""

    def test_requires_media_bearing_piece(self):
        # dev_diary pieces have no media nodes → no podcast_script → excluded
        assert "podcast_script', '') != ''" in _STRANDED_SQL

    def test_targets_empty_long_script(self):
        assert "video_long_script', '') = ''" in _STRANDED_SQL

    def test_never_touches_operator_approved_video(self):
        assert "ma.status = 'approved'" in _STRANDED_SQL
        assert "NOT EXISTS" in _STRANDED_SQL
