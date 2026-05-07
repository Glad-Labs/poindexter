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
from typing import Any, Optional
from unittest.mock import AsyncMock, MagicMock

import pytest

# pythonpath in pyproject.toml includes "../.." so the brain package
# resolves the same way the other brain probe tests import it.
from brain import pr_staleness_probe as psp


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
        psp.REPO_KEY: "Glad-Labs/glad-labs-stack",
        psp.MAX_PRS_PER_ALERT_KEY: "5",
        # gh_token row exists with a value so _read_token short-circuits
        # without falling through to the GITHUB_TOKEN env var (which the
        # secret_reader path would otherwise need to be mocked for).
        "gh_token": "test-token",
    }


def _make_pool(
    *,
    setting_values: Optional[dict[str, str]] = None,
    deduped_fingerprints: Optional[set[str]] = None,
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


def _make_check_runs(*, all_green: bool) -> dict[str, Any]:
    """Build a GitHub check-runs payload with one green or failing run."""
    if all_green:
        return {
            "total_count": 1,
            "check_runs": [
                {"status": "completed", "conclusion": "success", "name": "ci"},
            ],
        }
    return {
        "total_count": 1,
        "check_runs": [
            {"status": "completed", "conclusion": "failure", "name": "ci"},
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

    def __init__(self, *, prs: list[dict[str, Any]], check_runs_by_sha: dict[str, dict[str, Any]]):
        self._prs = prs
        self._check_runs_by_sha = check_runs_by_sha
        self.calls: list[str] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def get(self, url: str, params: Optional[dict] = None):
        self.calls.append(url)
        if "/pulls" in url:
            return _FakeResponse(200, self._prs)
        if "/check-runs" in url:
            # url shape: /repos/{repo}/commits/{sha}/check-runs
            sha = url.split("/commits/")[-1].split("/check-runs")[0]
            return _FakeResponse(
                200,
                self._check_runs_by_sha.get(sha, {"check_runs": []}),
            )
        return _FakeResponse(404, {}, text="not found")


def _factory_for(
    *,
    prs: list[dict[str, Any]],
    check_runs_by_sha: Optional[dict[str, dict[str, Any]]] = None,
):
    """Build an http_client_factory that returns one canned client."""
    crs = check_runs_by_sha or {}

    def _factory():
        return _FakeAsyncClient(prs=prs, check_runs_by_sha=crs)

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
            check_runs_by_sha={},  # never queried
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
            check_runs_by_sha={
                "failingsha": _make_check_runs(all_green=False),
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
            check_runs_by_sha={
                "green309": _make_check_runs(all_green=True),
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
        assert ev["alertname"] == "pr_stale_Glad-Labs_glad-labs-stack"
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
        assert upserts == ["pr_stale_Glad-Labs/glad-labs-stack_309"]


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
        deduped = {"pr_stale_Glad-Labs/glad-labs-stack_309"}
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
            check_runs_by_sha={
                "green309": _make_check_runs(all_green=True),
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
