"""Unit tests for brain/pr_staleness_probe.py.

Covers the five acceptance scenarios from the issue spec:

1. No open PRs -> ok, no alert.
2. PR age < threshold -> skipped (too young).
3. PR age >= threshold but CI failing -> skipped (not actionable).
4. PR age >= threshold AND CI green -> alert emitted with the right
   fingerprint + severity.
5. Same PR seen twice within the dedup window -> second cycle suppressed.

All external I/O (asyncpg pool, GitHub API via httpx) is mocked. The
pool is a MagicMock whose async methods are AsyncMocks; we seed
app_settings reads via the ``setting_values`` dict passed to
``_make_pool``. The httpx client is replaced via the
``http_client_factory`` injection seam.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

# pythonpath in pyproject.toml includes "../.." so the brain package
# resolves the same way the other brain probe tests import it.
from poindexter.brain import pr_staleness_probe as psp

# ---------------------------------------------------------------------------
# Helpers — fixed clock, pool builder, fake httpx client
# ---------------------------------------------------------------------------


_FIXED_NOW = datetime(2026, 5, 7, 12, 0, 0, tzinfo=timezone.utc)


def _now_fn():
    return _FIXED_NOW


def _default_settings() -> dict[str, str]:
    """Match the migration's seed values."""
    return {
        psp.ENABLED_KEY: "true",
        psp.POLL_INTERVAL_MINUTES_KEY: "60",
        psp.MIN_HOURS_KEY: "24",
        psp.DEDUP_HOURS_KEY: "12",
        psp.REPO_KEY: "Test-Org/test-repo",
        psp.MAX_PRS_PER_ALERT_KEY: "5",
        # gh_token row exists with a value so _read_token short-circuits
        # without falling through to the GITHUB_TOKEN env var (which the
        # secret_reader path would otherwise need to be mocked for).
        "gh_token": "test-token",
    }


def _make_pool(
    *,
    setting_values: dict[str, str] | None = None,
    deduped_fingerprints: set[str] | None = None,
):
    """Build an asyncpg-style mock pool that:

    - returns ``setting_values[key]`` for ``SELECT value FROM app_settings``
      lookups via ``fetchval``,
    - returns the fingerprint+is_secret row for the gh_token secret_reader
      lookup (the brain secret_reader uses ``fetchrow`` for that),
    - reports each fingerprint in ``deduped_fingerprints`` as having a
      fresh ``last_seen_at`` (now) so the dedup gate suppresses,
    - records every ``execute`` call so tests can assert on what was
      written (alert_events, alert_dedup_state, audit_log).
    """
    pool = MagicMock()
    settings = {**_default_settings(), **(setting_values or {})}
    deduped = deduped_fingerprints or set()

    async def _fetchval(query, *args):
        if "app_settings" in query and args:
            return settings.get(args[0])
        return None

    async def _fetchrow(query, *args):
        if "app_settings" in query and args:
            # secret_reader uses fetchrow with SELECT value, is_secret.
            key = args[0]
            if key in settings:
                # gh_token is is_secret=true; treat the test-supplied
                # value as plaintext so the decrypt branch is skipped.
                return {"value": settings[key], "is_secret": False}
            return None
        if "alert_dedup_state" in query and args:
            fp = args[0]
            if fp in deduped:
                return {"last_seen_at": _FIXED_NOW}
            return None
        return None

    pool.fetchval = AsyncMock(side_effect=_fetchval)
    pool.fetchrow = AsyncMock(side_effect=_fetchrow)
    pool.execute = AsyncMock()
    pool.fetch = AsyncMock(return_value=[])
    return pool


def _executed_alert_events(pool) -> list[dict[str, Any]]:
    """Pull every alert_events INSERT made by the probe, structured."""
    out: list[dict[str, Any]] = []
    for call in pool.execute.call_args_list:
        sql = call.args[0]
        if "INSERT INTO alert_events" not in sql:
            continue
        out.append({
            "alertname": call.args[1],
            "labels_json": call.args[2],
            "annotations_json": call.args[3],
            "fingerprint": call.args[4],
        })
    return out


def _executed_dedup_upserts(pool) -> list[str]:
    """Pull the fingerprint args from every alert_dedup_state INSERT."""
    out: list[str] = []
    for call in pool.execute.call_args_list:
        sql = call.args[0]
        if "INSERT INTO alert_dedup_state" not in sql:
            continue
        out.append(call.args[1])
    return out


