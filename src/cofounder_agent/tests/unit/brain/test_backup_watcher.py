"""Unit tests for brain/backup_watcher.py (Glad-Labs/poindexter#388).

Covers the two acceptance scenarios from the issue plus assorted edge
cases:

1. simulate kill → watcher restarts container → fresh dump appears →
   alert is auto-resolved (a status='resolved' alert_events row gets
   inserted).
2. simulate persistent failure → after N retries the watcher escalates
   (audit event, no further restarts), leaving the original firing
   alert in place for the dispatcher to page.

All external I/O (filesystem stats, subprocess, the asyncpg pool) is
mocked — no real ``docker restart`` runs and no test sleeps for real.
The pool is a ``MagicMock`` whose async methods are ``AsyncMock``s; we
seed app_settings reads via the ``setting_values`` dict passed to
``_make_pool``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional
from unittest.mock import AsyncMock, MagicMock

import pytest

# pythonpath in pyproject.toml includes "../.." so the brain package
# resolves the same way the migration_drift_probe tests import it.
from brain import backup_watcher as bw


# ---------------------------------------------------------------------------
# Helpers — pool builder + canned config
# ---------------------------------------------------------------------------


def _default_settings() -> dict[str, str]:
    """Match the migration's seed values."""
    return {
        bw.ENABLED_KEY: "true",
        bw.POLL_INTERVAL_MINUTES_KEY: "5",
        bw.HOURLY_MAX_AGE_MINUTES_KEY: "90",
        bw.DAILY_MAX_AGE_HOURS_KEY: "26",
        bw.MAX_RETRIES_KEY: "2",
        bw.RETRY_DELAY_SECONDS_KEY: "120",
        bw.BACKUP_DIR_KEY: "/tmp/poindexter-backups-test",
    }


def _make_pool(
    *,
    setting_values: Optional[dict[str, str]] = None,
    firing_alertnames: Optional[set[str]] = None,
    existing_fingerprints: Optional[set[str]] = None,
):
    """Build an asyncpg-style mock pool that:

    - returns ``setting_values[key]`` for ``SELECT value FROM app_settings``
      lookups (via ``fetchval``),
    - returns ``{"status": "firing"}`` for alert_events alertname lookups
      for names in ``firing_alertnames``, ``None`` otherwise (via
      ``fetchrow``),
    - returns ``{"?column?": 1}`` for alert_events fingerprint lookups
      for fingerprints in ``existing_fingerprints``, ``None`` otherwise
      (Glad-Labs/poindexter#444 sentinel dedup).
    - records every ``execute`` call so tests can assert on what was
      written (audit_log rows, status='resolved' alert_events rows).
    """
    pool = MagicMock()
    settings = {**_default_settings(), **(setting_values or {})}
    firing = firing_alertnames or set()
    fingerprints = existing_fingerprints or set()

    async def _fetchval(query, *args):
        # The watcher only ever issues
        # ``SELECT value FROM app_settings WHERE key = $1`` via fetchval.
        if "app_settings" in query and args:
            return settings.get(args[0])
        return None

    async def _fetchrow(query, *args):
        # Two callers — alertname lookup (latest status) and
        # fingerprint lookup (sentinel dedup, #444).
        if "alert_events" in query and args:
            if "fingerprint" in query:
                return {"?column?": 1} if args[0] in fingerprints else None
            alertname = args[0]
            if alertname in firing:
                return {"status": "firing"}
            return None
        return None

    pool.fetchval = AsyncMock(side_effect=_fetchval)
    pool.fetchrow = AsyncMock(side_effect=_fetchrow)
    pool.execute = AsyncMock()
    pool.fetch = AsyncMock(return_value=[])
    return pool


def _executed_alertnames(pool) -> list[str]:
    """Pull the alertname positional arg from every alert_events INSERT
    the watcher made on the pool. Used to assert auto-resolves wrote
    the right rows.
    """
    out: list[str] = []
    for call in pool.execute.call_args_list:
        sql = call.args[0]
        if "INSERT INTO alert_events" in sql:
            out.append(call.args[1])
    return out


