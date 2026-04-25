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
    hp._last_run.clear()
    hp._failure_counts.clear()
    hp._created_issues.clear()
    hp._last_remediation.clear()
    hp._config_synced = False
    yield
    hp._last_run.clear()
    hp._failure_counts.clear()
    hp._created_issues.clear()
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


@pytest.mark.unit
class TestCreateGiteaIssue:
    def test_skips_duplicate(self):
        hp._created_issues.add("already_made")
        with patch("urllib" + ".request.urlopen") as mock_open:
            hp._create_gitea_issue("already_made", "some detail")
        mock_open.assert_not_called()

    def test_creates_when_new(self):
        fake_resp = MagicMock()
        fake_resp.status = 201
        with patch("urllib" + ".request.urlopen", return_value=fake_resp):
            hp._create_gitea_issue("fresh_probe", "went boom")
        assert "fresh_probe" in hp._created_issues


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
