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


class _AcquireCM:
    """Stand-in for ``asyncpg.Pool.acquire()`` — an async context manager
    yielding one connection. Lets probe tests exercise the cross-process GPU
    advisory-lock gating (``async with pool.acquire() as conn``) without a
    live Postgres."""

    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, *_exc):
        return False


def _make_pool():
    pool = MagicMock()
    pool.fetch = AsyncMock()
    pool.fetchrow = AsyncMock()
    pool.execute = AsyncMock()
    # Connection handed out by ``pool.acquire()``. Its ``fetchval`` answers the
    # ``SELECT pg_try_advisory_lock(...)`` GPU-arbitration probe — default True
    # (GPU free) so probes that take the lock run as before. Tests exercising
    # the "GPU busy" skip override ``pool._lock_conn.fetchval``.
    conn = MagicMock()
    conn.fetchval = AsyncMock(return_value=True)
    conn.execute = AsyncMock()
    pool._lock_conn = conn
    pool.acquire = MagicMock(return_value=_AcquireCM(conn))
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
    async def test_fail_open_advisory_when_agent_unconfigured(self):
        # The "needs migration" hard-fail is gone (#704): the probe now asks the
        # host Recovery Agent (GET /tasks) which CAN see the host Task Scheduler.
        # When the agent URL/token aren't configured (or the pool can't be read),
        # the probe is advisory (ok:True) rather than fake-healthy OR chronically
        # failing — mirrors compose_drift's host-recover fall-through. Full
        # behavior lives in tests/unit/brain/test_scheduled_tasks_probe.py.
        pool = MagicMock()
        pool.fetchrow = AsyncMock(return_value=None)  # no settings rows
        r = await hp.probe_scheduled_tasks(pool)
        assert r.get("ok") is True
        assert "advisory" in r.get("detail", "")
        assert "needs migration" not in r.get("detail", "")


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
class TestAsyncNotifyFnAwaited:
    """Regression: run_health_probes must AWAIT an async notify_fn.

    ``brain_daemon.notify`` is async (since #344), and it is the notify_fn
    passed in production. run_health_probes was calling ``notify_fn(...)``
    bare, so a probe-failure page became a coroutine that was never awaited —
    ``RuntimeWarning: coroutine 'notify' was never awaited`` and the alert
    silently dropped. The suppression tests above never caught it because they
    pass a SYNC lambda. This mirrors the ``_maybe_await`` fix already shipped in
    business_probes / post_performance.
    """

    async def test_async_notify_fn_is_awaited_on_probe_failure(self):
        async def fail(_pool):
            return {"ok": False, "detail": "boom"}

        notify_fn = AsyncMock()
        # 'fake_fail' is NOT in PROMETHEUS_COVERED_PROBES, so it pages directly
        # (no suppression), and ALERT_AFTER_FAILURES=1 trips on this single run.
        with patch.dict(hp.PROBES, {"fake_fail": fail}, clear=True), \
                patch.object(hp, "_is_due", return_value=True), \
                patch.object(hp, "ALERT_AFTER_FAILURES", 1), \
                patch.object(
                    hp, "_alertmanager_healthy", new=AsyncMock(return_value=True),
                ):
            hp._config_synced = True
            await hp.run_health_probes(_make_pool(), notify_fn=notify_fn)

        # The page must have been *awaited*, not left as a dangling coroutine.
        assert notify_fn.await_count == 1, (
            "run_health_probes did not await the async notify_fn — the "
            "probe-failure page was dropped as an un-awaited coroutine "
            f"(call_count={notify_fn.call_count}, await_count={notify_fn.await_count})"
        )


