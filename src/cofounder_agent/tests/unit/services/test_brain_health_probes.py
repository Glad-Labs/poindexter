"""
Unit tests for brain/health_probes.py.

brain/ is standalone (stdlib + asyncpg). All external I/O is mocked:
asyncpg pool, urllib.request.urlopen, subprocess.run, shutil.disk_usage,
platform.system.

Covers happy paths, error paths, and edge cases for the probe scheduler,
HTTP helper, self-healer, and Gitea-issue deduplicator.
"""

from __future__ import annotations

import time
import urllib.error
from io import BytesIO
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from brain import health_probes as hp


def _make_pool():
    pool = MagicMock()
    pool.fetch = AsyncMock()
    pool.fetchrow = AsyncMock()
    pool.execute = AsyncMock()
    return pool


@pytest.fixture(autouse=True)
def _reset_module_state():
    # _created_issues was removed when the Gitea-issue auto-create helper
    # was deleted (Gitea decommissioned 2026-04-30).
    hp._last_run.clear()
    hp._failure_counts.clear()
    hp._last_remediation.clear()
    hp._config_synced = False
    yield
    hp._last_run.clear()
    hp._failure_counts.clear()
    hp._last_remediation.clear()
    hp._config_synced = False


@pytest.mark.unit
class TestIsDue:
    def test_returns_true_when_never_run(self):
        assert hp._is_due("db_ping") is True

    def test_returns_false_when_within_interval(self):
        hp._mark_run("db_ping")
        assert hp._is_due("db_ping") is False

    def test_returns_true_after_interval_elapsed(self):
        hp._last_run["db_ping"] = time.time() - 1000
        assert hp._is_due("db_ping") is True

    def test_unknown_probe_uses_default_interval(self):
        hp._mark_run("unknown_probe_name")
        assert hp._is_due("unknown_probe_name") is False


@pytest.mark.unit
class TestHttpJsonSuccess:
    def test_success_returns_parsed_body(self):
        resp = MagicMock()
        resp.read.return_value = b'{"a": 1}'
        with patch("urllib" + ".request.urlopen", return_value=resp):
            flag, result = hp._http_json("http://e.com")
        assert flag is True
        assert result["a"] == 1


@pytest.mark.unit
class TestHttpJsonErrors:
    def test_http_error_returns_error_dict(self):
        err = urllib.error.HTTPError(
            url="http://e.com",
            code=503,
            msg="Service Unavailable",
            hdrs={},
            fp=BytesIO(b""),
        )
        with patch("urllib" + ".request.urlopen", side_effect=err):
            flag, result = hp._http_json("http://e.com")
        assert flag is False
        assert "HTTP 503" in result["error"]

    def test_generic_exception_returns_error_dict(self):
        with patch("urllib" + ".request.urlopen", side_effect=RuntimeError("boom")):
            flag, result = hp._http_json("http://e.com")
        assert flag is False
        assert "boom" in result["error"]


@pytest.mark.unit
@pytest.mark.asyncio
class TestOllamaProbe:
    async def test_lists_loaded_models(self):
        resp = MagicMock()
        resp.read.return_value = b'{"models": [{"name": "qwen3:30b"}]}'
        with patch("urllib" + ".request.urlopen", return_value=resp):
            r = await hp.probe_ollama_models(_make_pool())
        assert r.get("ok") is True
        assert r.get("model_count") == 1

    async def test_empty_models_returns_not_ok(self):
        resp = MagicMock()
        resp.read.return_value = b'{"models": []}'
        with patch("urllib" + ".request.urlopen", return_value=resp):
            r = await hp.probe_ollama_models(_make_pool())
        assert r.get("ok") is False

    async def test_unreachable_returns_not_ok(self):
        with patch("urllib" + ".request.urlopen", side_effect=RuntimeError("down")):
            r = await hp.probe_ollama_models(_make_pool())
        assert r.get("ok") is False
        assert "unreachable" in r.get("detail", "")


@pytest.mark.unit
@pytest.mark.asyncio
class TestDiskSpaceProbe:
    async def test_plenty_free_is_ok(self):
        fake = MagicMock(total=1000, free=500)
        with patch("platform.system", return_value="Linux"), \
             patch("shutil.disk_usage", return_value=fake):
            r = await hp.probe_disk_space(None)
        assert r.get("ok") is True

    async def test_low_free_triggers_warning(self):
        fake = MagicMock(total=1_000_000_000, free=50_000_000)
        with patch("platform.system", return_value="Linux"), \
             patch("shutil.disk_usage", return_value=fake):
            r = await hp.probe_disk_space(None)
        assert r.get("ok") is False
        assert "low_drives" in r