def _make_pr(
    *,
    number: int,
    age_hours: float,
    title: str = "test PR",
    sha: str = "deadbeef",
    author: str = "claude",
    additions: int = 100,
    deletions: int = 5,
) -> dict[str, Any]:
    """Build a minimal GitHub PR payload."""
    created_at = _FIXED_NOW - timedelta(hours=age_hours)
    return {
        "number": number,
        "title": title,
        "created_at": created_at.isoformat().replace("+00:00", "Z"),
        "head": {"sha": sha},
        "user": {"login": author},
        "additions": additions,
        "deletions": deletions,
    }


def _make_workflow_runs(*, all_green: bool) -> dict[str, Any]:
    """Build a GitHub Actions ``/actions/runs`` payload with one green or failing run."""
    conclusion = "success" if all_green else "failure"
    return {
        "total_count": 1,
        "workflow_runs": [
            {
                "id": 1, "workflow_id": 10, "name": "ci",
                "created_at": "2026-05-06T10:00:00Z",
                "status": "completed", "conclusion": conclusion,
            },
        ],
    }


class _FakeResponse:
    """Minimal httpx.Response stand-in supporting .status_code + .json()."""

    def __init__(self, status_code: int, payload: Any, text: str = ""):
        self.status_code = status_code
        self._payload = payload
        self.text = text or ""

    def json(self):
        return self._payload


class _FakeAsyncClient:
    """httpx.AsyncClient stand-in routing GETs by URL substring."""

    def __init__(self, *, prs: list[dict[str, Any]], runs_by_sha: dict[str, dict[str, Any]]):
        self._prs = prs
        self._runs_by_sha = runs_by_sha
        self.calls: list[str] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def get(self, url: str, params: dict | None = None):
        self.calls.append(url)
        if "/pulls" in url:
            return _FakeResponse(200, self._prs)
        if "/actions/runs" in url:
            # /repos/{repo}/actions/runs?head_sha={sha}
            sha = (params or {}).get("head_sha")
            return _FakeResponse(
                200,
                self._runs_by_sha.get(sha, {"workflow_runs": []}),
            )
        return _FakeResponse(404, {}, text="not found")


def _factory_for(
    *,
    prs: list[dict[str, Any]],
    runs_by_sha: dict[str, dict[str, Any]] | None = None,
):
    """Build an http_client_factory that returns one canned client."""
    crs = runs_by_sha or {}

    def _factory():
        return _FakeAsyncClient(prs=prs, runs_by_sha=crs)

    return _factory


# ---------------------------------------------------------------------------
# Module-state isolation between scenarios
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clear_module_state():
    """Reset the cadence-gate dedup memory between scenarios."""
    psp._reset_state()
    yield
    psp._reset_state()


# ---------------------------------------------------------------------------
# Test 1 — no open PRs -> ok, no alert
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestNoOpenPRs:
    @pytest.mark.asyncio
    async def test_zero_prs_no_alert(self):
        pool = _make_pool()
        factory = _factory_for(prs=[])

        notify_calls: list[dict[str, Any]] = []

        def fake_notify(**kwargs):
            notify_calls.append(kwargs)

        summary = await psp.run_pr_staleness_probe(
            pool,
            now_fn=_now_fn,
            notify_fn=fake_notify,
            http_client_factory=factory,
        )

        assert summary["ok"] is True
        assert summary["status"] == "no_stale_prs"
        assert summary["stale_prs"] == 0
        assert summary["alert_emitted"] is False
        assert summary["pr_count_seen"] == 0
        # No alert_events INSERT.
        assert _executed_alert_events(pool) == []
        # No dedup writes.
        assert _executed_dedup_upserts(pool) == []
        # Loud-failure notify was NOT used on the success path.
        assert notify_calls == []


# ---------------------------------------------------------------------------
# Test 2 — PR age < threshold -> skipped (too young)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestYoungPRSkipped:
    @pytest.mark.asyncio
    async def test_pr_below_min_hours_no_alert(self):
        pool = _make_pool()
        # 5h old — below the 24h default.
        factory = _factory_for(
            prs=[_make_pr(number=101, age_hours=5)],
            runs_by_sha={},  # never queried
        )

        summary = await psp.run_pr_staleness_probe(
            pool,
            now_fn=_now_fn,
            http_client_factory=factory,
        )

        assert summary["ok"] is True
        assert summary["status"] == "no_stale_prs"
        assert summary["stale_prs"] == 0
        assert summary["skipped_too_young"] == 1
        assert summary["pr_count_seen"] == 1
        assert _executed_alert_events(pool) == []
        assert _executed_dedup_upserts(pool) == []


