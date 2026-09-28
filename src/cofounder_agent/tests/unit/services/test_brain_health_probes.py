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
from datetime import UTC, datetime, timedelta
from io import BytesIO
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from poindexter.brain import docker_utils, probe_failure_state, probe_schedule
from poindexter.brain import health_probes as hp
from poindexter.brain import probe_severity as ps
from poindexter.brain.probe_failure_state import ProbeFailureState
from poindexter.brain.probe_schedule import ProbeSchedule


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
def _reset_module_state(monkeypatch):
    # A fresh probe schedule, failure counts and self-heal cooldowns per test,
    # as in a newly started brain.
    monkeypatch.setattr(probe_schedule, "schedule", ProbeSchedule())
    monkeypatch.setattr(probe_failure_state, "state", ProbeFailureState())


@pytest.fixture(autouse=True)
def _no_live_alertmanager(monkeypatch):
    """``run_health_probes`` asks Alertmanager whether it can deliver
    (``GET alertmanager:9093/-/healthy``) once per cycle. Unstubbed, every test
    that runs a cycle made that request, and on the self-hosted CI runner the
    name resolves to the PRODUCTION Alertmanager. The egress guard now refuses
    compose service names for every unit test.

    False, meaning unreachable, is what these tests always got on a developer
    host, where the name does not resolve. It is also the probe's conservative
    branch: covered probes are not suppressed. The tests that turn on
    Alertmanager's state re-patch it, and the innermost patch wins.
    """
    monkeypatch.setattr(hp, "_alertmanager_healthy", AsyncMock(return_value=False))


@pytest.mark.unit
class TestIsDue:
    def test_returns_true_when_never_run(self):
        assert hp._is_due("db_ping") is True

    async def test_returns_false_when_within_interval(self):
        await hp._mark_run(_make_pool(), "db_ping")
        assert hp._is_due("db_ping") is False

    def test_returns_true_after_interval_elapsed(self):
        probe_schedule.schedule.last_run["db_ping"] = time.time() - 1000
        assert hp._is_due("db_ping") is True

    async def test_unknown_probe_uses_default_interval(self):
        await hp._mark_run(_make_pool(), "unknown_probe_name")
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


def _restart_outcome(
    ok: bool, detail: str, *, status: str | None = None, container: str = "poindexter-grafana",
) -> docker_utils.ContainerRestart:
    """What a stubbed ``docker_utils.restart_container`` returns."""
    if status is None:
        status = docker_utils.RESTART_OK if ok else docker_utils.RESTART_FAILED
    return docker_utils.ContainerRestart(container, status, detail, 90)


class _StatefulDocker:
    """A ``subprocess.run`` stand-in for a docker whose container starts when
    it is restarted: ``docker inspect`` answers with its start time, and
    ``docker restart`` moves that to now. Every argv is recorded."""

    def __init__(self, *, started_seconds_ago: float) -> None:
        self.started = datetime.now(UTC) - timedelta(seconds=started_seconds_ago)
        self.argvs: list[list[str]] = []

    def __call__(self, argv, **_kwargs):
        self.argvs.append(list(argv))
        if argv[1] == "inspect":
            stamp = self.started.strftime("%Y-%m-%dT%H:%M:%S.%f") + "000Z"
            return MagicMock(returncode=0, stdout=f"running {stamp}\n", stderr="")
        self.started = datetime.now(UTC)
        return MagicMock(returncode=0, stdout="", stderr="")


@pytest.mark.unit
def test_the_self_heal_restarts_through_the_shared_helper():
    assert hp._restart_container is docker_utils.restart_container


@pytest.mark.unit
@pytest.mark.asyncio
class TestSelfHealRestart:
    """The REMEDIATIONS container restarts go through the brain's one
    ``docker restart`` implementation (``docker_utils.restart_container``,
    tested in ``tests/unit/brain/test_docker_utils_restart.py``). Before it,
    this self-heal ran its own: a hardcoded 60 s timeout, under the worker's
    75 s stop grace, and no inspect pre-check. These pin what
    ``_try_remediation`` does with each outcome."""

    async def test_the_pool_is_passed_so_the_timeout_knob_applies(self):
        pool = _make_pool()
        restart = AsyncMock(return_value=_restart_outcome(True, "restarted poindexter-grafana"))

        with patch.object(hp, "_restart_container", new=restart):
            await hp._try_remediation(
                "grafana_datasources", {"detail": "datasource broken"}, MagicMock(), pool=pool,
            )

        restart.assert_awaited_once_with("poindexter-grafana", pool=pool)

    async def test_real_helper_inspects_first_and_waits_the_db_timeout(self):
        """End to end through ``docker_utils`` with only ``subprocess.run``
        faked: inspect, then restart on the knob's timeout (was 60 s)."""
        pool = _make_pool()
        pool.fetchval = AsyncMock(return_value="120")
        run = MagicMock(side_effect=[
            MagicMock(returncode=0, stdout="running 2026-01-01T00:00:00.000000000Z\n", stderr=""),
            MagicMock(returncode=0, stdout="", stderr=""),
        ])
        notices: list[str] = []

        with patch.object(docker_utils.subprocess, "run", run):
            await hp._try_remediation(
                "grafana_datasources", {"detail": "datasource broken"},
                MagicMock(), pool=pool, info_fn=notices.append,
            )

        argvs = [c.args[0] for c in run.call_args_list]
        assert argvs[0][:2] == ["docker", "inspect"]
        assert argvs[1] == ["docker", "restart", "poindexter-grafana"]
        assert run.call_args_list[1].kwargs["timeout"] == 120
        assert len(notices) == 1 and "restarted poindexter-grafana" in notices[0]

    async def test_missing_container_sends_nothing_but_keeps_its_cooldown(self):
        """Mid-recreate there is nothing to restart and nothing to report.
        The cooldown stamped before the attempt still stands, so the next
        attempt waits it out instead of landing on the container that was
        just recreated."""
        pages: list[str] = []
        notices: list[str] = []
        restart = AsyncMock(return_value=_restart_outcome(
            False, "container poindexter-grafana not found (likely mid-recreate)",
            status=docker_utils.RESTART_MISSING,
        ))

        with patch.object(hp, "_restart_container", new=restart):
            for _ in range(2):
                await hp._try_remediation(
                    "grafana_datasources", {"detail": "datasource broken"},
                    pages.append, pool=_make_pool(), info_fn=notices.append,
                )

        assert pages == [] and notices == []
        assert restart.await_count == 1
        assert not probe_failure_state.state.remediation_due(
            "grafana_datasources", hp.REMEDIATION_COOLDOWN,
        )

    async def test_recently_started_container_sends_nothing_but_keeps_its_cooldown(self):
        """Something restarted the worker moments ago (another path's heal,
        deploy-sync, compose), so the restart this heal wanted has effectively
        happened and a second one would kill it mid-boot. Nothing to restart,
        nothing to report. The cooldown stamped before the attempt stands, so
        the next attempt waits it out."""
        pages: list[str] = []
        notices: list[str] = []
        restart = AsyncMock(return_value=_restart_outcome(
            False,
            "poindexter-worker started 30s ago, inside the 120s "
            "brain_docker_restart_min_uptime_seconds guard; not restarted",
            status=docker_utils.RESTART_RECENTLY_STARTED, container="poindexter-worker",
        ))

        with patch.object(hp, "_restart_container", new=restart):
            for _ in range(2):
                await hp._try_remediation(
                    "worker_error_rate", {"detail": "100% errors"},
                    pages.append, pool=_make_pool(), info_fn=notices.append,
                )

        assert pages == [] and notices == []
        assert restart.await_count == 1
        assert not probe_failure_state.state.remediation_due(
            "worker_error_rate", hp.REMEDIATION_COOLDOWN,
        )

    async def test_recently_started_is_logged_not_sent(self, caplog):
        restart = AsyncMock(return_value=_restart_outcome(
            False, "poindexter-worker started 30s ago, inside the 120s guard; not restarted",
            status=docker_utils.RESTART_RECENTLY_STARTED, container="poindexter-worker",
        ))

        with patch.object(hp, "_restart_container", new=restart), \
                caplog.at_level("INFO", logger=hp.logger.name):
            await hp._try_remediation(
                "worker_error_rate", {"detail": "100% errors"}, MagicMock(), pool=_make_pool(),
            )

        assert any(
            "Skipped remediation for 'worker_error_rate'" in r.getMessage()
            and "started 30s ago" in r.getMessage()
            and "nothing restarted" in r.getMessage()
            for r in caplog.records
        )

    async def test_three_probes_that_restart_the_worker_in_one_cycle_restart_it_once(self):
        """``worker_error_rate``, ``stuck_tasks`` and ``public_site`` all
        restart ``poindexter-worker``, each on its own cooldown, so one bad
        cycle can run all three within seconds. The worker takes 40-90 s to
        come back: the second and third used to kill the worker the first had
        just started. Through the real helper, on a docker whose container
        starts when it is restarted."""
        docker = _StatefulDocker(started_seconds_ago=3600)
        pages: list[str] = []
        notices: list[str] = []
        probes = ("worker_error_rate", "stuck_tasks", "public_site")

        with patch.object(docker_utils.subprocess, "run", docker):
            for probe in probes:
                pool = _make_pool()
                pool.fetchval = AsyncMock(return_value=None)
                await hp._try_remediation(
                    probe, {"detail": "the worker is failing"},
                    pages.append, pool=pool, info_fn=notices.append,
                )

        restarts = [argv for argv in docker.argvs if argv[1] == "restart"]
        assert restarts == [["docker", "restart", "poindexter-worker"]]
        assert [argv[1] for argv in docker.argvs].count("inspect") == 3
        # The one restart that happened is the one notice; the two that were
        # declined are neither a notice nor a page.
        assert len(notices) == 1 and "restarted poindexter-worker" in notices[0]
        assert pages == []
        for probe in probes:
            assert not probe_failure_state.state.remediation_due(probe, hp.REMEDIATION_COOLDOWN)

    async def test_a_restart_that_timed_out_pages(self):
        pages: list[str] = []
        notices: list[str] = []
        detail = (
            "docker restart poindexter-worker did not return within 90s "
            "(app_settings.brain_docker_restart_timeout_seconds); dockerd may still complete it"
        )
        restart = AsyncMock(return_value=_restart_outcome(
            False, detail, status=docker_utils.RESTART_TIMED_OUT, container="poindexter-worker",
        ))

        with patch.object(hp, "_restart_container", new=restart):
            await hp._try_remediation(
                "worker_error_rate", {"detail": "100% errors"},
                pages.append, pool=_make_pool(), info_fn=notices.append,
            )

        assert notices == []
        assert len(pages) == 1
        assert pages[0].startswith("⚠️ Self-heal 'worker_error_rate': docker restart poindexter-worker did not return")

    @pytest.mark.parametrize(
        ("statuses", "expected"),
        [
            ((docker_utils.RESTART_OK, docker_utils.RESTART_MISSING), "notice"),
            ((docker_utils.RESTART_FAILED, docker_utils.RESTART_MISSING), "page"),
            ((docker_utils.RESTART_MISSING, docker_utils.RESTART_MISSING), "nothing"),
            ((docker_utils.RESTART_OK, docker_utils.RESTART_RECENTLY_STARTED), "notice"),
            ((docker_utils.RESTART_FAILED, docker_utils.RESTART_RECENTLY_STARTED), "page"),
            ((docker_utils.RESTART_RECENTLY_STARTED, docker_utils.RESTART_RECENTLY_STARTED), "nothing"),
            # Neither was restarted and neither failed: still nothing to report.
            ((docker_utils.RESTART_MISSING, docker_utils.RESTART_RECENTLY_STARTED), "nothing"),
        ],
    )
    async def test_restart_multiple_reports_every_container(self, statuses, expected):
        pages: list[str] = []
        notices: list[str] = []
        outcomes = [
            _restart_outcome(st == docker_utils.RESTART_OK, f"{c}: {st}", status=st, container=c)
            for c, st in zip(("a", "b"), statuses, strict=True)
        ]
        action = {"type": "restart_multiple", "containers": ["a", "b"], "description": "both"}

        with patch.dict(hp.REMEDIATIONS, {"multi_probe": action}, clear=True), \
                patch.object(hp, "_restart_container", new=AsyncMock(side_effect=outcomes)):
            await hp._try_remediation(
                "multi_probe", {"detail": "x"}, pages.append, pool=_make_pool(), info_fn=notices.append,
            )

        if expected == "nothing":
            assert pages == [] and notices == []
        else:
            sent, other = (notices, pages) if expected == "notice" else (pages, notices)
            assert other == []
            assert len(sent) == 1 and f"a: {statuses[0]}; b: {statuses[1]}" in sent[0]