@pytest.mark.unit
@pytest.mark.asyncio
class TestGpuTemperatureProbe:
    """#536 — the probe must distinguish 'exporter alive' from 'writing fresh
    data'. A stale newest row (frozen feed) with a normal temp must fail."""

    @staticmethod
    def _row(temp, age_min):
        from datetime import datetime, timedelta, timezone
        return {
            "temperature": temp,
            "timestamp": datetime.now(timezone.utc) - timedelta(minutes=age_min),
        }

    async def test_stale_feed_fails_even_with_normal_temp(self):
        p = _make_pool()
        # 1) newest gpu row: cool temp but 60min old; 2) staleness setting=15
        p.fetchrow = AsyncMock(side_effect=[self._row(45, 60), {"value": "15"}])
        r = await hp.probe_gpu_temperature(p)
        assert r["ok"] is False
        assert "STALE" in r["detail"]
        assert r["stale_minutes"] >= 15

    async def test_fresh_normal_temp_is_ok(self):
        p = _make_pool()
        # fresh row + staleness setting + threshold setting
        p.fetchrow = AsyncMock(side_effect=[self._row(45, 1), {"value": "15"}, {"value": "85"}])
        r = await hp.probe_gpu_temperature(p)
        assert r["ok"] is True
        assert r["temperature_c"] == 45

    async def test_fresh_hot_temp_fails_on_threshold(self):
        p = _make_pool()
        p.fetchrow = AsyncMock(side_effect=[self._row(92, 1), {"value": "15"}, {"value": "85"}])
        r = await hp.probe_gpu_temperature(p)
        assert r["ok"] is False
        assert "exceeds threshold" in r["detail"]

    async def test_no_rows_is_ok(self):
        p = _make_pool()
        p.fetchrow = AsyncMock(side_effect=[None])
        r = await hp.probe_gpu_temperature(p)
        assert r["ok"] is True
        assert "no gpu_metrics" in r["detail"]


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


@pytest.mark.unit
class TestStripOllamaPrefix:
    def test_strips_leading_ollama_prefix(self):
        assert hp._strip_ollama_prefix("ollama/gemma3:27b") == "gemma3:27b"

    def test_leaves_bare_model_untouched(self):
        assert hp._strip_ollama_prefix("gemma3:27b") == "gemma3:27b"

    def test_only_strips_leading_occurrence(self):
        # A model name that merely contains 'ollama/' mid-string is left alone.
        assert hp._strip_ollama_prefix("my-ollama/model") == "my-ollama/model"

    def test_empty_and_none_safe(self):
        assert hp._strip_ollama_prefix("") == ""
        assert hp._strip_ollama_prefix(None) == ""


@pytest.mark.unit
@pytest.mark.asyncio
class TestResolveContentGenModel:
    """The content-gen probe resolves its model dynamically from
    app_settings (writer → default), then /api/tags, then a safe literal —
    so it never 404s on an uninstalled hardcoded model (#228 follow-up)."""

    def _pool_with_settings(self, **values):
        """fetchval(query, key) → values[key]; missing keys return None."""
        p = _make_pool()

        async def _fv(_query, *args):
            key = args[0] if args else None
            return values.get(key)

        p.fetchval = AsyncMock(side_effect=_fv)
        return p

    async def test_uses_writer_model_and_strips_prefix(self):
        # pipeline_writer_model carries a LiteLLM ollama/ prefix → stripped.
        p = self._pool_with_settings(pipeline_writer_model="ollama/glm-4.7-5090:latest")
        model = await hp._resolve_content_gen_model(p)
        assert model == "glm-4.7-5090:latest"

    async def test_falls_back_to_default_ollama_model(self):
        p = self._pool_with_settings(
            pipeline_writer_model="", default_ollama_model="gemma3:27b"
        )
        model = await hp._resolve_content_gen_model(p)
        assert model == "gemma3:27b"

    async def test_falls_back_to_first_installed_non_embedding_model(self):
        # Neither setting set → probe asks /api/tags and skips embedders.
        p = self._pool_with_settings()
        tags = MagicMock()
        tags.read.return_value = (
            b'{"models": [{"name": "nomic-embed-text:latest"},'
            b' {"name": "phi4:14b"}]}'
        )
        with patch("urllib" + ".request.urlopen", return_value=tags):
            model = await hp._resolve_content_gen_model(p)
        assert model == "phi4:14b"  # embedder skipped

    async def test_final_literal_when_nothing_resolves(self):
        # No settings, /api/tags unreachable → safe literal.
        p = self._pool_with_settings()
        with patch("urllib" + ".request.urlopen", side_effect=RuntimeError("down")):
            model = await hp._resolve_content_gen_model(p)
        assert model == hp._CONTENT_GEN_FALLBACK_MODEL


@pytest.mark.unit
@pytest.mark.asyncio
class TestProbeContentGen:
    """probe_content_gen exercises the resolved (installed) model rather
    than a hardcoded one, and surfaces the model it used."""

    def _pool_with_writer(self, writer):
        p = _make_pool()

        async def _fv(_query, *args):
            key = args[0] if args else None
            return {"pipeline_writer_model": writer}.get(key)

        p.fetchval = AsyncMock(side_effect=_fv)
        return p

    async def test_resolves_installed_model_and_generates(self):
        p = self._pool_with_writer("ollama/glm-4.7-5090:latest")
        resp = MagicMock()
        resp.read.return_value = b'{"response": "FastAPI is a Python web framework."}'
        with patch("urllib" + ".request.urlopen", return_value=resp):
            r = await hp.probe_content_gen(p)
        assert r["ok"] is True
        # The ollama/ prefix must be stripped before hitting /api/generate.
        assert r["model"] == "glm-4.7-5090:latest"
        assert r["response_length"] > 0

    async def test_generate_failure_reports_model(self):
        p = self._pool_with_writer("gemma3:27b")
        with patch("urllib" + ".request.urlopen", side_effect=RuntimeError("404")):
            r = await hp.probe_content_gen(p)
        assert r["ok"] is False
        assert r["model"] == "gemma3:27b"
        assert "gemma3:27b" in r["detail"]