@pytest.mark.unit
@pytest.mark.asyncio
class TestStuckProbe:
    async def test_no_stuck(self):
        p = _make_pool()
        p.fetch.return_value = []
        r = await hp.probe_stuck_tasks(p)
        assert r.get("ok") is True


@pytest.mark.unit
class TestRestartContainer:
    def test_success_returns_ok(self):
        fake = MagicMock(returncode=0, stderr="")
        with patch("subprocess.run", return_value=fake):
            flag, msg = hp._restart_container("svc")
        assert flag is True
        assert "svc" in msg

    def test_failure_returns_not_ok(self):
        fake = MagicMock(returncode=1, stderr="No such container")
        with patch("subprocess.run", return_value=fake):
            flag, msg = hp._restart_container("svc")
        assert flag is False
        assert "failed" in msg.lower()

    def test_subprocess_exception_returns_not_ok(self):
        with patch("subprocess.run", side_effect=FileNotFoundError("docker missing")):
            flag, msg = hp._restart_container("svc")
        assert flag is False
        assert "error" in msg


# TestCreateGiteaIssue removed 2026-05-03 alongside the underlying
# `_emit_finding` helper and `_created_issues` dedupe set —
# Gitea was decommissioned 2026-04-30, the auto-create paper trail
# went with it. Probe-failure escalation now goes only through
# `notify_operator` (Telegram + Discord) + `alert_events`.


@pytest.mark.unit
@pytest.mark.asyncio
class TestScheduledTasksProbe:
    async def test_skipped_on_non_windows(self):
        with patch("platform.system", return_value="Linux"):
            r = await hp.probe_scheduled_tasks(None)
        assert r.get("ok") is True
        assert "skipped" in r.get("detail", "")


@pytest.mark.unit
@pytest.mark.asyncio
class TestRunHealthProbes:
    async def test_skips_undue_probes(self):
        now = time.time()
        for name in hp.PROBES.keys():
            hp._last_run[name] = now
        hp._config_synced = True

        p = _make_pool()
        results = await hp.run_health_probes(p)
        assert results == {}
        p.execute.assert_not_called()


@pytest.fixture
def in_memory_otel_exporter():
    """Install an InMemorySpanExporter as the global OTel provider for
    the test, then restore. Module-scoped state is hard here because
    OTel refuses to override an already-set TracerProvider — so we
    install once, share, and clear spans between tests."""
    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
        InMemorySpanExporter,
    )

    exporter = InMemorySpanExporter()
    # Re-use an already-installed provider if there is one (subsequent
    # tests in the same process). Otherwise install ours.
    current = trace.get_tracer_provider()
    if isinstance(current, TracerProvider):
        provider = current
    else:
        provider = TracerProvider()
        trace.set_tracer_provider(provider)
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    exporter.clear()
    yield exporter
    exporter.clear()


@pytest.mark.unit
@pytest.mark.asyncio
class TestPerProbeSpans:
    """Issue #176 — each probe in run_health_probes gets its own
    brain.probe.<name> child span carrying probe.name, probe.duration_s,
    and probe.ok attributes. Verified via the OTel SDK
    InMemorySpanExporter so we don't need a live Tempo backend."""

    async def test_each_probe_emits_child_span_with_attributes(
        self, in_memory_otel_exporter,
    ):
        # Two fake probes — one passes, one fails — registered into
        # the PROBES dict so run_health_probes iterates them. Skip
        # _is_due / db writes / Telegram alerts to keep the assertion
        # surface tight.
        async def ok_probe(_pool):
            return {"ok": True, "detail": "all good"}

        async def fail_probe(_pool):
            return {"ok": False, "detail": "boom"}

        with patch.dict(
            hp.PROBES,
            {"fake_ok": ok_probe, "fake_fail": fail_probe},
            clear=True,
        ), \
            patch.object(hp, "_is_due", return_value=True):
            hp._config_synced = True
            p = _make_pool()
            await hp.run_health_probes(p, notify_fn=None)

        spans = {s.name: s for s in in_memory_otel_exporter.get_finished_spans()}
        assert "brain.probe.fake_ok" in spans
        assert "brain.probe.fake_fail" in spans

        ok_span = spans["brain.probe.fake_ok"]
        assert ok_span.attributes["probe.name"] == "fake_ok"
        assert ok_span.attributes["probe.ok"] is True
        assert "probe.duration_s" in ok_span.attributes
        assert ok_span.attributes["probe.duration_s"] >= 0

        fail_span = spans["brain.probe.fake_fail"]
        assert fail_span.attributes["probe.name"] == "fake_fail"
        assert fail_span.attributes["probe.ok"] is False
        assert "probe.duration_s" in fail_span.attributes

    async def test_probe_exception_still_closes_span_with_ok_false(
        self, in_memory_otel_exporter,
    ):
        """try/finally must end the span even when the probe raises —
        and the span should record probe.ok=false (since the result
        dict gets stamped {ok: False, detail: 'probe crashed: ...'})."""
        async def crashy_probe(_pool):
            raise RuntimeError("kaboom")

        with patch.dict(
            hp.PROBES, {"crashy": crashy_probe}, clear=True,
        ), \
            patch.object(hp, "_is_due", return_value=True):
            hp._config_synced = True
            p = _make_pool()
            await hp.run_health_probes(p, notify_fn=None)

        spans = {s.name: s for s in in_memory_otel_exporter.get_finished_spans()}
        assert "brain.probe.crashy" in spans
        crashy_span = spans["brain.probe.crashy"]
        assert crashy_span.attributes["probe.name"] == "crashy"
        assert crashy_span.attributes["probe.ok"] is False
        assert "probe.duration_s" in crashy_span.attributes