# ---------------------------------------------------------------------------
# Test 3 — PR age >= threshold but CI failing -> skipped
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCIRedSkipped:
    @pytest.mark.asyncio
    async def test_old_pr_with_failing_ci_skipped(self):
        pool = _make_pool()
        factory = _factory_for(
            prs=[_make_pr(number=202, age_hours=30, sha="failingsha")],
            runs_by_sha={
                "failingsha": _make_workflow_runs(all_green=False),
            },
        )

        summary = await psp.run_pr_staleness_probe(
            pool,
            now_fn=_now_fn,
            http_client_factory=factory,
        )

        assert summary["ok"] is True
        assert summary["status"] == "no_stale_prs"
        assert summary["stale_prs"] == 0
        assert summary["skipped_ci_not_green"] == 1
        assert summary["skipped_too_young"] == 0
        assert _executed_alert_events(pool) == []
        assert _executed_dedup_upserts(pool) == []


# ---------------------------------------------------------------------------
# Test 4 — old PR with green CI -> alert + correct fingerprint + severity
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestStalePRAlerts:
    @pytest.mark.asyncio
    async def test_old_pr_with_green_ci_emits_alert(self):
        pool = _make_pool()
        factory = _factory_for(
            prs=[
                _make_pr(
                    number=309,
                    age_hours=28,
                    title="test: coverage audit + quick wins",
                    sha="green309",
                    author="claude",
                    additions=938,
                    deletions=12,
                ),
            ],
            runs_by_sha={
                "green309": _make_workflow_runs(all_green=True),
            },
        )

        summary = await psp.run_pr_staleness_probe(
            pool,
            now_fn=_now_fn,
            http_client_factory=factory,
        )

        assert summary["ok"] is True
        assert summary["status"] == "alert_emitted"
        assert summary["stale_prs"] == 1
        assert summary["alert_emitted"] is True
        assert summary["pr_numbers"] == [309]

        events = _executed_alert_events(pool)
        assert len(events) == 1
        ev = events[0]
        assert ev["alertname"] == "pr_stale_Test-Org_test-repo"
        # Severity is hardcoded into the SQL ('warning'); routing via
        # the alert_dispatcher sends warnings to Discord only, which is
        # the desired channel for stale PRs (no Telegram pages).
        # Annotations include the rendered Discord body.
        import json as _json
        ann = _json.loads(ev["annotations_json"])
        assert "309" in ann["pr_numbers"]
        assert "test: coverage audit + quick wins" in ann["description"]
        assert "28h, +938/-12, by claude" in ann["description"]
        assert "older than 24h with green CI" in ann["description"]

        # Per-PR dedup row was written with the right fingerprint.
        upserts = _executed_dedup_upserts(pool)
        assert upserts == ["pr_stale_Test-Org/test-repo_309"]


# ---------------------------------------------------------------------------
# Test 5 — same PR within dedup window -> second cycle suppressed
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestDedupSuppression:
    @pytest.mark.asyncio
    async def test_same_pr_inside_dedup_window_suppressed_second_cycle(self):
        # Cycle 2 sees the same fingerprint already recorded in
        # alert_dedup_state -> the PR is skipped before the alert_events
        # insert. We simulate "cycle 1 already happened" by pre-seeding
        # the dedup set passed to the pool.
        deduped = {"pr_stale_Test-Org/test-repo_309"}
        pool = _make_pool(deduped_fingerprints=deduped)
        factory = _factory_for(
            prs=[
                _make_pr(
                    number=309,
                    age_hours=30,  # still > 24h
                    title="same PR as last cycle",
                    sha="green309",
                ),
            ],
            runs_by_sha={
                "green309": _make_workflow_runs(all_green=True),
            },
        )

        summary = await psp.run_pr_staleness_probe(
            pool,
            now_fn=_now_fn,
            http_client_factory=factory,
        )

        assert summary["ok"] is True
        assert summary["status"] == "no_stale_prs"
        assert summary["stale_prs"] == 0
        assert summary["skipped_deduped"] == 1
        assert summary["pr_count_seen"] == 1
        # Critically: NO alert_events row this cycle.
        assert _executed_alert_events(pool) == []
        # NO dedup upsert this cycle either — we already deduped.
        assert _executed_dedup_upserts(pool) == []