def _executed_audit_events(pool) -> list[str]:
    """Pull every event_type written to audit_log by the watcher."""
    out: list[str] = []
    for call in pool.execute.call_args_list:
        sql = call.args[0]
        if "INSERT INTO audit_log" in sql:
            # event_type is positional arg 1 (after sql).
            out.append(call.args[1])
    return out


@pytest.fixture(autouse=True)
def _reset_module_state(tmp_path, monkeypatch):
    """Reset per-tier retry counters between tests.

    Also point ``DEFAULT_BACKUP_DIR`` at a tmp path so the
    "dir missing" guard never fires unintentionally — tests that need
    the missing-dir branch still set ``setting_values`` explicitly.
    """
    bw._reset_retry_state()
    # Make a real, empty backup dir so the existence guard passes for
    # tests that don't override BACKUP_DIR_KEY. _check_one_tier uses
    # the injected stat_fn for freshness, so the empty dir is fine.
    backup_root = tmp_path / "backups"
    (backup_root / "hourly").mkdir(parents=True)
    (backup_root / "daily").mkdir(parents=True)
    monkeypatch.setattr(bw, "DEFAULT_BACKUP_DIR", str(backup_root))
    yield str(backup_root)
    bw._reset_retry_state()


# ---------------------------------------------------------------------------
# Acceptance Test 1 — kill → restart → fresh dump → alert resolved
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestKillRestartResolves:
    @pytest.mark.asyncio
    async def test_stale_then_restart_brings_dump_back_resolves_alert(self, _reset_module_state):
        """The happy auto-recover path.

        Before the cycle starts there's a firing ``backup_hourly_failed``
        alert (the runner inserted it on a failed pg_dump). The
        watcher sees a stale dump, restarts the container, the next
        stat shows a fresh dump, and the watcher writes a
        ``status='resolved'`` alert_events row that the dispatcher
        will turn into a "[RESOLVED]" page.
        """
        pool = _make_pool(
            setting_values={bw.BACKUP_DIR_KEY: _reset_module_state},
            firing_alertnames={"backup_hourly_failed", "backup_daily_failed"},
        )

        # First stat call per tier → stale; second call (post-restart)
        # → fresh.
        stat_calls: list[str] = []
        # Use distinct iterators per tier so calls are independent.
        per_tier_responses: dict[str, list[Optional[float]]] = {
            "hourly": [10_000.0, 30.0],   # stale (>5400s threshold), then fresh
            "daily":  [200_000.0, 60.0],  # stale (>93600s threshold), then fresh
        }

        def fake_stat(_dir, tier):
            stat_calls.append(tier)
            return per_tier_responses[tier].pop(0)

        restart_calls: list[str] = []

        def fake_restart(container):
            restart_calls.append(container)
            return True, f"Restarted {container}"

        sleeps: list[float] = []

        def fake_sleep(seconds):
            sleeps.append(seconds)

        notify_calls: list[dict] = []

        def fake_notify(**kwargs):
            notify_calls.append(kwargs)

        summary = await bw.run_backup_watcher_probe(
            pool,
            stat_fn=fake_stat,
            restart_fn=fake_restart,
            sleep_fn=fake_sleep,
            notify_fn=fake_notify,
        )

        # Both tiers should have recovered.
        assert summary["ok"] is True, summary
        assert summary["status"] == "ok"
        assert summary["tiers"]["hourly"]["status"] == "recovered"
        assert summary["tiers"]["daily"]["status"] == "recovered"

        # Each tier's container was restarted exactly once.
        assert restart_calls == [
            "poindexter-backup-hourly",
            "poindexter-backup-daily",
        ]

        # The retry-delay sleep happened once per tier.
        assert sleeps == [120.0, 120.0]

        # A status='resolved' alert_events row was written for each
        # tier whose firing alert was outstanding.
        resolved = _executed_alertnames(pool)
        assert "backup_hourly_failed" in resolved
        assert "backup_daily_failed" in resolved

        # No operator notification — the existing dispatcher already
        # handled the original page; the watcher only emits its own
        # notify when the docker socket is broken.
        assert notify_calls == []

        # Audit log captured both recoveries.
        events = _executed_audit_events(pool)
        assert events.count("probe.backup_watcher_recovered") == 2

        # And the per-tier retry counter is back at zero so the next
        # cycle starts fresh.
        assert bw._retry_state == {"hourly": 0, "daily": 0}

    @pytest.mark.asyncio
    async def test_resolved_row_is_skipped_when_no_alert_is_firing(self, _reset_module_state):
        """If the runner never wrote a firing row (e.g. we recovered
        before the runner's next failure tick), the watcher must NOT
        synthesize a phantom resolved page. The retry counter still
        resets and the audit event still fires.
        """
        pool = _make_pool(
            setting_values={bw.BACKUP_DIR_KEY: _reset_module_state},
            firing_alertnames=set(),  # nothing firing
        )

        per_tier_responses: dict[str, list[Optional[float]]] = {
            "hourly": [10_000.0, 30.0],
            "daily":  [60.0, 60.0],  # was already fresh
        }

        def fake_stat(_dir, tier):
            return per_tier_responses[tier].pop(0)

        await bw.run_backup_watcher_probe(
            pool,
            stat_fn=fake_stat,
            restart_fn=lambda c: (True, f"Restarted {c}"),
            sleep_fn=lambda s: None,
            notify_fn=lambda **k: None,
        )

        resolved = _executed_alertnames(pool)
        assert resolved == [], (
            "Watcher should not write a resolved row when no firing alert exists; "
            f"got {resolved!r}"
        )