@pytest.mark.unit
@pytest.mark.asyncio
class TestConditionalSuppressionAndCrash:
    """#304 — PROMETHEUS_COVERED suppression is conditional on Alertmanager
    health, and probe CRASHES always page (distinct from service-down)."""

    async def _run_with(self, probe_fn, *, probe_name, am_healthy):
        hp._failure_counts.clear()
        notifies: list[str] = []
        with patch.dict(hp.PROBES, {probe_name: probe_fn}, clear=True), \
                patch.object(hp, "_is_due", return_value=True), \
                patch.object(hp, "ALERT_AFTER_FAILURES", 1), \
                patch.object(
                    hp, "_alertmanager_healthy",
                    new=AsyncMock(return_value=am_healthy),
                ):
            hp._config_synced = True
            await hp.run_health_probes(
                _make_pool(), notify_fn=lambda m: notifies.append(m)
            )
        return notifies

    async def test_covered_probe_suppressed_when_alertmanager_healthy(self):
        async def fail(_pool):
            return {"ok": False, "detail": "db down"}

        # db_ping IS in PROMETHEUS_COVERED_PROBES; Alertmanager healthy => Prom owns it.
        notifies = await self._run_with(fail, probe_name="db_ping", am_healthy=True)
        assert notifies == []  # suppressed — Prometheus/Alertmanager pages

    async def test_covered_probe_pages_when_alertmanager_down(self):
        async def fail(_pool):
            return {"ok": False, "detail": "db down"}

        notifies = await self._run_with(fail, probe_name="db_ping", am_healthy=False)
        assert len(notifies) == 1
        assert "Alertmanager is unreachable" in notifies[0]

    async def test_crash_always_pages_even_when_covered_and_am_healthy(self):
        async def crash(_pool):
            raise RuntimeError("probe bug")

        # Even a covered probe with healthy Alertmanager pages on a CRASH —
        # the monitoring code itself is broken, which Prometheus doesn't cover.
        notifies = await self._run_with(crash, probe_name="db_ping", am_healthy=True)
        assert len(notifies) == 1
        assert "ERRORED" in notifies[0]


@pytest.mark.unit
@pytest.mark.asyncio
class TestProbeTopicQuality:
    """probe_topic_quality attributes rejections to actual drivers.

    Before issue #235's fix, the probe reported "topics too low quality"
    even when 0% of tasks failed the quality threshold. The driver was
    actually semantic_dedup_rejected. These tests lock in the honest
    attribution.
    """

    async def _run_with_counts(self, total, rejected, low_quality, drivers=None):
        p = _make_pool()
        p.fetchrow.return_value = {
            "total": total, "rejected": rejected, "low_quality": low_quality,
        }
        driver_rows = [
            {"event_type": k, "n": v}
            for k, v in (drivers or {}).items()
        ]
        p.fetch.return_value = driver_rows
        return await hp.probe_topic_quality(p)

    async def test_returns_idle_when_no_tasks(self):
        r = await self._run_with_counts(total=0, rejected=0, low_quality=0)
        assert r["ok"] is True
        assert "idle" in r["detail"]

    async def test_healthy_when_rejection_rate_under_threshold(self):
        r = await self._run_with_counts(total=100, rejected=10, low_quality=2)
        assert r["ok"] is True
        assert "10% rejected" in r["detail"]
        # No suffix when healthy.
        assert "driver:" not in r["detail"]

    async def test_blames_semantic_dedup_when_that_is_the_driver(self):
        """The scenario from issue #235: 72% rejected, 0 low-quality,
        all rejections are semantic dedup hits."""
        r = await self._run_with_counts(
            total=148, rejected=107, low_quality=0,
            drivers={"semantic_dedup_rejected": 107},
        )
        assert r["ok"] is False
        assert "72% rejected" in r["detail"]
        assert "0% below 70" in r["detail"]
        assert "duplicate topics" in r["detail"]
        assert r["top_driver"] == "semantic_dedup_rejected"
        # Must not blame quality when low_quality_rate == 0.
        assert "topics too low quality" not in r["detail"]

    async def test_blames_qa_when_that_is_the_driver(self):
        r = await self._run_with_counts(
            total=50, rejected=25, low_quality=25,
            drivers={"qa_rejected": 25, "semantic_dedup_rejected": 3},
        )
        assert r["ok"] is False
        assert "QA threshold" in r["detail"]
        assert r["top_driver"] == "qa_rejected"

    async def test_detail_says_cause_unknown_when_no_drivers(self):
        r = await self._run_with_counts(total=100, rejected=80, low_quality=0)
        assert r["ok"] is False
        assert "cause unknown" in r["detail"]

    async def test_drivers_field_exposed_for_dashboards(self):
        r = await self._run_with_counts(
            total=100, rejected=60, low_quality=5,
            drivers={
                "semantic_dedup_rejected": 40,
                "qa_rejected": 15,
                "title_not_original": 5,
            },
        )
        assert r["drivers"]["semantic_dedup_rejected"] == 40
        assert r["drivers"]["qa_rejected"] == 15
        assert r["drivers"]["title_not_original"] == 5