# ---------------------------------------------------------------------------
# Bonus — disabled flag short-circuits before any HTTP I/O
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestDisabledFlag:
    @pytest.mark.asyncio
    async def test_disabled_returns_disabled_status(self):
        pool = _make_pool(setting_values={psp.ENABLED_KEY: "false"})

        called: list[bool] = []

        def factory():  # pragma: no cover — must NOT be called
            called.append(True)
            raise AssertionError("HTTP client should not be built when disabled")

        summary = await psp.run_pr_staleness_probe(
            pool,
            now_fn=_now_fn,
            http_client_factory=factory,
        )

        assert summary["ok"] is True
        assert summary["status"] == "disabled"
        assert summary["alert_emitted"] is False
        assert called == []
        assert _executed_alert_events(pool) == []


# ---------------------------------------------------------------------------
# CI green is read from GitHub Actions runs, not check runs
# ---------------------------------------------------------------------------


def _run(workflow_id: int, conclusion: str | None, *, status: str = "completed",
         created_at: str = "2026-05-06T10:00:00Z", run_id: int = 1) -> dict[str, Any]:
    return {
        "id": run_id, "workflow_id": workflow_id, "name": f"wf-{workflow_id}",
        "created_at": created_at, "status": status, "conclusion": conclusion,
    }


@pytest.mark.unit
class TestCIGreenFromActionsRuns:
    """Why Actions: a fine-grained token has no Checks permission, so the
    check-runs endpoint is unreadable on a private repo. Why skipped counts as
    green: every glad-labs-stack PR carries skipped path-filtered jobs, and
    treating them as failures kept the probe from flagging any PR from August
    2026 (#4041 merged CLEAN with 14 successful + 8 skipped check runs)."""

    def test_skipped_and_neutral_workflows_do_not_make_a_pr_red(self):
        runs = [_run(1, "success"), _run(2, "skipped", run_id=2), _run(3, "neutral", run_id=3)]
        assert psp._ci_all_green(runs) is True

    def test_only_skipped_runs_are_not_a_confirmed_pass(self):
        assert psp._ci_all_green([_run(1, "skipped"), _run(2, "skipped", run_id=2)]) is False

    def test_no_runs_are_not_green(self):
        assert psp._ci_all_green([]) is False

    @pytest.mark.parametrize("status", ["queued", "in_progress", "waiting", "pending"])
    def test_an_unfinished_run_is_not_green(self, status):
        runs = [_run(1, "success"), _run(2, None, status=status, run_id=2)]
        assert psp._ci_all_green(runs) is False

    @pytest.mark.parametrize(
        "conclusion",
        ["failure", "cancelled", "timed_out", "action_required", "startup_failure", "stale"],
    )
    def test_a_bad_conclusion_is_not_green(self, conclusion):
        runs = [_run(1, "success"), _run(2, conclusion, run_id=2)]
        assert psp._ci_all_green(runs) is False

    def test_each_workflow_is_judged_by_its_latest_run(self):
        cancelled_then_green = [
            _run(1, "cancelled", created_at="2026-05-06T10:00:00Z", run_id=1),
            _run(1, "success", created_at="2026-05-06T10:05:00Z", run_id=2),
        ]
        green_then_failed = [
            _run(1, "success", created_at="2026-05-06T10:00:00Z", run_id=1),
            _run(1, "failure", created_at="2026-05-06T10:05:00Z", run_id=2),
        ]
        assert psp._ci_all_green(cancelled_then_green) is True
        assert psp._ci_all_green(green_then_failed) is False

    @pytest.mark.asyncio
    async def test_a_pr_with_skipped_workflows_now_raises_the_stale_alert(self):
        pool = _make_pool()
        client = _FakeAsyncClient(
            prs=[_make_pr(number=4041, age_hours=30, sha="clean4041")],
            runs_by_sha={"clean4041": {"workflow_runs": [
                _run(1, "success"), _run(2, "skipped", run_id=2),
            ]}},
        )

        summary = await psp.run_pr_staleness_probe(
            pool, now_fn=_now_fn, http_client_factory=lambda: client,
        )

        assert summary["status"] == "alert_emitted"
        assert summary["pr_numbers"] == [4041]
        assert summary["skipped_ci_not_green"] == 0
        assert any("/actions/runs" in url for url in client.calls)
        assert not any("check-runs" in url for url in client.calls)