# ---------------------------------------------------------------------------
# Acceptance Test 2 — persistent failure escalates after N retries
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestPersistentFailureEscalates:
    @pytest.mark.asyncio
    async def test_max_retries_then_escalate(self, _reset_module_state):
        """Three consecutive cycles, dump never recovers.

        Cycle 1: stale → restart attempt 1, still stale (retries_used=1).
        Cycle 2: stale → restart attempt 2, still stale (retries_used=2).
        Cycle 3: stale, retries_used == max_retries (2) → escalate
                 (no more restarts, audit event written).

        The watcher leaves the original firing alert in place so the
        existing dispatcher continues paging on it — that's what
        "escalate" means in the issue's vocabulary.
        """
        pool = _make_pool(
            setting_values={
                bw.BACKUP_DIR_KEY: _reset_module_state,
                bw.MAX_RETRIES_KEY: "2",
            },
            firing_alertnames={"backup_hourly_failed"},
        )

        # Always return stale for hourly; fresh for daily so we focus
        # on the hourly escalation path.
        def fake_stat(_dir, tier):
            if tier == "hourly":
                return 10_000.0  # stale
            return 60.0  # fresh

        restart_calls: list[str] = []

        def fake_restart(container):
            restart_calls.append(container)
            return True, f"Restarted {container}"

        # Cycle 1
        s1 = await bw.run_backup_watcher_probe(
            pool,
            stat_fn=fake_stat,
            restart_fn=fake_restart,
            sleep_fn=lambda s: None,
            notify_fn=lambda **k: None,
        )
        assert s1["tiers"]["hourly"]["status"] == "retry_failed"
        assert s1["tiers"]["hourly"]["retries_used"] == 1
        assert restart_calls == ["poindexter-backup-hourly"]

        # Cycle 2
        s2 = await bw.run_backup_watcher_probe(
            pool,
            stat_fn=fake_stat,
            restart_fn=fake_restart,
            sleep_fn=lambda s: None,
            notify_fn=lambda **k: None,
        )
        assert s2["tiers"]["hourly"]["status"] == "retry_failed"
        assert s2["tiers"]["hourly"]["retries_used"] == 2
        # Two restarts now.
        assert restart_calls == [
            "poindexter-backup-hourly",
            "poindexter-backup-hourly",
        ]

        # Cycle 3 — we've burned through max_retries; expect escalate.
        s3 = await bw.run_backup_watcher_probe(
            pool,
            stat_fn=fake_stat,
            restart_fn=fake_restart,
            sleep_fn=lambda s: None,
            notify_fn=lambda **k: None,
        )
        assert s3["tiers"]["hourly"]["status"] == "escalated"
        assert s3["tiers"]["hourly"]["retries_used"] == 2
        # No new restart on the escalate cycle.
        assert restart_calls == [
            "poindexter-backup-hourly",
            "poindexter-backup-hourly",
        ]

        # The escalation cycle wrote a watcher_escalate audit event.
        events = _executed_audit_events(pool)
        assert "probe.backup_watcher_escalate" in events

        # The watcher never inserted a status='resolved' row — the
        # original firing alert remains the operator's signal.
        resolved = _executed_alertnames(pool)
        assert resolved == [], (
            "Watcher must NOT auto-resolve a persistent failure; "
            f"got resolved alertnames {resolved!r}"
        )