@pytest.mark.unit
@pytest.mark.asyncio
class TestProbeCadenceSlo:
    """probe_cadence_slo pages when ACTUAL publish output falls below the
    operator-CONFIGURED cadence target (issue #525).

    The settings come from app_settings (a single ANY($1) fetch) and the
    actual count from the posts table. Both are mocked here.
    """

    def _settings_rows(self, **overrides):
        """Build the app_settings rows the probe's first fetch returns.

        Pass overrides like enabled='false' or expected='2' to tweak a key;
        omit a key entirely to exercise the probe's documented defaults.
        """
        defaults = {
            "cadence_slo_enabled": overrides.get("enabled", "true"),
            "cadence_slo_expected_posts_per_day": overrides.get("expected", "1"),
            "cadence_slo_window_hours": overrides.get("window", "24"),
            "cadence_slo_shortfall_ratio": overrides.get("ratio", "0.5"),
        }
        # Drop any key whose override is explicitly None (simulate missing row).
        return [
            {"key": k, "value": v}
            for k, v in defaults.items()
            if v is not None
        ]

    def _make_pool_with(self, actual, last=None, **overrides):
        p = _make_pool()
        p.fetch.return_value = self._settings_rows(**overrides)
        p.fetchrow.return_value = {"c": actual, "last_published": last}
        return p

    async def test_breach_fails_when_actual_below_threshold(self):
        # expected_for_window = 1 * (24/24) = 1; threshold = 0.5 * 1 = 0.5.
        # actual 0 < 0.5 → breach.
        p = self._make_pool_with(actual=0)
        r = await hp.probe_cadence_slo(p)
        assert r["ok"] is False
        assert "cadence SLO breach" in r["detail"]
        assert r["actual"] == 0
        assert r["expected_for_window"] == 1.0

    async def test_healthy_when_actual_meets_expected(self):
        # actual 1 >= threshold 0.5 → pass.
        p = self._make_pool_with(actual=1, last="2026-05-30 09:00:00")
        r = await hp.probe_cadence_slo(p)
        assert r["ok"] is True
        assert "cadence OK" in r["detail"]
        assert r["actual"] == 1

    async def test_disabled_skips_cleanly(self):
        p = self._make_pool_with(actual=0, enabled="false")
        r = await hp.probe_cadence_slo(p)
        assert r["ok"] is True
        assert r.get("status") == "disabled"
        # When disabled, the probe must not even query the posts table.
        p.fetchrow.assert_not_called()

    async def test_uses_defaults_when_settings_rows_missing(self):
        # No app_settings rows at all → defaults (1/day, 24h, 0.5) apply.
        # actual 0 < 0.5 threshold → breach with default-derived expectation.
        p = _make_pool()
        p.fetch.return_value = []
        p.fetchrow.return_value = {"c": 0, "last_published": None}
        r = await hp.probe_cadence_slo(p)
        assert r["ok"] is False
        assert r["expected_for_window"] == 1.0
        assert r["window_hours"] == 24.0

    async def test_higher_target_widens_breach_window(self):
        # expected 3/day over 24h → expected_for_window 3, threshold 1.5.
        # actual 1 < 1.5 → breach even though a post WAS published.
        p = self._make_pool_with(actual=1, last="2026-05-30 09:00:00", expected="3")
        r = await hp.probe_cadence_slo(p)
        assert r["ok"] is False
        assert r["expected_for_window"] == 3.0
        assert "target 3/day" in r["detail"]