# TestCreateGiteaIssue removed 2026-05-03 alongside the underlying
# `_emit_finding` helper and `_created_issues` dedupe set —
# Gitea was decommissioned 2026-04-30, the auto-create paper trail
# went with it. Probe-failure escalation now goes only through
# `notify_operator` (Telegram + Discord) + `alert_events`.


@pytest.mark.unit
@pytest.mark.asyncio
class TestRunHealthProbes:
    async def test_skips_undue_probes(self):
        now = time.time()
        for name in hp.PROBES.keys():
            probe_schedule.schedule.last_run[name] = now

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
    """#304: PROMETHEUS_COVERED suppression is conditional on Alertmanager
    health, and a probe CRASH is never suppressed (the monitoring code is
    broken, which Prometheus does not cover).

    Since 2026-09-25 both go out at the probe's own severity. The
    double-blind case needs *delivery*, and any delivery ends it, so a
    covered warning-class probe (``publish_rate``) is a Discord notice
    rather than a page just because Alertmanager is down. A crash follows
    the probe's severity too, the rule #4051 set for the branch-drift
    canary. Before this, the severity of the five covered probes was
    consulted on no path at all.
    """

    async def _run_with(self, probe_fn, *, probe_name, am_healthy):
        pages: list[str] = []
        notices: list[str] = []
        with patch.dict(hp.PROBES, {probe_name: probe_fn}, clear=True), \
                patch.object(hp, "_is_due", return_value=True), \
                patch.object(hp, "ALERT_AFTER_FAILURES", 1), \
                patch.object(
                    hp, "_alertmanager_healthy",
                    new=AsyncMock(return_value=am_healthy),
                ):
            await hp.run_health_probes(
                _make_pool(), notify_fn=pages.append, info_fn=notices.append,
            )
        return pages, notices

    async def test_covered_probe_suppressed_when_alertmanager_healthy(self):
        async def fail(_pool):
            return {"ok": False, "detail": "db down"}

        # db_ping IS in PROMETHEUS_COVERED_PROBES; Alertmanager healthy => Prom owns it.
        pages, notices = await self._run_with(fail, probe_name="db_ping", am_healthy=True)
        assert pages == [] and notices == []  # Prometheus/Alertmanager delivers it

    async def test_cost_freshness_reports_itself_while_alertmanager_is_healthy(self):
        # Until 2026-09-28 this failure was suppressed here, deferred to
        # spend-level rules that read low in exactly this condition. It is
        # warning-class, so it is a Discord notice, not a page.
        async def stale(_pool):
            return {"ok": False, "detail": "no inference cost-logged for 30.0h — STALE"}

        pages, notices = await self._run_with(
            stale, probe_name="cost_freshness", am_healthy=True,
        )
        assert pages == []
        assert len(notices) == 1
        assert "cost_freshness" in notices[0] and "STALE" in notices[0]

    @pytest.mark.parametrize(
        ("probe_name", "channel"),
        [("db_ping", "page"), ("publish_rate", "notice")],
    )
    async def test_covered_probe_goes_out_at_its_severity_when_alertmanager_down(
        self, probe_name, channel,
    ):
        async def fail(_pool):
            return {"ok": False, "detail": "down"}

        pages, notices = await self._run_with(fail, probe_name=probe_name, am_healthy=False)

        sent, other = (pages, notices) if channel == "page" else (notices, pages)
        assert len(sent) == 1 and other == []
        assert "Alertmanager is unreachable" in sent[0]

    @pytest.mark.parametrize(
        ("probe_name", "channel"),
        [("db_ping", "page"), ("publish_rate", "notice")],
    )
    async def test_a_crash_is_never_suppressed_and_follows_severity(
        self, probe_name, channel,
    ):
        async def crash(_pool):
            raise RuntimeError("probe bug")

        # Covered probe, healthy Alertmanager: a plain failure would be
        # suppressed, a crash is not — at the probe's own severity.
        pages, notices = await self._run_with(crash, probe_name=probe_name, am_healthy=True)

        sent, other = (pages, notices) if channel == "page" else (notices, pages)
        assert len(sent) == 1 and other == []
        assert "ERRORED" in sent[0]


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
            await hp.run_health_probes(_make_pool(), notify_fn=notify_fn)

        # The page must have been *awaited*, not left as a dangling coroutine.
        assert notify_fn.await_count == 1, (
            "run_health_probes did not await the async notify_fn — the "
            "probe-failure page was dropped as an un-awaited coroutine "
            f"(call_count={notify_fn.call_count}, await_count={notify_fn.await_count})"
        )