# ---------------------------------------------------------------------------
# Edge cases — disabled, missing dir, docker missing, exception isolation
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestEdgeCases:
    @pytest.mark.asyncio
    async def test_disabled_short_circuits_without_stat_or_restart(self, _reset_module_state):
        pool = _make_pool(
            setting_values={
                bw.BACKUP_DIR_KEY: _reset_module_state,
                bw.ENABLED_KEY: "false",
            },
        )

        stat_calls: list[str] = []
        restart_calls: list[str] = []

        def fake_stat(_dir, tier):
            stat_calls.append(tier)
            return 10_000.0

        def fake_restart(container):
            restart_calls.append(container)
            return True, "should not be called"

        summary = await bw.run_backup_watcher_probe(
            pool,
            stat_fn=fake_stat,
            restart_fn=fake_restart,
            sleep_fn=lambda s: None,
            notify_fn=lambda **k: None,
        )

        assert summary["status"] == "disabled"
        assert stat_calls == []
        assert restart_calls == []

    @pytest.mark.asyncio
    async def test_missing_backup_dir_notifies_operator_and_returns_degraded(self, _reset_module_state):
        # Override the backup_dir setting to a path that doesn't exist.
        # The watcher should call notify_fn (fail-loud) and return a
        # ``dir_missing`` summary without trying to stat or restart.
        bogus_dir = str(Path(_reset_module_state).parent / "does-not-exist-xyz")
        pool = _make_pool(setting_values={bw.BACKUP_DIR_KEY: bogus_dir})

        notify_calls: list[dict] = []

        def fake_notify(**kwargs):
            notify_calls.append(kwargs)

        summary = await bw.run_backup_watcher_probe(
            pool,
            stat_fn=lambda d, t: 30.0,  # would be fresh, but we never get there
            restart_fn=lambda c: (True, ""),
            sleep_fn=lambda s: None,
            notify_fn=fake_notify,
        )

        assert summary["ok"] is False
        assert summary["status"] == "dir_missing"
        assert len(notify_calls) == 1
        assert "backup directory" in notify_calls[0]["title"].lower()
        assert notify_calls[0]["severity"] == "warning"

        # An audit event captured the bad config.
        events = _executed_audit_events(pool)
        assert "probe.backup_watcher_dir_missing" in events

    @pytest.mark.asyncio
    async def test_docker_unavailable_notifies_and_counts_as_retry(self, _reset_module_state):
        """When the docker CLI isn't reachable the watcher can't
        recover anything. It must still consume a retry budget slot
        (otherwise it would loop forever) and surface the broken-docker
        condition via notify_operator so the operator knows the brain
        is degraded.
        """
        pool = _make_pool(
            setting_values={bw.BACKUP_DIR_KEY: _reset_module_state},
            firing_alertnames={"backup_hourly_failed"},
        )

        notify_calls: list[dict] = []

        def fake_notify(**kwargs):
            notify_calls.append(kwargs)

        # Stale forever; docker CLI is "missing".
        def fake_stat(_dir, tier):
            if tier == "hourly":
                return 10_000.0
            return 60.0

        def fake_restart(container):
            return False, "docker CLI not on PATH (brain image missing docker binary?)"

        summary = await bw.run_backup_watcher_probe(
            pool,
            stat_fn=fake_stat,
            restart_fn=fake_restart,
            sleep_fn=lambda s: None,
            notify_fn=fake_notify,
        )

        assert summary["tiers"]["hourly"]["status"] == "restart_failed"
        # The "docker CLI" branch fires its own notify.
        assert any(
            "cannot restart container" in (c.get("title") or "").lower()
            for c in notify_calls
        ), notify_calls

        # Retry counter advanced so the next cycle eventually escalates
        # rather than infinite-looping.
        assert bw._retry_state["hourly"] == 1

    @pytest.mark.asyncio
    async def test_one_tier_exception_does_not_kill_the_other(self, _reset_module_state):
        """A bug in the hourly check shouldn't take the daily check
        offline — each tier is wrapped in its own try/except inside the
        top-level probe.
        """
        pool = _make_pool(
            setting_values={bw.BACKUP_DIR_KEY: _reset_module_state},
        )

        def fake_stat(_dir, tier):
            if tier == "hourly":
                raise RuntimeError("synthetic hourly explosion")
            return 30.0  # daily is fresh

        summary = await bw.run_backup_watcher_probe(
            pool,
            stat_fn=fake_stat,
            restart_fn=lambda c: (True, ""),
            sleep_fn=lambda s: None,
            notify_fn=lambda **k: None,
        )

        assert summary["tiers"]["hourly"]["status"] == "exception"
        assert summary["tiers"]["daily"]["status"] == "fresh"
        # Overall ok=False because hourly failed; daily summary preserved.
        assert summary["ok"] is False


