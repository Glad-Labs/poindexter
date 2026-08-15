"""Unit tests for ProbeNarrationFailureJob (2026-08-15 chatterbox outage).

Per-render ``narration_synthesis_failed`` warns route to Discord and scroll
past — the 08-13 outage ran two days with 30+ sends while three silent
renders reached (and failed) operator review. These pin the escalating
watchdog: page critical on a failure cluster, or on the live TTS probe
staying down across consecutive runs (the dispatch TTS gate makes a total
outage produce ZERO failure findings, so the count trigger alone would go
quiet exactly when the outage is worst).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from services.jobs import probe_narration_failure as pnf
from services.jobs.probe_narration_failure import ProbeNarrationFailureJob
from services.site_config import SiteConfig


class _FakePool:
    def __init__(
        self,
        failures: int,
        tasks: int,
        top_reason: str | None,
        history: list[str] | None = None,
    ) -> None:
        self._row = {"failures": failures, "tasks": tasks, "top_reason": top_reason}
        self._history = [{"tts_healthy": h} for h in (history or [])]
        self.seen_fetchrow_args: tuple | None = None
        self.seen_fetch_args: tuple | None = None

    def acquire(self):  # type: ignore[no-untyped-def]
        pool = self

        class _Acq:
            async def __aenter__(self):  # type: ignore[no-untyped-def]
                conn = AsyncMock()

                async def _fetchrow(_sql, *args):  # type: ignore[no-untyped-def]
                    pool.seen_fetchrow_args = args
                    return pool._row

                async def _fetch(_sql, *args):  # type: ignore[no-untyped-def]
                    pool.seen_fetch_args = args
                    limit = args[-1] if args else len(pool._history)
                    return pool._history[: int(limit)]

                conn.fetchrow = _fetchrow
                conn.fetch = _fetch
                return conn

            async def __aexit__(self, *_a):  # type: ignore[no-untyped-def]
                return False

        return _Acq()


def _cfg(**overrides: str) -> dict:
    return {"_site_config": SiteConfig(initial_config=dict(overrides))}


def _stub_probe(monkeypatch, healthy: bool | None, detail: str = "stubbed"):
    monkeypatch.setattr(
        pnf, "_probe_tts", AsyncMock(return_value=(healthy, detail)),
    )


@pytest.mark.unit
class TestFailureClusterTrigger:
    async def test_cluster_pages_critical(self, monkeypatch):
        findings: list[dict] = []
        monkeypatch.setattr(pnf, "emit_finding", lambda **kw: findings.append(kw))
        _stub_probe(monkeypatch, True, "chatterbox healthy")
        pool = _FakePool(6, 3, "Chatterbox TTS failed: sidecar returned no audio")

        result = await ProbeNarrationFailureJob().run(pool, _cfg())

        assert result.ok and result.changes_made == 1
        f = findings[0]
        assert f["kind"] == "narration_failure_streak"
        # critical is the whole point: warn routes to Discord (which scrolled
        # past for two days); critical routes to Telegram and is never cooled
        assert f["severity"] == "critical"
        assert "Chatterbox TTS failed" in f["body"]
        assert "tts-hq" in f["body"]  # remediation names the exact command
        assert f["extra"]["narration_failures"] == 6
        assert f["extra"]["tasks_affected"] == 3

    async def test_below_threshold_healthy_probe_is_silent(self, monkeypatch):
        findings: list[dict] = []
        monkeypatch.setattr(pnf, "emit_finding", lambda **kw: findings.append(kw))
        _stub_probe(monkeypatch, True, "chatterbox healthy")

        result = await ProbeNarrationFailureJob().run(_FakePool(2, 2, "x"), _cfg())

        assert result.ok and result.changes_made == 0
        assert findings == []

    async def test_thresholds_are_operator_tunable(self, monkeypatch):
        findings: list[dict] = []
        monkeypatch.setattr(pnf, "emit_finding", lambda **kw: findings.append(kw))
        _stub_probe(monkeypatch, True, "healthy")
        pool = _FakePool(4, 2, "x")

        result = await ProbeNarrationFailureJob().run(
            pool,
            _cfg(
                narration_failure_min_count="5",
                narration_failure_window_hours="12",
            ),
        )

        assert result.changes_made == 0 and findings == []
        assert pool.seen_fetchrow_args == ("narration_synthesis_failed", 12)


@pytest.mark.unit
class TestProbeTrigger:
    """The gated-outage path: dispatch defers, no failure findings exist —
    only the live probe + own-history streak can see the outage."""

    async def test_consecutive_unhealthy_pages(self, monkeypatch):
        findings: list[dict] = []
        monkeypatch.setattr(pnf, "emit_finding", lambda **kw: findings.append(kw))
        _stub_probe(monkeypatch, False, "chatterbox unreachable (ConnectError)")
        # 2 prior unhealthy runs + this one = 3 consecutive (default min)
        pool = _FakePool(0, 0, None, history=["false", "false"])

        result = await ProbeNarrationFailureJob().run(pool, _cfg())

        assert result.changes_made == 1
        assert findings[0]["severity"] == "critical"
        assert findings[0]["extra"]["consecutive_unhealthy"] == 3
        assert "unreachable" in findings[0]["title"]

    async def test_first_unhealthy_run_does_not_page(self, monkeypatch):
        """A single blip (restart window, deploy) must not page — that is
        what min_consecutive exists for."""
        findings: list[dict] = []
        monkeypatch.setattr(pnf, "emit_finding", lambda **kw: findings.append(kw))
        _stub_probe(monkeypatch, False, "unreachable")
        pool = _FakePool(0, 0, None, history=["true", "true"])

        result = await ProbeNarrationFailureJob().run(pool, _cfg())

        assert result.changes_made == 0 and findings == []
        assert result.metrics["consecutive_unhealthy"] == 1

    async def test_healthy_gap_breaks_the_streak(self, monkeypatch):
        findings: list[dict] = []
        monkeypatch.setattr(pnf, "emit_finding", lambda **kw: findings.append(kw))
        _stub_probe(monkeypatch, False, "unreachable")
        # newest-first history: last run healthy → streak = just this run
        pool = _FakePool(0, 0, None, history=["true", "false"])

        result = await ProbeNarrationFailureJob().run(pool, _cfg())

        assert result.changes_made == 0
        assert result.metrics["consecutive_unhealthy"] == 1

    async def test_no_history_requires_failure_evidence(self, monkeypatch):
        """Metrics capture off / first runs: a live miss alone must not page,
        but a live miss + at least one render-time failure must."""
        findings: list[dict] = []
        monkeypatch.setattr(pnf, "emit_finding", lambda **kw: findings.append(kw))
        _stub_probe(monkeypatch, False, "unreachable")

        r1 = await ProbeNarrationFailureJob().run(_FakePool(0, 0, None), _cfg())
        assert r1.changes_made == 0 and findings == []

        r2 = await ProbeNarrationFailureJob().run(_FakePool(1, 1, "boom"), _cfg())
        assert r2.changes_made == 1 and len(findings) == 1

    async def test_skipped_probe_never_counts_as_unhealthy(self, monkeypatch):
        """TTS disabled by config (probe returns None) must neither page nor
        poison the history with an unhealthy observation."""
        findings: list[dict] = []
        monkeypatch.setattr(pnf, "emit_finding", lambda **kw: findings.append(kw))
        _stub_probe(monkeypatch, None, "tts disabled — probe skipped")

        result = await ProbeNarrationFailureJob().run(
            _FakePool(0, 0, None, history=["false", "false"]), _cfg(),
        )

        assert result.changes_made == 0 and findings == []
        assert result.metrics["tts_healthy"] == "skipped"
        assert result.metrics["consecutive_unhealthy"] == 0


@pytest.mark.unit
class TestJobPlumbing:
    async def test_metrics_returned_even_when_healthy(self, monkeypatch):
        monkeypatch.setattr(pnf, "emit_finding", lambda **kw: None)
        _stub_probe(monkeypatch, True, "chatterbox healthy")

        result = await ProbeNarrationFailureJob().run(_FakePool(0, 0, None), _cfg())

        assert result.metrics["narration_failures"] == 0
        assert result.metrics["window_hours"] == 6
        assert result.metrics["tts_healthy"] == "true"

    async def test_disabled_via_settings(self, monkeypatch):
        monkeypatch.setattr(
            pnf, "emit_finding",
            lambda **kw: pytest.fail("must not emit when disabled"),
        )
        result = await ProbeNarrationFailureJob().run(
            _FakePool(99, 9, "x"), _cfg(narration_failure_probe_enabled="false"),
        )
        assert result.ok and result.changes_made == 0

    async def test_no_pool_fails_loud(self):
        result = await ProbeNarrationFailureJob().run(None, _cfg())
        assert result.ok is False


@pytest.mark.unit
class TestProbeTtsHelper:
    """_probe_tts itself — URL resolution shared with media_infra_health."""

    async def test_probes_configured_chatterbox(self):
        sc = SiteConfig(
            initial_config={
                "podcast_tts_enabled": "true",
                "podcast_tts_engine": "chatterbox",
            },
        )
        seen: list[str] = []

        class _FakeClient:
            def __init__(self, **_kw):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_a):
                return False

            async def get(self, url):
                seen.append(url)
                resp = MagicMock()
                resp.status_code = 200
                return resp

        healthy, detail = await pnf._probe_tts(sc, http_client_factory=_FakeClient)
        assert healthy is True
        assert seen == ["http://chatterbox:8000/health"]
        assert "chatterbox" in detail

    async def test_unreachable_reports_unhealthy(self):
        sc = SiteConfig(initial_config={"podcast_tts_enabled": "true"})

        class _DeadClient:
            def __init__(self, **_kw):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_a):
                return False

            async def get(self, url):
                raise ConnectionError(f"refused: {url}")

        healthy, detail = await pnf._probe_tts(sc, http_client_factory=_DeadClient)
        assert healthy is False
        assert "unreachable" in detail

    async def test_tts_disabled_skips(self):
        sc = SiteConfig(initial_config={"podcast_tts_enabled": "false"})
        healthy, detail = await pnf._probe_tts(
            sc,
            http_client_factory=lambda **_kw: pytest.fail("must not build a client"),
        )
        assert healthy is None
        assert "skipped" in detail
