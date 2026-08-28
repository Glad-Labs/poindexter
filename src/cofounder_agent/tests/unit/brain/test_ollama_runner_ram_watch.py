"""Ollama runner host-RAM recycle watch (brain/ollama_runner_ram_watch.py).

The sibling of sidecar_ram_watch that reaches what it cannot: ollama runs as a
HOST systemd unit here, so cadvisor never sees it and `docker restart` cannot
touch it. Measured 2026-08-28 (poindexter#3434) — the llama-server runner leaks
~6.9 MiB per request, dead linear; a fresh runner holds 0.30 GB and one that
had served ~5.5h held 9.35 GB, the single largest holder of host swap.

The dangerous failure is the same as its sibling's: a FALSE IDLE, recycling a
runner mid-request and costing a QA rail its answer (plus an ~85s reload).
Most of these tests are about that.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest
from brain import ollama_runner_ram_watch as ow


class _Pool:
    """Minimal asyncpg-pool stand-in: app_settings reads + audit_log writes."""

    def __init__(self, settings: dict | None = None, gpu_lock_held: bool = False):
        self._settings = settings or {}
        self._gpu_lock_held = gpu_lock_held
        self.execute = AsyncMock()

    async def fetchval(self, sql: str, *args):
        if "pg_locks" in sql:
            return self._gpu_lock_held
        key = args[0] if args else None
        return self._settings.get(key)


ENABLED = {
    ow.ENABLED_KEY: "true",
    ow.TARGETS_KEY: "ollama-vision.service|4|http://h:11435|qwen3-vl:30b",
}


def _run(coro):
    return asyncio.run(coro)


def _summary(pool, **kw):
    kw.setdefault("gpu_lock_fn", AsyncMock(return_value=False))
    kw.setdefault(
        "mem_fn",
        lambda url: {"ollama-vision.service": {"anon_gb": 9.0, "cpu_percent": 0.0}},
    )
    kw.setdefault("recycle_fn", lambda e, m: (True, "unloaded and re-pinned"))
    kw.setdefault("now_fn", lambda: 10_000.0)
    return _run(ow.run_ollama_runner_ram_watch_probe(pool, **kw))


@pytest.fixture(autouse=True)
def _clean_state():
    ow._reset_recycle_state()
    yield
    ow._reset_recycle_state()


class TestTargetParsing:
    """`unit:watermark:endpoint:model` — both tail fields carry their own
    colons, which a naive split(':') destroys."""

    def test_keeps_the_url_scheme_and_the_model_tag_intact(self):
        parsed = ow.parse_targets("ollama-vision.service|4|http://h:11435|qwen3-vl:30b")
        assert parsed == [
            ("ollama-vision.service", 4.0, "http://h:11435", "qwen3-vl:30b")
        ]

    def test_shipped_default_parses(self):
        """The default is a real target, not an illustration — if it stops
        parsing, the probe silently watches nothing."""
        assert len(ow.parse_targets(ow.DEFAULT_TARGETS)) == 1

    @pytest.mark.parametrize(
        "raw", ["ollama-vision.service|4", "u|notanumber|http://h:1|m", "", "|||"]
    )
    def test_malformed_entries_are_skipped_not_fatal(self, raw):
        assert ow.parse_targets(raw) == []

    def test_a_bad_entry_does_not_take_down_its_neighbours(self):
        parsed = ow.parse_targets("junk, ollama-vision.service|4|http://h:11435|m:1")
        assert [p[0] for p in parsed] == ["ollama-vision.service"]


class TestMetricParsing:
    def test_reads_anon_and_cpu_per_unit(self):
        text = (
            '# HELP ollama_runner_anon_bytes x\n'
            'ollama_runner_anon_bytes{unit="ollama-vision.service"} 10737418240\n'
            'ollama_runner_cpu_percent{unit="ollama-vision.service"} 3.5\n'
        )
        stats = ow.parse_runner_stats(text)
        assert stats["ollama-vision.service"]["anon_gb"] == pytest.approx(10.0)
        assert stats["ollama-vision.service"]["cpu_percent"] == pytest.approx(3.5)

    def test_missing_cpu_is_absent_not_zero(self):
        """CPU is a rate and is absent on the exporter's first scrape. Defaulting
        it to 0.0 would read as 'idle' and let the probe recycle a busy runner
        during that window."""
        text = 'ollama_runner_anon_bytes{unit="u"} 1073741824\n'
        assert "cpu_percent" not in ow.parse_runner_stats(text)["u"]


class TestIdleGating:
    """A false idle costs a live QA rail its answer plus an ~85s reload."""

    def test_recycles_when_over_watermark_and_idle(self):
        out = _summary(_Pool(ENABLED))
        assert out["status"] == "recycled"
        assert out["anon_gb"] == 9.0

    def test_defers_while_a_gpu_session_holds_the_lock(self):
        # gpu_lock_fn must be passed explicitly: _summary defaults it, so the
        # pool's own value would never reach the probe.
        out = _summary(_Pool(ENABLED), gpu_lock_fn=AsyncMock(return_value=True))
        assert out["status"] == "deferred"
        assert "scheduler lock" in out["detail"]

    def test_defers_when_the_lock_state_is_unknown(self):
        """Unprovable counts as busy (#3094) — an unreadable gate must never
        be read as permission."""
        out = _summary(_Pool(ENABLED), gpu_lock_fn=AsyncMock(return_value=None))
        assert out["status"] == "deferred"

    def test_defers_when_the_runner_cpu_is_busy(self):
        out = _summary(
            _Pool(ENABLED),
            mem_fn=lambda url: {
                "ollama-vision.service": {"anon_gb": 9.0, "cpu_percent": 80.0}
            },
        )
        assert out["status"] == "deferred"
        assert "CPU" in out["detail"]

    def test_defers_when_cpu_is_unknown(self):
        out = _summary(
            _Pool(ENABLED),
            mem_fn=lambda url: {"ollama-vision.service": {"anon_gb": 9.0}},
        )
        assert out["status"] == "deferred"
        assert "CPU unknown" in out["detail"]

    def test_cpu_gate_is_not_redundant_with_the_gpu_lock(self):
        """Requests that never take the GPU scheduler lock (a warm-up ping, a
        direct curl) still peg the runner. With the lock free and CPU busy the
        probe must still defer, or the second gate is decoration."""
        out = _summary(
            _Pool(ENABLED, gpu_lock_held=False),
            mem_fn=lambda url: {
                "ollama-vision.service": {"anon_gb": 9.0, "cpu_percent": 95.0}
            },
        )
        assert out["status"] == "deferred"


class TestWatermarkAndCooldown:
    def test_under_watermark_does_nothing(self):
        out = _summary(
            _Pool(ENABLED),
            mem_fn=lambda url: {
                "ollama-vision.service": {"anon_gb": 0.3, "cpu_percent": 0.0}
            },
        )
        assert out["status"] == "under_watermark"

    def test_a_fresh_runner_is_under_the_shipped_watermark(self):
        """0.30 GB measured on a fresh runner vs a 4 GB default watermark. If a
        change ever puts the watermark below a fresh runner, the probe would
        recycle in a loop and never converge."""
        watermark = ow.parse_targets(ow.DEFAULT_TARGETS)[0][1]
        assert watermark > 0.30

    def test_the_incident_footprint_would_have_tripped(self):
        """9.35 GB was the measured 2026-08-28 footprint. A watermark above it
        would mean shipping a probe that watches the incident it was written
        for (the stable-audio lesson from 2026-08-27)."""
        assert ow.parse_targets(ow.DEFAULT_TARGETS)[0][1] < 9.35

    def test_cooldown_blocks_a_second_recycle(self):
        pool = _Pool({**ENABLED, ow.COOLDOWN_MINUTES_KEY: "120"})
        assert _summary(pool, now_fn=lambda: 10_000.0)["status"] == "recycled"
        again = _summary(pool, now_fn=lambda: 10_060.0)
        assert again["status"] == "under_watermark"
        assert "cooldown" in again["detail"]

    def test_cooldown_expires(self):
        pool = _Pool({**ENABLED, ow.COOLDOWN_MINUTES_KEY: "10"})
        assert _summary(pool, now_fn=lambda: 10_000.0)["status"] == "recycled"
        assert _summary(pool, now_fn=lambda: 10_000.0 + 601)["status"] == "recycled"


class TestFailureModes:
    def test_disabled_by_default_setting(self):
        out = _summary(_Pool({}))
        assert out["status"] == "disabled"

    def test_ships_disabled(self):
        """Other installs run ollama differently; restarting someone's LLM
        endpoint uninvited is a bad default."""
        assert ow.DEFAULT_ENABLED is False

    def test_unreachable_exporter_recycles_nothing(self):
        """Cannot tell a healthy runner from a leaking one — so do nothing, and
        say so, rather than treat 'no data' as 'no problem'."""
        out = _summary(_Pool(ENABLED), mem_fn=lambda url: None)
        assert out["ok"] is False
        assert out["status"] == "exporter_unreachable"

    def test_absent_unit_is_not_an_error(self):
        """No runner = the model is not loaded = nothing to reclaim."""
        out = _summary(_Pool(ENABLED), mem_fn=lambda url: {})
        assert out["status"] == "under_watermark"

    def test_recycle_failure_is_reported_and_not_stamped(self):
        pool = _Pool(ENABLED)
        out = _summary(pool, recycle_fn=lambda e, m: (False, "connection refused"))
        assert out["ok"] is False
        assert out["status"] == "recycle_failed"
        # A failed attempt must not start the cooldown, or a broken endpoint
        # would be retried once every two hours instead of every cycle.
        assert "ollama-vision.service" not in ow._last_recycle_monotonic

    def test_only_the_fattest_is_recycled_per_cycle(self):
        pool = _Pool(
            {
                **ENABLED,
                ow.TARGETS_KEY: (
                    "ollama-vision.service|4|http://h:11435|m:1,"
                    "ollama-primary.service|4|http://h:11434|m:2"
                ),
            }
        )
        out = _summary(
            pool,
            mem_fn=lambda url: {
                "ollama-vision.service": {"anon_gb": 9.0, "cpu_percent": 0.0},
                "ollama-primary.service": {"anon_gb": 12.0, "cpu_percent": 0.0},
            },
        )
        assert out["unit"] == "ollama-primary.service"


class TestUrlSchemeGuard:
    """Both URLs come from app_settings. urlopen honours file:// and friends,
    so a typo would silently read a local file and parse it as Prometheus text
    (or POST a recycle at it) instead of failing loudly."""

    @pytest.mark.parametrize("url", ["http://h:9835/metrics", "https://h/metrics"])
    def test_http_urls_pass(self, url):
        assert ow._require_http_url(url, "x") == url

    @pytest.mark.parametrize(
        "url", ["file:///etc/passwd", "ftp://h/x", "/just/a/path", ""]
    )
    def test_other_schemes_are_rejected(self, url):
        with pytest.raises(ValueError):
            ow._require_http_url(url, "x")

    def test_a_bad_exporter_url_is_a_clean_no_recycle(self):
        """The guard must surface as 'unreachable' — nothing recycled — rather
        than propagating and killing the whole probe cycle."""
        pool = _Pool({**ENABLED, ow.EXPORTER_URL_KEY: "file:///etc/passwd"})
        out = _run(
            ow.run_ollama_runner_ram_watch_probe(
                pool,
                gpu_lock_fn=AsyncMock(return_value=False),
                recycle_fn=lambda e, m: (True, "ok"),
                now_fn=lambda: 1.0,
            )
        )
        assert out["status"] == "exporter_unreachable"
        assert out["ok"] is False