# ---------------------------------------------------------------------------
# Helper coverage
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestLatestDumpAge:
    def test_returns_none_when_dir_missing(self, tmp_path):
        assert bw._latest_dump_age_seconds(str(tmp_path / "missing"), "hourly") is None

    def test_returns_none_when_dir_empty(self, tmp_path):
        (tmp_path / "hourly").mkdir()
        assert bw._latest_dump_age_seconds(str(tmp_path), "hourly") is None

    def test_picks_newest_dump(self, tmp_path):
        tier_dir = tmp_path / "hourly"
        tier_dir.mkdir()
        # Create three dumps with different mtimes.
        import os as _os
        a = tier_dir / "poindexter_brain_20260101T000000Z.dump"
        b = tier_dir / "poindexter_brain_20260102T000000Z.dump"
        c = tier_dir / "poindexter_brain_20260103T000000Z.dump"
        for p in (a, b, c):
            p.write_bytes(b"x")
        _os.utime(a, (1_000_000.0, 1_000_000.0))
        _os.utime(b, (2_000_000.0, 2_000_000.0))
        _os.utime(c, (3_000_000.0, 3_000_000.0))

        age = bw._latest_dump_age_seconds(str(tmp_path), "hourly", now=3_000_500.0)
        assert age == 500.0

    def test_skips_tmp_partials(self, tmp_path):
        tier_dir = tmp_path / "hourly"
        tier_dir.mkdir()
        # A partial .tmp file should be ignored even if it's newest.
        partial = tier_dir / ".poindexter_brain_20260103T000000Z.dump.tmp"
        good = tier_dir / "poindexter_brain_20260102T000000Z.dump"
        partial.write_bytes(b"x")
        good.write_bytes(b"x")
        import os as _os
        _os.utime(partial, (3_000_000.0, 3_000_000.0))
        _os.utime(good, (2_000_000.0, 2_000_000.0))

        age = bw._latest_dump_age_seconds(str(tmp_path), "hourly", now=2_000_500.0)
        assert age == 500.0