@pytest.mark.unit
@pytest.mark.asyncio
class TestNoticesThatMustNotPage:
    """2026-09-25: a probe's recovery and a self-heal that worked go to
    ``info_fn``; a probe classified critical/error still pages through
    ``notify_fn`` (this helper uses "disk_space", which
    ``probe_severity.PROBE_DEFAULT_SEVERITY`` classifies critical). The
    brain passes ``notify`` (Telegram + Discord) and ``notify_discord_ops``
    (Discord #ops only), and ``notify`` has no severity, so before
    ``info_fn`` every "✅ recovered" reached Telegram. Without an
    ``info_fn`` both notices fall back to ``notify_fn``, which is the old
    behaviour. See ``TestProbeFailureSeverityRouting`` below for the
    warning/info-default case (most probes)."""

    async def _fail_then_recover(self, *, notify_fn, info_fn):
        results = iter([{"ok": False, "detail": "down"}, {"ok": True, "detail": "back"}])

        async def probe(_pool):
            return next(results)

        with patch.dict(hp.PROBES, {"disk_space": probe}, clear=True), \
                patch.object(hp, "_is_due", return_value=True), \
                patch.object(hp, "ALERT_AFTER_FAILURES", 1):
            for _ in range(2):
                await hp.run_health_probes(
                    _make_pool(), notify_fn=notify_fn, info_fn=info_fn,
                )

    async def test_recovery_goes_to_info_fn_and_the_failure_pages(self):
        pages: list[str] = []
        notices: list[str] = []

        await self._fail_then_recover(notify_fn=pages.append, info_fn=notices.append)

        assert any("failed" in p for p in pages)
        assert not any("recovered" in p for p in pages)
        assert notices == ["✅ Probe 'disk_space' recovered: back"]

    async def test_without_info_fn_the_recovery_falls_back_to_notify_fn(self):
        pages: list[str] = []

        await self._fail_then_recover(notify_fn=pages.append, info_fn=None)

        assert any("recovered" in p for p in pages)

    @pytest.mark.parametrize(
        ("heal_ok", "expected"),
        [(True, "notice"), (False, "page")],
    )
    async def test_self_heal_success_is_a_notice_and_failure_pages(self, heal_ok, expected):
        pages: list[str] = []
        notices: list[str] = []

        restart = AsyncMock(return_value=_restart_outcome(heal_ok, "restarted grafana"))
        with patch.object(hp, "_restart_container", new=restart):
            await hp._try_remediation(
                "grafana_datasources", {"detail": "datasource broken"},
                pages.append, pool=_make_pool(), info_fn=notices.append,
            )

        sent = notices if expected == "notice" else pages
        other = pages if expected == "notice" else notices
        assert len(sent) == 1 and "Self-heal 'grafana_datasources'" in sent[0]
        assert other == []

    async def test_self_heal_success_without_info_fn_falls_back_to_notify_fn(self):
        pages: list[str] = []

        restart = AsyncMock(return_value=_restart_outcome(True, "restarted grafana"))
        with patch.object(hp, "_restart_container", new=restart):
            await hp._try_remediation(
                "grafana_datasources", {"detail": "datasource broken"},
                pages.append, pool=_make_pool(),
            )

        assert len(pages) == 1 and pages[0].startswith("🔧 Self-heal")


@pytest.mark.unit
@pytest.mark.asyncio
class TestProbeFailureSeverityRouting:
    """2026-09-25: a probe's failure or crash pages or notices depending
    on ``probe_severity.severity_for`` (the Prometheus-covered cases are in
    ``TestConditionalSuppressionAndCrash``). Most probes default to
    "warning" (Discord, via ``info_fn``); only the small
    ``probe_severity.PROBE_DEFAULT_SEVERITY`` allowlist pages by default.
    Fixes the shape that let ``cadence_slo`` page Telegram 8 times and
    ``pipeline_throughput`` 6 times in the 30 days to 2026-09-25 for
    business/quality signals, not outages."""

    async def _fail_n_times(
        self, probe_name: str, *, notify_fn, info_fn, n: int = 1, pool=None,
        crash: bool = False,
    ) -> None:
        async def probe(_pool):
            if crash:
                raise RuntimeError("probe bug")
            return {"ok": False, "detail": "boom"}

        # REMEDIATIONS is cleared: several critical probes (worker_error_rate,
        # ollama_models, public_site) also have a self-heal entry, which
        # would fire its OWN notice/page as soon as ALERT_AFTER_FAILURES is
        # hit — a real subprocess.run(["docker", "restart", ...]) with no
        # docker socket in this test, confirmed by the 60s hang before this
        # guard was added. These tests are isolating the plain
        # probe-failure severity routing, not remediation (already covered
        # by TestNoticesThatMustNotPage above).
        with patch.dict(hp.PROBES, {probe_name: probe}, clear=True), \
                patch.object(hp, "_is_due", return_value=True), \
                patch.object(hp, "ALERT_AFTER_FAILURES", n), \
                patch.object(hp, "REMEDIATIONS", {}):
            for _ in range(n):
                await hp.run_health_probes(
                    pool or _make_pool(), notify_fn=notify_fn, info_fn=info_fn,
                )

    async def test_unclassified_probe_defaults_to_a_notice_not_a_page(self):
        """A probe nobody has reconsidered (not in PROBE_DEFAULT_SEVERITY,
        no DB override) must NOT page — the safe default direction."""
        pages: list[str] = []
        notices: list[str] = []

        await self._fail_n_times(
            "some_new_probe_nobody_classified", notify_fn=pages.append, info_fn=notices.append,
        )

        assert pages == []
        assert len(notices) == 1
        assert "Probe 'some_new_probe_nobody_classified' failed" in notices[0]
        assert "warning" in notices[0]

    async def test_unclassified_probe_falls_back_to_notify_fn_without_info_fn(self):
        """Without an info_fn, even a non-paging severity still reaches the
        operator somehow — falls back to notify_fn, same contract as the
        recovery/self-heal notices."""
        pages: list[str] = []

        await self._fail_n_times("some_unclassified_probe", notify_fn=pages.append, info_fn=None)

        assert len(pages) == 1
        assert "failed" in pages[0]

    @pytest.mark.parametrize("probe_name", sorted(ps.PROBE_DEFAULT_SEVERITY))
    async def test_every_default_critical_probe_pages(self, probe_name):
        """Every probe PROBE_DEFAULT_SEVERITY classifies critical must
        actually page when it fails — a live check against the table
        itself, not a hardcoded example, so a future edit to the table is
        covered automatically."""
        if ps.PROBE_DEFAULT_SEVERITY[probe_name] not in ps.PAGING_SEVERITIES:
            pytest.skip(f"{probe_name} is not classified as paging")
        pages: list[str] = []
        notices: list[str] = []

        await self._fail_n_times(probe_name, notify_fn=pages.append, info_fn=notices.append)

        assert len(pages) == 1, f"{probe_name} (critical) did not page: {pages=} {notices=}"
        assert notices == []

    def _pool_with_override(self, overrides_json: str):
        """A pool whose ``brain_probe_severity_overrides`` row answers
        ``overrides_json``; every other app_settings read (the URL
        resolution ``_sync_config_from_db`` runs every cycle) sees no
        row, so it falls through to its own default rather than being
        confused by an unrelated fixed return value."""
        pool = _make_pool()

        async def _fv(_query, *args):
            key = args[0] if args else None
            if key == ps.SEVERITY_OVERRIDES_SETTING_KEY:
                return overrides_json
            return None

        pool.fetchval = AsyncMock(side_effect=_fv)
        return pool

    async def test_db_override_downgrades_a_critical_probe_to_a_notice(self):
        """The operator can decide a normally-critical probe no longer
        needs to page on their install."""
        pages: list[str] = []
        notices: list[str] = []

        pool = self._pool_with_override('{"worker_error_rate": "warning"}')
        await self._fail_n_times(
            "worker_error_rate", notify_fn=pages.append, info_fn=notices.append, pool=pool,
        )

        assert pages == []
        assert len(notices) == 1

    async def test_db_override_promotes_a_warning_probe_to_paging(self):
        """The operator can decide a normally-non-critical probe SHOULD
        page on their install (e.g. cadence_slo matters more for them)."""
        pages: list[str] = []
        notices: list[str] = []

        pool = self._pool_with_override('{"cadence_slo": "critical"}')
        await self._fail_n_times(
            "cadence_slo", notify_fn=pages.append, info_fn=notices.append, pool=pool,
        )

        assert len(pages) == 1
        assert notices == []

    @pytest.mark.parametrize(
        ("probe_name", "channel"),
        [("worker_error_rate", "page"), ("cadence_slo", "notice")],
    )
    async def test_a_crash_follows_the_probes_severity(self, probe_name, channel):
        """A CRASH means the monitoring code is broken, so this check is
        blind. Blindness to a warning-class signal is a warning (the rule
        #4051 set for the branch-drift canary); a critical probe's crash
        still pages."""
        pages: list[str] = []
        notices: list[str] = []

        await self._fail_n_times(
            probe_name, notify_fn=pages.append, info_fn=notices.append, crash=True,
        )

        sent, other = (pages, notices) if channel == "page" else (notices, pages)
        assert len(sent) == 1 and other == []
        assert "ERRORED" in sent[0]