@pytest.mark.unit
@pytest.mark.asyncio
class TestProbeContentGenGpuLock:
    """probe_content_gen must yield the GPU to active renders / LLM jobs.

    Exercising the writer loads the ~19GB model into VRAM. Firing during a
    media render (wan + SDXL already near the 32GB ceiling) oversubscribes the
    GPU → SDXL CUDA-OOM → degraded video (observed 2026-06-21). The brain runs
    in its own stdlib+asyncpg container and can't import
    ``services.gpu_scheduler``, but it shares Postgres, so it takes the SAME
    cross-process advisory lock NON-BLOCKINGLY: ``pg_try_advisory_lock(
    GPU_ADVISORY_LOCK_KEY)``. Lock held → skip this cycle with a non-alerting
    status (NOT writer-down — that would fire a false Ollama/writer page). Lock
    free → run, then release on the same connection.
    """

    def _pool(self, *, lock_free, writer="gemma3:27b"):
        p = _make_pool()

        async def _fv(_query, *args):
            key = args[0] if args else None
            return {"pipeline_writer_model": writer}.get(key)

        # Settings resolution reads via the POOL; the advisory lock reads via
        # the acquired CONNECTION — distinct objects, set independently.
        p.fetchval = AsyncMock(side_effect=_fv)
        p._lock_conn.fetchval = AsyncMock(return_value=lock_free)
        return p

    async def test_skips_without_loading_writer_when_lock_held(self):
        p = self._pool(lock_free=False)
        with patch("urllib" + ".request.urlopen") as urlopen:
            r = await hp.probe_content_gen(p)
        # Non-alerting skip — must NOT report the writer as down.
        assert r["ok"] is True
        assert r.get("status") == "skipped_gpu_busy"
        # The ~19GB writer was NOT loaded: /api/generate never called.
        urlopen.assert_not_called()
        # Never acquired the lock → must not release someone else's.
        assert not [
            c for c in p._lock_conn.execute.await_args_list
            if "pg_advisory_unlock" in c.args[0]
        ]

    async def test_probes_lock_with_shared_gpu_key(self):
        p = self._pool(lock_free=False)
        with patch("urllib" + ".request.urlopen"):
            await hp.probe_content_gen(p)
        call = p._lock_conn.fetchval.await_args
        assert "pg_try_advisory_lock" in call.args[0]
        # Same int64 key the worker's GPUScheduler holds (kept in sync by value).
        assert call.args[1] == hp.GPU_ADVISORY_LOCK_KEY == 7_777_777_777

    async def test_runs_and_unlocks_when_lock_free(self):
        p = self._pool(lock_free=True, writer="gemma3:27b")
        resp = MagicMock()
        resp.read.return_value = (
            b'{"response": "FastAPI is a modern Python web framework for APIs."}'
        )
        with patch("urllib" + ".request.urlopen", return_value=resp):
            r = await hp.probe_content_gen(p)
        assert r["ok"] is True
        assert r["model"] == "gemma3:27b"
        # Released the lock on the SAME connection, with the shared key.
        unlocks = [
            c for c in p._lock_conn.execute.await_args_list
            if "pg_advisory_unlock" in c.args[0]
        ]
        assert len(unlocks) == 1
        assert unlocks[0].args[1] == hp.GPU_ADVISORY_LOCK_KEY

    async def test_unlocks_even_if_probe_work_raises(self):
        # An advisory-lock leak would block the worker's real GPU scheduler,
        # so the release MUST live in a finally.
        p = self._pool(lock_free=True)
        with patch.object(
            hp, "_resolve_content_gen_model",
            new=AsyncMock(side_effect=RuntimeError("boom")),
        ):
            with pytest.raises(RuntimeError):
                await hp.probe_content_gen(p)
        unlocks = [
            c for c in p._lock_conn.execute.await_args_list
            if "pg_advisory_unlock" in c.args[0]
        ]
        assert len(unlocks) == 1