@pytest.mark.unit
class TestProbeWrapper:
    @pytest.mark.asyncio
    async def test_probe_protocol_wrapper_returns_proberesult(self, _reset_module_state):
        pool = _make_pool(setting_values={bw.BACKUP_DIR_KEY: _reset_module_state})

        # Patch the probe entry point so the wrapper call is fast and
        # deterministic. We're only testing the adapter contract here —
        # not the full probe.
        async def fake_probe(_pool, **_kwargs):
            return {
                "ok": True,
                "status": "ok",
                "detail": "fake",
                "tiers": {
                    "hourly": {"status": "fresh"},
                    "daily": {"status": "fresh"},
                },
                "sentinels": {"status": "clean"},
            }

        import brain.backup_watcher as _bw_mod
        original = _bw_mod.run_backup_watcher_probe
        _bw_mod.run_backup_watcher_probe = fake_probe  # type: ignore[assignment]
        try:
            probe = bw.BackupWatcherProbe()
            result = await probe.check(pool, {})
        finally:
            _bw_mod.run_backup_watcher_probe = original  # type: ignore[assignment]

        assert result.ok is True
        assert result.detail == "fake"
        assert result.metrics["status"] == "ok"
        assert result.metrics["tiers"]["hourly"] == "fresh"
        assert result.metrics["sentinels"] == "clean"


# ---------------------------------------------------------------------------
# dr-backup sentinel surfacing — Glad-Labs/poindexter#444
# ---------------------------------------------------------------------------


def _write_sentinel(
    path: Path,
    *,
    rc: int = 1,
    ts: str = "2026-05-09T03:00:00Z",
    host: Optional[str] = "test-host",
    log_path: str = "/var/log/dr-backup/dr-backup.log",
    tail: str = "restic: error\nwhich tier: hourly",
) -> None:
    """Write a sentinel matching the dr-backup script format."""
    lines: list[str] = [f"rc={rc}", f"ts={ts}"]
    if host is not None:
        lines.append(f"host={host}")
    lines.append(f"log={log_path}")
    lines.append("tail<<EOF")
    lines.append(tail)
    lines.append("EOF")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


@pytest.mark.unit
class TestParseSentinel:
    def test_parses_full_format(self, tmp_path):
        sentinel = tmp_path / "dr-backup-failed.sentinel"
        _write_sentinel(
            sentinel,
            rc=2,
            ts="2026-05-09T03:00:00Z",
            host="test-host",
            log_path="/var/log/dr-backup.log",
            tail="boom\nbang",
        )
        parsed = bw._parse_sentinel_file(sentinel)
        assert parsed["rc"] == "2"
        assert parsed["ts"] == "2026-05-09T03:00:00Z"
        assert parsed["host"] == "test-host"
        assert parsed["log"] == "/var/log/dr-backup.log"
        assert parsed["tail"] == "boom\nbang"
        assert parsed["_path"] == str(sentinel)

    def test_tolerates_missing_host_field(self, tmp_path):
        # The hourly script omits ``host=`` — parser must not blow up.
        sentinel = tmp_path / "dr-backup-hourly-failed.sentinel"
        _write_sentinel(sentinel, host=None, tail="hourly tail")
        parsed = bw._parse_sentinel_file(sentinel)
        assert "host" not in parsed
        assert parsed["rc"] == "1"
        assert parsed["tail"] == "hourly tail"

    def test_returns_path_when_unreadable(self, tmp_path):
        # Pointing at a directory triggers OSError on read_text.
        result = bw._parse_sentinel_file(tmp_path)
        assert result == {"_path": str(tmp_path)}