@pytest.mark.unit
def test_default_severity_keys_are_real_probe_names():
    """Every ``probe_severity.PROBE_DEFAULT_SEVERITY`` key must be a probe
    that actually exists in ``health_probes.PROBES`` or one of the
    business/post-performance probe names — catches a renamed or removed
    probe leaving a stale severity entry behind (the exact shape CLAUDE.md
    calls out for the qa_gates alias guard: an expectation must be
    verified against the live source, not trusted by construction)."""
    known = set(hp.PROBES) | {"webhook_freshness", "silent_alerter", "post_performance"}
    unknown = set(ps.PROBE_DEFAULT_SEVERITY) - known
    assert not unknown, (
        f"probe_severity.PROBE_DEFAULT_SEVERITY names probes that don't "
        f"exist: {sorted(unknown)}"
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

    def _make_pool_with(self, actual, last=None, niche_rows=None, **overrides):
        p = _make_pool()
        # Two distinct ``fetch`` calls now happen in sequence: settings,
        # then niches with a per-niche cadence override (poindexter#538).
        # Existing callers that don't pass ``niche_rows`` get an empty
        # override list — the additive per-niche block finds nothing to
        # check and behaves exactly as it did before that block existed.
        p.fetch.side_effect = [self._settings_rows(**overrides), niche_rows or []]
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

    async def test_no_niche_overrides_leaves_result_unaffected(self):
        # No niches carry a cadence_target_posts_per_day override — the
        # additive per-niche block must find nothing and change nothing
        # (poindexter#538, purely additive over the pre-existing global
        # check exercised by every test above this one).
        p = self._make_pool_with(actual=1, last="2026-05-30 09:00:00")
        r = await hp.probe_cadence_slo(p)
        assert r["ok"] is True
        assert r["niche_breaches"] == []
        p.fetchrow.assert_called_once()

    async def test_niche_override_breach_flagged_even_when_global_healthy(self):
        # Global check is healthy (actual 1 >= global threshold 0.5), but
        # a niche with its own 3/day override is starving at 0 posts.
        p = self._make_pool_with(
            actual=1, last="2026-05-30 09:00:00",
            niche_rows=[
                {"slug": "starving-niche", "cadence_target_posts_per_day": 3.0},
            ],
        )
        p.fetchrow.side_effect = [
            {"c": 1, "last_published": "2026-05-30 09:00:00"},  # global
            {"c": 0},  # starving-niche's own count
        ]
        r = await hp.probe_cadence_slo(p)
        assert r["ok"] is False
        assert len(r["niche_breaches"]) == 1
        assert r["niche_breaches"][0]["niche"] == "starving-niche"
        assert "starving-niche" in r["detail"]

    async def test_niche_meeting_its_own_target_does_not_breach(self):
        p = self._make_pool_with(
            actual=1, last="2026-05-30 09:00:00",
            niche_rows=[
                {"slug": "healthy-niche", "cadence_target_posts_per_day": 1.0},
            ],
        )
        p.fetchrow.side_effect = [
            {"c": 1, "last_published": "2026-05-30 09:00:00"},  # global
            {"c": 1},  # healthy-niche meets its own 1/day target
        ]
        r = await hp.probe_cadence_slo(p)
        assert r["ok"] is True
        assert r["niche_breaches"] == []

    async def test_disabled_skips_niche_check_too(self):
        # cadence_slo_enabled=false must short-circuit before the
        # per-niche block runs at all — not just the global one.
        p = self._make_pool_with(actual=0, enabled="false")
        r = await hp.probe_cadence_slo(p)
        assert r["ok"] is True
        assert r.get("status") == "disabled"
        # Only the settings fetch happened — no niches query, no counts.
        assert p.fetch.call_count == 1


@pytest.mark.unit
@pytest.mark.asyncio
class TestProbePodcastHealth:
    """probe_podcast_health measures media_assets — the produced artefact.

    2026-08-15 false positive: the probe counted ``pipeline_tasks_view
    WHERE task_type = 'podcast' OR topic ILIKE '%podcast%'`` and reported
    "stale" while a 4-day-old episode sat in media_assets. Podcasts are a
    side-effect of the canonical_blog graph, so only 5 task_type='podcast'
    rows ever existed against 99 produced assets, and the ILIKE clause
    mis-attributed blog posts that merely discuss podcasting. These tests
    pin the corrected signal source, the settings-driven threshold, and
    the disabled-lane / bootstrap escape hatches.
    """

    def _settings_rows(self, *, threshold=None, enabled=None):
        rows = []
        if threshold is not None:
            rows.append({"key": "podcast_staleness_max_age_days", "value": threshold})
        if enabled is not None:
            rows.append({"key": "podcast_pipeline_trigger_enabled", "value": enabled})
        return rows

    def _make_podcast_pool(self, *, total, age_days=None, settings_rows=None):
        """Pool whose settings fetch and media_assets fetchrow are canned.

        ``age_days`` places the newest episode that many days in the past;
        None with total>0 exercises the NULL-created_at oddity branch.
        """
        p = _make_pool()
        p.fetch.return_value = settings_rows or []
        last = (
            datetime.now(UTC) - timedelta(days=age_days)
            if age_days is not None
            else None
        )
        p.fetchrow.return_value = {"total": total, "last_created": last}
        return p

    async def test_recent_episode_is_healthy(self):
        p = self._make_podcast_pool(total=99, age_days=4)
        r = await hp.probe_podcast_health(p)
        assert r["ok"] is True
        assert r["total_episodes"] == 99
        assert r["last_episode_days"] == pytest.approx(4.0, abs=0.1)
        assert "STALE" not in r["detail"]

    async def test_gap_at_observed_cadence_max_is_healthy(self):
        # The regression that motivated the rewrite: production gaps of
        # 8-9 days are NORMAL cadence (p95 gap 8.75d over 90 days). The
        # old 7d threshold paged on exactly this; 9d must pass now.
        p = self._make_podcast_pool(total=99, age_days=9)
        r = await hp.probe_podcast_health(p)
        assert r["ok"] is True

    async def test_genuinely_stale_fails(self):
        p = self._make_podcast_pool(total=99, age_days=20)
        r = await hp.probe_podcast_health(p)
        assert r["ok"] is False
        assert "STALE" in r["detail"]
        assert r["max_age_days"] == 14.0

    async def test_no_episodes_at_all_is_ok(self):
        p = self._make_podcast_pool(total=0)
        r = await hp.probe_podcast_health(p)
        assert r["ok"] is True
        assert r["total_episodes"] == 0
        assert "no podcast episodes produced yet" in r["detail"]

    async def test_disabled_lane_reports_disabled_without_querying_media(self):
        p = self._make_podcast_pool(
            total=99, age_days=400,
            settings_rows=self._settings_rows(enabled="false"),
        )
        r = await hp.probe_podcast_health(p)
        assert r["ok"] is True
        assert r.get("status") == "disabled"
        p.fetchrow.assert_not_called()

    async def test_threshold_override_tightens(self):
        # Operator sets 7d; a 10-day-old episode is stale under it.
        p = self._make_podcast_pool(
            total=99, age_days=10,
            settings_rows=self._settings_rows(threshold="7"),
        )
        r = await hp.probe_podcast_health(p)
        assert r["ok"] is False
        assert r["max_age_days"] == 7.0

    async def test_threshold_override_loosens(self):
        p = self._make_podcast_pool(
            total=99, age_days=20,
            settings_rows=self._settings_rows(threshold="30"),
        )
        r = await hp.probe_podcast_health(p)
        assert r["ok"] is True
        assert r["max_age_days"] == 30.0

    async def test_unparseable_threshold_falls_back_to_default(self):
        p = self._make_podcast_pool(
            total=99, age_days=4,
            settings_rows=self._settings_rows(threshold="not-a-number"),
        )
        r = await hp.probe_podcast_health(p)
        assert r["ok"] is True
        assert r["max_age_days"] == hp.PODCAST_STALE_DAYS_DEFAULT

    async def test_missing_settings_rows_use_defaults(self):
        # No app_settings rows at all (brain racing the seeder) — the
        # documented defaults apply: 14d threshold, lane assumed enabled.
        p = self._make_podcast_pool(total=99, age_days=4, settings_rows=[])
        r = await hp.probe_podcast_health(p)
        assert r["ok"] is True
        assert r["max_age_days"] == 14.0

    async def test_rows_without_created_at_fail_loud(self):
        # COUNT > 0 but MAX(created_at) NULL — a schema oddity the probe
        # must surface rather than guess a freshness verdict over.
        p = self._make_podcast_pool(total=5, age_days=None)
        r = await hp.probe_podcast_health(p)
        assert r["ok"] is False
        assert "cannot judge freshness" in r["detail"]

    async def test_missing_relation_reports_bootstrap_state(self):
        p = _make_pool()
        p.fetch.return_value = []
        p.fetchrow.side_effect = Exception(
            'relation "media_assets" does not exist'
        )
        r = await hp.probe_podcast_health(p)
        assert r["ok"] is True
        assert "not created yet" in r["detail"]

    async def test_column_drift_fails_loud(self):
        # A missing COLUMN is probe-SQL drift, not a bootstrap state —
        # same rule the 2026-07-11 incident earned for the siblings.
        p = _make_pool()
        p.fetch.return_value = []
        p.fetchrow.side_effect = Exception(
            'column "created_at" does not exist'
        )
        r = await hp.probe_podcast_health(p)
        assert r["ok"] is False
        assert "created_at" in r["detail"]

    async def test_sql_targets_media_assets_production_timestamps(self):
        # Pin the signal source: media_assets.created_at (production),
        # never the legacy pipeline_tasks/topic-ILIKE query, and never
        # updated_at — distribution jobs bump that long after production
        # (96 of 99 live rows), which would mask a real stall.
        p = self._make_podcast_pool(total=99, age_days=4)
        recorded = []

        async def _fetchrow(query, *args, **kwargs):
            recorded.append(query)
            return {
                "total": 99,
                "last_created": datetime.now(UTC) - timedelta(days=4),
            }

        p.fetchrow = AsyncMock(side_effect=_fetchrow)
        await hp.probe_podcast_health(p)
        assert recorded, "probe issued no media query"
        media_sql = recorded[0]
        assert "media_assets" in media_sql
        assert "created_at" in media_sql
        assert "updated_at" not in media_sql
        assert "pipeline_tasks" not in media_sql
        assert "ILIKE" not in media_sql.upper()


def _cost_freshness_pool(*, age_hours, queue=0, settings=None):
    """Pool for probe_cost_freshness: ``fetch`` answers the app_settings read,
    ``fetchrow`` the cost_logs / approval-queue aggregate. ``age_hours=None``
    means cost_logs holds no inference row at all."""
    p = _make_pool()
    p.fetch.return_value = [
        {"key": key, "value": value} for key, value in (settings or {}).items()
    ]
    last = (
        None if age_hours is None else datetime.now(UTC) - timedelta(hours=age_hours)
    )
    p.fetchrow.return_value = {"last_inference": last, "approval_queue": queue}
    return p


@pytest.mark.unit
@pytest.mark.asyncio
class TestProbeCostFreshness:
    """probe_cost_freshness: is LLM inference still being cost-logged?

    The brain reports this probe itself. From 2026-04-19 to 2026-09-28 it
    deferred to spend-level Prometheus rules, which read low in exactly the
    condition it fails on (test_prometheus_covered_probes.py keeps it out of
    that map). These pin the settings-driven threshold, the expected-idle
    check against the throttle's real limit (a hardcoded 3 while prod
    throttled at 5), and that only an inference row can make it look fresh.
    """

    async def test_recent_inference_is_healthy(self):
        r = await hp.probe_cost_freshness(_cost_freshness_pool(age_hours=2))
        assert r["ok"] is True
        assert r["age_hours"] == pytest.approx(2.0, abs=0.1)
        assert r["max_age_hours"] == 24.0
        assert "STALE" not in r["detail"]

    async def test_drought_with_room_in_the_queue_fails(self):
        r = await hp.probe_cost_freshness(_cost_freshness_pool(
            age_hours=30, queue=1, settings={"max_approval_queue": "5"},
        ))
        assert r["ok"] is False
        assert "STALE" in r["detail"]
        assert "(1/5)" in r["detail"]

    async def test_drought_with_a_full_queue_is_expected_idle(self):
        r = await hp.probe_cost_freshness(_cost_freshness_pool(
            age_hours=30, queue=5, settings={"max_approval_queue": "5"},
        ))
        assert r["ok"] is True
        assert r["status"] == "expected_idle"
        assert "(5/5)" in r["detail"]

    async def test_three_waiting_tasks_do_not_excuse_a_drought_at_prods_limit(self):
        # The old hardcoded 3 reported "approval queue full (3/3), pipeline
        # throttled" here, while the throttle (max_approval_queue=5 on prod)
        # was not throttling anything.
        r = await hp.probe_cost_freshness(_cost_freshness_pool(
            age_hours=30, queue=3, settings={"max_approval_queue": "5"},
        ))
        assert r["ok"] is False
        assert r["approval_queue_limit"] == 5

    async def test_throttle_off_never_excuses_a_drought(self):
        r = await hp.probe_cost_freshness(_cost_freshness_pool(
            age_hours=30, queue=50, settings={"max_approval_queue": "0"},
        ))
        assert r["ok"] is False
        assert "throttle off" in r["detail"]

    async def test_missing_settings_rows_use_the_documented_defaults(self):
        # A brain racing the seeder: 24h, and the throttle's own fallback
        # limit when its row is missing.
        r = await hp.probe_cost_freshness(_cost_freshness_pool(age_hours=30, queue=3))
        assert r["ok"] is True
        assert r["status"] == "expected_idle"
        assert r["max_age_hours"] == hp.COST_FRESHNESS_MAX_AGE_DEFAULT
        assert r["approval_queue_limit"] == hp.MAX_APPROVAL_QUEUE_FALLBACK

    @pytest.mark.parametrize(
        ("threshold", "age_hours", "ok"),
        [("6", 7, False), ("6", 5, True), ("48", 30, True), ("48", 50, False)],
    )
    async def test_the_threshold_is_a_setting(self, threshold, age_hours, ok):
        r = await hp.probe_cost_freshness(_cost_freshness_pool(
            age_hours=age_hours, settings={"cost_freshness_max_age_hours": threshold},
        ))
        assert r["ok"] is ok
        assert r["max_age_hours"] == float(threshold)

    async def test_unparseable_threshold_falls_back_to_default(self):
        r = await hp.probe_cost_freshness(_cost_freshness_pool(
            age_hours=2, settings={"cost_freshness_max_age_hours": "a day"},
        ))
        assert r["ok"] is True
        assert r["max_age_hours"] == hp.COST_FRESHNESS_MAX_AGE_DEFAULT

    async def test_no_inference_row_yet_is_ok(self):
        r = await hp.probe_cost_freshness(_cost_freshness_pool(age_hours=None))
        assert r["ok"] is True
        assert r["status"] == "no_inference_yet"

    async def test_only_inference_rows_can_make_it_fresh(self):
        # The brain writes an electricity row every cycle. An unfiltered
        # MAX(created_at) would read any drought as minutes old.
        p = _cost_freshness_pool(age_hours=2)
        await hp.probe_cost_freshness(p)
        sql = " ".join(p.fetchrow.await_args.args[0].split())
        assert "FROM cost_logs WHERE cost_type IS NULL OR cost_type = 'inference'" in sql
        assert sql.count("FROM cost_logs") == 1

    async def test_a_query_error_fails_with_the_real_message(self):
        # The old fallback re-ran MAX(created_at) over every cost_logs row on
        # ANY error, electricity rows included, so a failing query read as
        # fresh. One query, and its error is the result.
        p = _cost_freshness_pool(age_hours=2)
        p.fetchrow.side_effect = Exception("canceling statement due to statement timeout")
        r = await hp.probe_cost_freshness(p)
        assert r["ok"] is False
        assert "statement timeout" in r["detail"]
        assert p.fetchrow.await_count == 1

    async def test_the_expected_idle_count_is_the_throttles_view(self):
        p = _cost_freshness_pool(age_hours=2)
        await hp.probe_cost_freshness(p)
        sql = " ".join(p.fetchrow.await_args.args[0].split())
        assert "FROM content_tasks WHERE status = 'awaiting_approval'" in sql


@pytest.mark.unit
def test_cost_freshness_default_is_the_seeded_default():
    from poindexter.services.settings_defaults import DEFAULTS, METADATA

    key = hp.COST_FRESHNESS_MAX_AGE_SETTING
    assert float(DEFAULTS[key]) == hp.COST_FRESHNESS_MAX_AGE_DEFAULT
    assert METADATA[key] == {"owner": "health_probes", "value_type": "float"}


@pytest.mark.unit
@pytest.mark.asyncio
class TestCostFreshnessUsesTheThrottlesLimit:
    """The probe's "expected idle" is a claim about the throttle: inference
    is quiet because ``pipeline_throttle.is_queue_full`` says the approval
    queue is full. The brain can't import the throttle, so it re-reads the
    same key with the same fallback. This runs both on the same raw values
    instead of trusting two hand-typed copies to agree, which also catches
    either side renaming the key."""

    @pytest.fixture(autouse=True)
    def _fresh_throttle(self, monkeypatch):
        from poindexter.services import pipeline_throttle

        # SiteConfig.get falls back to this env var when the key is absent.
        monkeypatch.delenv("MAX_APPROVAL_QUEUE", raising=False)
        pipeline_throttle.reset_for_tests()
        yield
        pipeline_throttle.reset_for_tests()

    @pytest.mark.parametrize(
        "raw", [None, "", "5", "3", "100", "0", "-1", "abc", "5.0", " 7 "],
    )
    @pytest.mark.parametrize("queue", [0, 2, 3, 4, 5, 7, 100])
    async def test_expected_idle_exactly_when_the_throttle_is_full(self, raw, queue):
        from poindexter.services import pipeline_throttle
        from poindexter.services.site_config import SiteConfig

        throttle_pool = MagicMock()
        throttle_pool.fetchrow = AsyncMock(return_value={"c": queue})
        initial = {} if raw is None else {"max_approval_queue": raw}
        full, _size, _limit = await pipeline_throttle.is_queue_full(
            throttle_pool, site_config=SiteConfig(initial_config=initial),
        )

        r = await hp.probe_cost_freshness(_cost_freshness_pool(
            age_hours=30, queue=queue, settings=initial,
        ))

        assert (r.get("status") == "expected_idle") is full, (
            f"max_approval_queue={raw!r}, {queue} awaiting approval: the "
            f"throttle says full={full}, the probe says {r}"
        )
        assert r["ok"] is full


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
class TestIsLocalOllamaTag:
    """The content-gen probe only sends Ollama's LOCAL /api/generate a name it
    can actually serve. Cloud/remote provider-prefixed models and non-tag
    sentinels are rejected so the probe never 404s (the 2026-07-07 Sonnet-canary
    regression: pipeline_writer_model flipped to anthropic/claude-sonnet-5)."""

    def test_bare_tag_is_local(self):
        assert hp._is_local_ollama_tag("gemma3:27b") is True

    def test_cloud_provider_prefix_is_not_local(self):
        # A residual '/' after ollama/-stripping = a foreign LiteLLM provider
        # prefix (anthropic/, openai/, gemini/, …) — not an Ollama tag.
        assert hp._is_local_ollama_tag("anthropic/claude-sonnet-5") is False
        assert hp._is_local_ollama_tag("openai/gpt-5") is False

    def test_auto_sentinel_is_not_local(self):
        # default_ollama_model ships as the literal 'auto' — not a concrete tag.
        assert hp._is_local_ollama_tag("auto") is False

    def test_empty_and_none_are_not_local(self):
        assert hp._is_local_ollama_tag("") is False
        assert hp._is_local_ollama_tag(None) is False


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

    async def test_skips_cloud_writer_model_and_falls_through(self):
        # 2026-07-07 Sonnet-canary regression: pipeline_writer_model was flipped
        # to a cloud model (anthropic/claude-sonnet-5). The brain runs Ollama's
        # LOCAL /api/generate, which 404s on a provider-prefixed name — so the
        # resolver must NOT return it; it falls through to the next local source.
        p = self._pool_with_settings(
            pipeline_writer_model="anthropic/claude-sonnet-5",
            default_ollama_model="gemma3:27b",
        )
        model = await hp._resolve_content_gen_model(p)
        assert model == "gemma3:27b"

    async def test_skips_auto_sentinel_and_falls_through(self):
        # default_ollama_model ships as the literal sentinel 'auto' (not a real
        # tag). It must be treated as non-concrete and fall through to installed-
        # model detection rather than being POSTed to /api/generate (→ 404).
        p = self._pool_with_settings(
            pipeline_writer_model="", default_ollama_model="auto"
        )
        tags = MagicMock()
        tags.read.return_value = b'{"models": [{"name": "phi4:14b"}]}'
        with patch("urllib" + ".request.urlopen", return_value=tags):
            model = await hp._resolve_content_gen_model(p)
        assert model == "phi4:14b"

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
class TestSmallestInstalledGenerativeModel:
    """The content-gen fallback picks the SMALLEST installed non-embedding model
    (by /api/tags ``size``), not the first in the list. The probe is a local-
    generation liveness check, so it must not cold-load a 40GB 70B model (30s
    timeout + VRAM oversubscription) when a ~2GB model proves Ollama generates."""

    def _tags(self, payload: bytes):
        resp = MagicMock()
        resp.read.return_value = payload
        return resp

    def test_picks_smallest_generative_skipping_embedders(self):
        # 70B listed first; embedder in the middle; small 3B last → 3B wins.
        payload = (
            b'{"models": ['
            b'{"name": "Llama-3.3-70B:latest", "size": 40050000000},'
            b'{"name": "nomic-embed-text:latest", "size": 270000000},'
            b'{"name": "qwen2.5:3b", "size": 1930000000}'
            b"]}"
        )
        with patch("urllib" + ".request.urlopen", return_value=self._tags(payload)):
            assert hp._smallest_installed_generative_model() == "qwen2.5:3b"

    def test_missing_size_degrades_to_declaration_order(self):
        # Older Ollama may omit 'size' — degrade to first-non-embedding.
        payload = (
            b'{"models": [{"name": "nomic-embed-text:latest"},'
            b' {"name": "phi4:14b"}]}'
        )
        with patch("urllib" + ".request.urlopen", return_value=self._tags(payload)):
            assert hp._smallest_installed_generative_model() == "phi4:14b"

    def test_empty_when_only_embedders(self):
        payload = b'{"models": [{"name": "nomic-embed-text:latest", "size": 1}]}'
        with patch("urllib" + ".request.urlopen", return_value=self._tags(payload)):
            assert hp._smallest_installed_generative_model() == ""

    def test_empty_when_ollama_unreachable(self):
        with patch("urllib" + ".request.urlopen", side_effect=RuntimeError("down")):
            assert hp._smallest_installed_generative_model() == ""


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

    async def test_cloud_writer_falls_through_to_local_model(self):
        # End-to-end reproduction of the 2026-07-07 page: the writer is a cloud
        # model. The probe must exercise an installed LOCAL model via /api/tags
        # rather than POST 'anthropic/claude-sonnet-5' to Ollama (→ HTTP 404).
        p = _make_pool()

        async def _fv(_query, *args):
            key = args[0] if args else None
            return {
                "pipeline_writer_model": "anthropic/claude-sonnet-5",
                "default_ollama_model": "auto",
            }.get(key)

        p.fetchval = AsyncMock(side_effect=_fv)

        def _urlopen(req, *_a, **_k):
            url = getattr(req, "full_url", req)
            resp = MagicMock()
            if "/api/tags" in url:
                resp.read.return_value = b'{"models": [{"name": "gemma3:27b"}]}'
            else:  # /api/generate
                resp.read.return_value = (
                    b'{"response": "FastAPI is a Python web framework."}'
                )
            return resp

        with patch("urllib" + ".request.urlopen", side_effect=_urlopen):
            r = await hp.probe_content_gen(p)
        assert r["ok"] is True
        assert r["model"] == "gemma3:27b"
        assert r["model"] != "anthropic/claude-sonnet-5"


@pytest.mark.unit
@pytest.mark.asyncio
class TestProbeContentGenGpuLock:
    """probe_content_gen must yield the GPU to active renders / LLM jobs.

    Exercising the writer loads the ~19GB model into VRAM. Firing during a
    media render (wan + image-gen already near the 32GB ceiling) oversubscribes the
    GPU → image-gen CUDA-OOM → degraded video (observed 2026-06-21). The brain runs
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
        # Same int64 key the worker's GPUScheduler holds. Pin it against the
        # WORKER CONSTANT, not a literal: the brain duplicates the value by
        # hand (it runs stdlib + asyncpg and cannot import the worker), so a
        # literal-only assertion passes happily while the two trees diverge —
        # and a diverged key means this probe stops seeing render sessions and
        # loads the ~19 GB writer into VRAM mid-render.
        from poindexter.services.gpu_scheduler import GPU_ADVISORY_LOCK_KEY

        assert call.args[1] == hp.GPU_ADVISORY_LOCK_KEY == GPU_ADVISORY_LOCK_KEY

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


# ---------------------------------------------------------------------------
# New probes + recovery helpers added for Ollama embed-endpoint monitoring
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
class TestBrainGpuLockIsSelfIdentifying:
    """A brain-held GPU lock must name itself, like the worker's does.

    The worker stamps its advisory-lock connection so any process can read the
    holder out of pg_stat_activity (poindexter#1018). These two probes took the
    SAME lock from an unlabelled POOLED connection, so while one was running
    the operator's 503 said "an untagged session" and the console panel showed
    a holder with no name — a GPU demonstrably busy, held by nobody you can go
    look at.

    Pooled, not dedicated, is why the tag is set at lock time and restored at
    unlock: the label must describe the LOCK, not the connection carrying it.
    """

    def _pool(self, *, writer="gemma3:27b"):
        p = _make_pool()

        async def _fv(query, *args):
            # The settings pool answers by key; the lock connection is separate.
            key = args[0] if args else None
            return {"pipeline_writer_model": writer}.get(key)

        p.fetchval = AsyncMock(side_effect=_fv)
        return p

    @staticmethod
    def _lock_conn_fetchval(previous_app_name="brain"):
        """Answer the three fetchvals a tagged lock makes, in order."""
        calls: list[tuple] = []

        async def _fv(query, *args):
            calls.append((query, args))
            if "pg_try_advisory_lock" in query:
                return True
            if "SHOW application_name" in query:
                return previous_app_name
            return args[0] if args else None  # set_config echoes its value

        return AsyncMock(side_effect=_fv), calls

    async def test_content_gen_stamps_and_restores_application_name(self):
        p = self._pool()
        p._lock_conn.fetchval, calls = self._lock_conn_fetchval()
        resp = MagicMock()
        resp.read.return_value = (
            b'{"response": "FastAPI is a modern Python web framework for APIs."}'
        )
        with patch("urllib" + ".request.urlopen", return_value=resp):
            await hp.probe_content_gen(p)

        stamps = [
            args[0]
            for query, args in calls
            if "set_config" in query and args
        ]
        assert stamps, "the lock was taken without ever labelling itself"
        tag = stamps[0]
        assert tag.startswith("poindexter-gpu:brain_probe:content_gen:")
        assert len(tag.encode()) <= 63, "Postgres truncates application_name at 63"
        # Restored before the pooled connection goes back to the pool.
        assert stamps[-1] == "brain"

    async def test_embedding_probe_stamps_its_own_phase(self):
        p = self._pool()
        p._lock_conn.fetchval, calls = self._lock_conn_fetchval()
        resp = MagicMock()
        resp.read.return_value = b'{"embeddings": [[0.1, 0.2]]}'
        with patch("urllib" + ".request.urlopen", return_value=resp):
            await hp.probe_ollama_embedding(p)

        stamps = [args[0] for query, args in calls if "set_config" in query and args]
        assert stamps and stamps[0].startswith(
            "poindexter-gpu:brain_probe:ollama_embedding:"
        )

    async def test_the_worker_can_parse_what_the_brain_writes(self):
        """The brain rebuilds the tag by hand (stdlib + asyncpg container, no
        gpu_scheduler import). If the shapes drift, a brain-held lock silently
        goes back to reading as anonymous — so round-trip it against the real
        parser rather than against a literal."""
        from poindexter.services.gpu_scheduler import parse_holder_tag_fields

        p = self._pool()
        p._lock_conn.fetchval, calls = self._lock_conn_fetchval()
        resp = MagicMock()
        resp.read.return_value = b'{"response": "ok ok ok ok ok ok ok ok ok ok"}'
        with patch("urllib" + ".request.urlopen", return_value=resp):
            await hp.probe_content_gen(p)

        tag = next(args[0] for query, args in calls if "set_config" in query and args)
        fields = parse_holder_tag_fields(tag)
        assert fields["owner"] == "brain_probe"
        assert fields["phase"] == "content_gen"
        assert isinstance(fields["pid"], int)

    async def test_a_failed_stamp_never_costs_us_the_probe(self):
        """Labelling is observability. If SHOW/set_config fails the probe must
        still run and still release the lock — the old, unlabelled behaviour."""
        async def _fv(query, *args):
            if "pg_try_advisory_lock" in query:
                return True
            raise RuntimeError("no set_config for you")

        p = self._pool()
        p._lock_conn.fetchval = AsyncMock(side_effect=_fv)
        resp = MagicMock()
        resp.read.return_value = (
            b'{"response": "FastAPI is a modern Python web framework for APIs."}'
        )
        with patch("urllib" + ".request.urlopen", return_value=resp):
            r = await hp.probe_content_gen(p)

        assert r.get("status") != "skipped_gpu_busy"
        assert [
            c for c in p._lock_conn.execute.await_args_list
            if "pg_advisory_unlock" in c.args[0]
        ], "the lock must still be released"


class TestOllamaEmbeddingProbe:
    """probe_ollama_embedding validates /api/embed, not just /api/tags.
    The existing probe_ollama_models only checks the model list; this probe
    exercises the actual embedding path so the brain catches RAG outages."""

    async def test_happy_path_returns_vector_dim(self):
        resp = MagicMock()
        resp.read.return_value = b'{"embeddings": [[0.1, 0.2, 0.3]]}'
        with patch("urllib" + ".request.urlopen", return_value=resp):
            r = await hp.probe_ollama_embedding(_make_pool())
        assert r["ok"] is True
        assert r["vector_dim"] == 3
        assert r["model"] == "nomic-embed-text"

    async def test_embed_endpoint_down_returns_not_ok(self):
        with patch("urllib" + ".request.urlopen", side_effect=OSError("connection refused")):
            r = await hp.probe_ollama_embedding(_make_pool())
        assert r["ok"] is False
        assert "detail" in r

    async def test_gpu_busy_is_a_non_alerting_skip_not_an_outage(self):
        """2026-08-22/23: 25 'embed endpoint failed: timed out' pages, every
        one inside a 200–300 s gemma QA hold on the same Ollama, with the
        pipeline's own embeds never failing. Lock held → skip, ok=True, and
        the embed request is never even sent (it would only queue)."""
        p = _make_pool()
        p._lock_conn.fetchval = AsyncMock(return_value=False)
        with patch("urllib" + ".request.urlopen") as urlopen:
            r = await hp.probe_ollama_embedding(p)
        assert r["ok"] is True
        assert r["status"] == "skipped_gpu_busy"
        urlopen.assert_not_called()
        assert not [
            c for c in p._lock_conn.execute.await_args_list
            if "pg_advisory_unlock" in c.args[0]
        ], "never acquired → must not release someone else's lock"

    async def test_lock_is_released_after_a_real_probe(self):
        p = _make_pool()  # lock free
        resp = MagicMock()
        resp.read.return_value = b'{"embeddings": [[0.1, 0.2]]}'
        with patch("urllib" + ".request.urlopen", return_value=resp):
            r = await hp.probe_ollama_embedding(p)
        assert r["ok"] is True
        assert [
            c for c in p._lock_conn.execute.await_args_list
            if "pg_advisory_unlock" in c.args[0]
        ], "lock taken for the probe must be released"

    async def test_empty_embeddings_list_returns_not_ok(self):
        # Ollama may return {"embeddings": []} if the model is not loaded.
        resp = MagicMock()
        resp.read.return_value = b'{"embeddings": []}'
        with patch("urllib" + ".request.urlopen", return_value=resp):
            r = await hp.probe_ollama_embedding(_make_pool())
        assert r["ok"] is False
        assert r["vector_dim"] == 0

    async def test_missing_embeddings_key_returns_not_ok(self):
        resp = MagicMock()
        resp.read.return_value = b'{"error": "model not found"}'
        with patch("urllib" + ".request.urlopen", return_value=resp):
            r = await hp.probe_ollama_embedding(_make_pool())
        assert r["ok"] is False


@pytest.mark.unit
@pytest.mark.asyncio
class TestCallAgentRecovery:
    """_call_agent_recovery POSTs to the host recovery agent via httpx.
    The recovery agent runs on the Windows host (not in Docker) and stops+starts
    host processes like Ollama."""

    async def test_pool_none_returns_false(self):
        ok, msg = await hp._call_agent_recovery(None, "ollama")
        assert ok is False
        assert "pool unavailable" in msg

    async def test_unconfigured_url_returns_false(self):
        # DB has no recovery_url row → fetchrow returns None → empty string → bail.
        pool = _make_pool()
        pool.fetchrow.return_value = None
        ok, msg = await hp._call_agent_recovery(pool, "ollama")
        assert ok is False
        assert "not configured" in msg

    async def test_httpx_unavailable_returns_false(self):
        pool = _make_pool()
        _configured = AsyncMock(side_effect=["http://host.docker.internal:9841/recover", "tok"])
        with patch.object(hp, "_read_app_setting", new=_configured), \
             patch.object(hp, "httpx", None):
            ok, msg = await hp._call_agent_recovery(pool, "ollama")
        assert ok is False
        assert "httpx" in msg

    async def test_agent_200_returns_true(self):
        pool = _make_pool()
        _configured = AsyncMock(side_effect=["http://host.docker.internal:9841/recover", "tok"])
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)

        fake_httpx = MagicMock()
        fake_httpx.AsyncClient.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        fake_httpx.AsyncClient.return_value.__aexit__ = AsyncMock(return_value=False)

        with patch.object(hp, "_read_app_setting", new=_configured), \
             patch.object(hp, "httpx", fake_httpx):
            ok, detail = await hp._call_agent_recovery(pool, "ollama")

        assert ok is True
        assert "200" in detail
        mock_client.post.assert_called_once()
        call_kwargs = mock_client.post.call_args
        assert call_kwargs.kwargs.get("json", {}).get("service") == "ollama"

    async def test_agent_500_returns_false(self):
        pool = _make_pool()
        _configured = AsyncMock(side_effect=["http://host.docker.internal:9841/recover", "tok"])
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.json.return_value = {"detail": "internal error"}
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)

        fake_httpx = MagicMock()
        fake_httpx.AsyncClient.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        fake_httpx.AsyncClient.return_value.__aexit__ = AsyncMock(return_value=False)

        with patch.object(hp, "_read_app_setting", new=_configured), \
             patch.object(hp, "httpx", fake_httpx):
            ok, detail = await hp._call_agent_recovery(pool, "ollama")

        assert ok is False
        assert "500" in detail

    async def test_connection_exception_returns_false(self):
        pool = _make_pool()
        _configured = AsyncMock(side_effect=["http://host.docker.internal:9841/recover", "tok"])
        fake_httpx = MagicMock()
        fake_httpx.AsyncClient.return_value.__aenter__ = AsyncMock(
            side_effect=ConnectionError("refused")
        )
        fake_httpx.AsyncClient.return_value.__aexit__ = AsyncMock(return_value=False)

        with patch.object(hp, "_read_app_setting", new=_configured), \
             patch.object(hp, "httpx", fake_httpx):
            ok, detail = await hp._call_agent_recovery(pool, "ollama")

        assert ok is False
        assert "ConnectionError" in detail or "refused" in detail


@pytest.mark.unit
@pytest.mark.asyncio
class TestCallAgentRecoveryDetail:
    """What ``_call_agent_recovery`` says back, and how long it waits. The
    firefighter's ``restart_host_service`` records this detail on its
    remediation_action and pages with it, so the agent's own words matter."""

    @staticmethod
    def _agent(status, body=None, *, raises=None):
        response = MagicMock()
        response.status_code = status
        if raises is not None:
            response.json.side_effect = raises
        else:
            response.json.return_value = body
        client = AsyncMock()
        client.post = AsyncMock(return_value=response)
        fake_httpx = MagicMock()
        fake_httpx.AsyncClient.return_value.__aenter__ = AsyncMock(return_value=client)
        fake_httpx.AsyncClient.return_value.__aexit__ = AsyncMock(return_value=False)
        return fake_httpx

    async def _call(self, fake_httpx, **kwargs):
        configured = AsyncMock(side_effect=["http://host.docker.internal:9841/recover", "tok"])
        with patch.object(hp, "_read_app_setting", new=configured), \
             patch.object(hp, "httpx", fake_httpx):
            return await hp._call_agent_recovery(_make_pool(), "ollama", **kwargs)

    async def test_success_names_what_the_agent_restarted(self):
        fake = self._agent(200, {"ok": True, "service": "ollama",
                                 "detail": "restarted ollama-primary.service (system)"})
        assert await self._call(fake) == (True, "HTTP 200 — restarted ollama-primary.service (system)")

    async def test_a_refused_request_reports_the_agents_error(self):
        """The agent answers a bad token or an unknown service with ``error``,
        not ``detail``; the old read dropped it and said only "HTTP 401 — "."""
        fake = self._agent(401, {"ok": False, "error": "invalid token"})
        assert await self._call(fake) == (False, "HTTP 401 — invalid token")

    async def test_a_body_that_is_not_json_keeps_the_plain_status(self):
        fake = self._agent(502, raises=ValueError("not json"))
        assert await self._call(fake) == (False, "HTTP 502")

    async def test_the_probe_path_keeps_its_15_second_wait(self):
        fake = self._agent(200, {"ok": True})
        await self._call(fake)
        fake.AsyncClient.assert_called_once_with(timeout=15.0)

    async def test_a_caller_can_wait_longer(self):
        """The firefighter waits past the agent's own 30 s on systemctl."""
        fake = self._agent(200, {"ok": True})
        await self._call(fake, timeout=45.0)
        fake.AsyncClient.assert_called_once_with(timeout=45.0)


@pytest.mark.unit
class TestOllamaRemediation:
    """REMEDIATIONS config and _try_remediation routing for Ollama probes."""

    def test_ollama_models_has_recover_via_agent_entry(self):
        entry = hp.REMEDIATIONS.get("ollama_models")
        assert entry is not None, '"ollama_models" not in REMEDIATIONS'
        assert entry["type"] == "recover_via_agent"
        assert entry.get("service") == "ollama"

    def test_ollama_embedding_has_recover_via_agent_entry(self):
        entry = hp.REMEDIATIONS.get("ollama_embedding")
        assert entry is not None, '"ollama_embedding" not in REMEDIATIONS'
        assert entry["type"] == "recover_via_agent"
        assert entry.get("service") == "ollama"

    async def test_try_remediation_routes_ollama_models_to_agent_recovery(self):
        """_try_remediation(ollama_models) must call _call_agent_recovery with
        service="ollama" rather than falling through to a docker-restart path."""
        calls: list = []

        async def fake_recovery(inner_pool, service):
            calls.append(service)
            return True, "ok"

        with patch.object(hp, "_call_agent_recovery", new=fake_recovery):
            await hp._try_remediation("ollama_models", {"ok": False}, pool=_make_pool())

        assert calls == ["ollama"]

    async def test_try_remediation_routes_ollama_embedding_to_agent_recovery(self):
        calls: list = []

        async def fake_recovery(inner_pool, service):
            calls.append(service)
            return True, "ok"

        with patch.object(hp, "_call_agent_recovery", new=fake_recovery):
            await hp._try_remediation("ollama_embedding", {"ok": False}, pool=_make_pool())

        assert calls == ["ollama"]


@pytest.mark.unit
@pytest.mark.asyncio
class TestProbeTrafficAnomaly:
    """probe_traffic_anomaly must compare a rolling trailing-24h window
    against the 7-day average, not a partial calendar day since midnight
    UTC. The calendar-day form mechanically read as a 60-100% "drop" every
    day until ~10am UTC regardless of real traffic — live audit_log data
    showed 44 of 45 issue-firings over 14 days landing in the 00:00-06:00
    UTC hours. This is a different bug than poindexter#1301/#1395's
    low-traffic minimum-baseline floor (avg < 10), which stays intact.
    """

    async def _run(self, last_24h, daily_avg):
        p = _make_pool()
        p.fetchrow.return_value = {"last_24h": last_24h, "daily_avg": daily_avg}
        return await hp.probe_traffic_anomaly(p)

    async def test_queries_rolling_24h_window_not_calendar_day(self):
        """Regression pin: the SQL must key off a rolling interval, not
        date_trunc('day', ...), so a probe run at 00:05 UTC doesn't see
        an artificially tiny 'today' count."""
        p = _make_pool()
        p.fetchrow.return_value = {"last_24h": 250, "daily_avg": 300.0}
        await hp.probe_traffic_anomaly(p)
        query = p.fetchrow.call_args.args[0]
        assert "INTERVAL '24 hours'" in query
        assert "date_trunc" not in query

    async def test_normal_traffic_no_anomaly(self):
        r = await self._run(last_24h=280, daily_avg=300.0)
        assert r["ok"] is True
        assert "ANOMALY" not in r["detail"]

    async def test_real_crash_still_flagged(self):
        """A genuine collapse (beacon broken, site down) must still fire —
        the fix must not make the probe blind to real anomalies."""
        r = await self._run(last_24h=20, daily_avg=300.0)
        assert r["ok"] is False
        assert "ANOMALY" in r["detail"]
        assert r["drop_pct"] > 60

    async def test_early_utc_partial_day_no_longer_false_positive(self):
        """The exact live scenario this bug produced: ~63 views a few
        hours into the UTC day against a 300/day average. Under the old
        date_trunc('day', ...) form this was a mechanical false ANOMALY;
        with a rolling 24h window a comparable trailing-24h count near
        the average must read healthy."""
        r = await self._run(last_24h=290, daily_avg=300.0)
        assert r["ok"] is True
        assert "ANOMALY" not in r["detail"]

    async def test_low_baseline_floor_from_1301_still_applies(self):
        """poindexter#1301/#1395's fix (skip when avg < 10) must survive
        this change untouched."""
        r = await self._run(last_24h=1, daily_avg=5.0)
        assert r["ok"] is True
        assert "not enough history" in r["detail"]

    async def test_result_keys_use_last_24h_not_today(self):
        """The 'today' key name implied calendar-day semantics that no
        longer apply — result must expose last_24h instead."""
        r = await self._run(last_24h=100, daily_avg=300.0)
        assert "last_24h" in r
        assert "today" not in r

    async def test_db_error_returns_ok_false_without_raising(self):
        p = _make_pool()
        p.fetchrow.side_effect = RuntimeError("connection reset")
        r = await hp.probe_traffic_anomaly(p)
        assert r["ok"] is False
        assert "connection reset" in r["detail"]