@pytest.mark.unit
class TestScanSentinelDir:
    def test_returns_empty_when_dir_missing(self, tmp_path):
        assert bw._scan_sentinel_dir(str(tmp_path / "nope")) == []

    def test_finds_both_tier_sentinels(self, tmp_path):
        _write_sentinel(tmp_path / "dr-backup-failed.sentinel")
        _write_sentinel(tmp_path / "dr-backup-hourly-failed.sentinel", host=None)
        out = bw._scan_sentinel_dir(str(tmp_path))
        tiers = sorted(t for t, _, _ in out)
        assert tiers == ["daily", "hourly"]

    def test_skips_unrelated_files(self, tmp_path):
        (tmp_path / "some-other.log").write_text("noise", encoding="utf-8")
        assert bw._scan_sentinel_dir(str(tmp_path)) == []


@pytest.mark.unit
class TestSentinelAlertEmission:
    @pytest.mark.asyncio
    async def test_emits_firing_alert_with_ts_fingerprint(self, _reset_module_state, tmp_path):
        sentinel_dir = tmp_path / "logs"
        sentinel_dir.mkdir()
        _write_sentinel(
            sentinel_dir / "dr-backup-failed.sentinel",
            ts="2026-05-09T03:00:00Z",
        )
        pool = _make_pool(
            setting_values={
                bw.BACKUP_DIR_KEY: _reset_module_state,
                bw.SENTINEL_DIR_KEY: str(sentinel_dir),
            },
        )

        notify_calls: list[dict] = []

        def fake_notify(**kwargs):
            notify_calls.append(kwargs)

        # All tiers fresh so the only path under test is the sentinel scan.
        summary = await bw.run_backup_watcher_probe(
            pool,
            stat_fn=lambda d, t: 60.0,
            restart_fn=lambda c: (True, ""),
            sleep_fn=lambda s: None,
            notify_fn=fake_notify,
        )

        assert summary["sentinels"]["status"] == "sentinels_found"
        assert summary["ok"] is False
        # Exactly one INSERT INTO alert_events for the sentinel.
        inserted = _executed_alertnames(pool)
        assert inserted == ["dr_backup_daily_failed"], inserted
        # Audit event recorded.
        events = _executed_audit_events(pool)
        assert "probe.backup_watcher_sentinel_alert" in events
        # notify_fn fallback fired with the sentinel summary.
        assert any(
            "sentinel surfaced" in (c.get("title") or "").lower()
            or "dr-backup" in (c.get("title") or "").lower()
            for c in notify_calls
        ), notify_calls

    @pytest.mark.asyncio
    async def test_does_not_double_page_existing_fingerprint(
        self, _reset_module_state, tmp_path
    ):
        """Re-scanning the same sentinel must not insert a second alert.

        The script leaves the file in place until the next successful
        run; the brain probe runs every 5 minutes. Without dedup the
        operator would be paged on every cycle.
        """
        sentinel_dir = tmp_path / "logs"
        sentinel_dir.mkdir()
        _write_sentinel(
            sentinel_dir / "dr-backup-hourly-failed.sentinel",
            ts="2026-05-09T04:00:00Z",
            host=None,
        )
        # Pretend a row with the matching fingerprint already exists.
        pool = _make_pool(
            setting_values={
                bw.BACKUP_DIR_KEY: _reset_module_state,
                bw.SENTINEL_DIR_KEY: str(sentinel_dir),
            },
            existing_fingerprints={
                "dr-backup-sentinel-hourly-2026-05-09T04:00:00Z",
            },
        )

        summary = await bw.run_backup_watcher_probe(
            pool,
            stat_fn=lambda d, t: 60.0,
            restart_fn=lambda c: (True, ""),
            sleep_fn=lambda s: None,
            notify_fn=lambda **k: None,
        )

        # Sentinel was found, but no new alert row was inserted.
        assert summary["sentinels"]["status"] == "sentinels_found"
        assert _executed_alertnames(pool) == [], (
            "Watcher must dedup by fingerprint when re-scanning the "
            "same sentinel; got "
            f"{_executed_alertnames(pool)!r}"
        )
        # And no audit event for an emitted alert (since none was emitted).
        events = _executed_audit_events(pool)
        assert "probe.backup_watcher_sentinel_alert" not in events

    @pytest.mark.asyncio
    async def test_clean_dir_returns_clean_status(self, _reset_module_state, tmp_path):
        sentinel_dir = tmp_path / "logs"
        sentinel_dir.mkdir()
        pool = _make_pool(
            setting_values={
                bw.BACKUP_DIR_KEY: _reset_module_state,
                bw.SENTINEL_DIR_KEY: str(sentinel_dir),
            },
        )

        summary = await bw.run_backup_watcher_probe(
            pool,
            stat_fn=lambda d, t: 60.0,
            restart_fn=lambda c: (True, ""),
            sleep_fn=lambda s: None,
            notify_fn=lambda **k: None,
        )

        assert summary["sentinels"]["status"] == "clean"
        assert summary["sentinels"]["sentinels"] == []
        assert summary["ok"] is True

    @pytest.mark.asyncio
    async def test_missing_sentinel_dir_does_not_break_probe(
        self, _reset_module_state, tmp_path
    ):
        """If the operator forgot the bind mount, the probe should
        return ``dir_missing`` for sentinels but keep the per-tier
        check intact (don't let one degraded surface mask the other).
        """
        bogus_dir = str(tmp_path / "no-mount-here")
        pool = _make_pool(
            setting_values={
                bw.BACKUP_DIR_KEY: _reset_module_state,
                bw.SENTINEL_DIR_KEY: bogus_dir,
            },
        )

        summary = await bw.run_backup_watcher_probe(
            pool,
            stat_fn=lambda d, t: 60.0,
            restart_fn=lambda c: (True, ""),
            sleep_fn=lambda s: None,
            notify_fn=lambda **k: None,
        )

        assert summary["sentinels"]["status"] == "dir_missing"
        # dir_missing is reported as ok=True (informational) so the
        # absence of a mount doesn't keep the probe permanently
        # degraded — the per-tier age check still works.
        assert summary["sentinels"]["ok"] is True
        assert summary["ok"] is True
        assert summary["tiers"]["hourly"]["status"] == "fresh"

    @pytest.mark.asyncio
    async def test_falls_back_to_mtime_when_ts_missing(
        self, _reset_module_state, tmp_path
    ):
        """A malformed sentinel without ``ts=`` should still get a
        deterministic fingerprint (file mtime) so dedup still works.
        """
        sentinel_dir = tmp_path / "logs"
        sentinel_dir.mkdir()
        sentinel = sentinel_dir / "dr-backup-failed.sentinel"
        sentinel.write_text("rc=99\nhost=test-host\n", encoding="utf-8")
        # Pin the mtime so the generated fingerprint is predictable.
        import os as _os
        _os.utime(sentinel, (1_700_000_000.0, 1_700_000_000.0))

        expected_fp = "dr-backup-sentinel-daily-1700000000"
        pool = _make_pool(
            setting_values={
                bw.BACKUP_DIR_KEY: _reset_module_state,
                bw.SENTINEL_DIR_KEY: str(sentinel_dir),
            },
            existing_fingerprints={expected_fp},
        )

        await bw.run_backup_watcher_probe(
            pool,
            stat_fn=lambda d, t: 60.0,
            restart_fn=lambda c: (True, ""),
            sleep_fn=lambda s: None,
            notify_fn=lambda **k: None,
        )

        # mtime-derived fingerprint dedups exactly the same way.
        assert _executed_alertnames(pool) == []
