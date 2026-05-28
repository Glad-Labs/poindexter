"""Unit tests for brain/prefect_stuck_flow_probe.py.

Pins the 2026-05-26 regression: ``romantic-harrier`` content_generation
flow stuck in state=RUNNING for 35h without a direct alert. The probe's
job is to detect such runs early, page once per cycle, and optionally
auto-CRASHED them when the operator opts in.

External I/O (httpx, notify_operator) is mocked. The pool is a MagicMock
with AsyncMock methods, seeded via the ``setting_values`` dict on
``_make_pool``.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from brain import prefect_stuck_flow_probe as psfp


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_pool(*, setting_values: dict[str, str] | None = None):
    """asyncpg-style mock pool that returns canned settings + records writes."""
    pool = MagicMock()
    settings = dict(setting_values or {})

    async def _fetchval(query, *args):
        if "app_settings" in query and args:
            return settings.get(args[0])
        return None

    audit_rows: list[tuple[Any, ...]] = []

    async def _execute(query, *args):
        audit_rows.append((query, args))
        return None

    pool.fetchval = AsyncMock(side_effect=_fetchval)
    pool.execute = AsyncMock(side_effect=_execute)
    pool._audit_rows = audit_rows  # type: ignore[attr-defined]
    return pool


class _MockResponse:
    def __init__(self, status_code: int, json_data: Any = None, text: str = ""):
        self.status_code = status_code
        self._json = json_data if json_data is not None else []
        self.text = text

    def json(self):
        return self._json


class _MockHttpClient:
    """Minimal stand-in for an ``httpx.AsyncClient`` async context manager.

    Records every ``.post`` call so tests can assert what was sent;
    serves canned responses keyed by URL path suffix.
    """

    def __init__(self, responses: dict[str, _MockResponse]):
        self._responses = responses
        self.posts: list[tuple[str, dict[str, Any]]] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url: str, *, json=None, **_kwargs):  # noqa: A002
        self.posts.append((url, json or {}))
        for path_suffix, resp in self._responses.items():
            if url.endswith(path_suffix):
                return resp
        return _MockResponse(404, text="no mock for URL")


def _iso_minutes_ago(minutes: int) -> str:
    """ISO-8601 UTC timestamp for ``minutes`` ago (Z-suffixed Prefect form)."""
    when = datetime.now(timezone.utc) - timedelta(minutes=minutes)
    return when.isoformat().replace("+00:00", "Z")


def _run(*, run_id: str, name: str, minutes_ago: int) -> dict[str, Any]:
    """Build a fake Prefect flow_run dict — minimum fields the probe reads."""
    return {
        "id": run_id,
        "name": name,
        "start_time": _iso_minutes_ago(minutes_ago),
        "state": {"type": "RUNNING", "timestamp": _iso_minutes_ago(minutes_ago)},
    }


def _pending_run(*, run_id: str, name: str, minutes_ago: int) -> dict[str, Any]:
    """Build a fake PENDING/Submitting flow_run dict.

    Real PENDING runs have ``start_time=None`` (they never transitioned
    to RUNNING). ``state.timestamp`` records when the run entered
    PENDING — that is the field the probe reads for age.
    """
    return {
        "id": run_id,
        "name": name,
        "start_time": None,
        "created": _iso_minutes_ago(minutes_ago),
        "state": {
            "type": "PENDING",
            "name": "Submitting",
            "timestamp": _iso_minutes_ago(minutes_ago),
        },
    }


# ---------------------------------------------------------------------------
# _age_minutes — pure parser, exercised directly for edge coverage
# ---------------------------------------------------------------------------


def test_age_minutes_parses_z_suffix():
    """Prefect's API emits Z-suffixed ISO; the probe normalizes it to
    +00:00 before fromisoformat. Pin the parse path."""
    iso = _iso_minutes_ago(45)
    assert iso.endswith("Z")
    age = psfp._age_minutes(iso)
    assert age is not None and 44 <= age <= 46


def test_age_minutes_parses_plus_zero_suffix():
    """Some Prefect responses also emit +00:00; the same code path
    handles both shapes."""
    when = datetime.now(timezone.utc) - timedelta(minutes=10)
    iso = when.isoformat()  # +00:00 form
    age = psfp._age_minutes(iso)
    assert age is not None and 9 <= age <= 11


def test_age_minutes_returns_none_on_missing_input():
    assert psfp._age_minutes(None) is None
    assert psfp._age_minutes("") is None


def test_age_minutes_returns_none_on_garbage():
    assert psfp._age_minutes("not a timestamp") is None


# ---------------------------------------------------------------------------
# run_prefect_stuck_flow_probe — full cycle behavior
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_disabled_via_setting_short_circuits():
    """Operator-flippable kill switch — the probe must no-op cleanly
    when ``prefect_stuck_flow_probe_enabled=false``."""
    pool = _make_pool(setting_values={"prefect_stuck_flow_probe_enabled": "false"})
    notify = MagicMock()
    summary = await psfp.run_prefect_stuck_flow_probe(
        pool, notify_fn=notify, http_client_factory=lambda: _MockHttpClient({}),
    )
    assert summary["status"] == "disabled"
    notify.assert_not_called()
    assert pool._audit_rows == []


@pytest.mark.asyncio
async def test_no_running_flows_returns_empty_summary():
    """Quiet day — Prefect reports zero RUNNING flows. Probe records
    the cycle but doesn't page or audit."""
    pool = _make_pool()
    notify = MagicMock()
    client = _MockHttpClient({
        "/flow_runs/filter": _MockResponse(200, json_data=[]),
    })
    summary = await psfp.run_prefect_stuck_flow_probe(
        pool, notify_fn=notify, http_client_factory=lambda: client,
    )
    assert summary["status"] == "ran"
    assert summary["running_seen"] == 0
    assert summary["stuck_count"] == 0
    notify.assert_not_called()


@pytest.mark.asyncio
async def test_running_under_threshold_is_not_stuck():
    """A flow that's been running 5 minutes is not stuck — threshold
    default is 30 minutes. No page, no audit."""
    pool = _make_pool()
    notify = MagicMock()
    client = _MockHttpClient({
        "/flow_runs/filter": _MockResponse(200, json_data=[
            _run(run_id="abc", name="happy-cat", minutes_ago=5),
        ]),
    })
    summary = await psfp.run_prefect_stuck_flow_probe(
        pool, notify_fn=notify, http_client_factory=lambda: client,
    )
    assert summary["stuck_count"] == 0
    assert summary["running_seen"] == 1
    notify.assert_not_called()


@pytest.mark.asyncio
async def test_stuck_flow_pages_operator_without_auto_crash():
    """The captured 2026-05-26 regression: 35h-old RUNNING flow. With
    auto_crash=false (default), the probe pages + audits but doesn't
    touch the flow run state — the operator decides whether to crash
    or wait."""
    pool = _make_pool()
    notify = MagicMock()
    client = _MockHttpClient({
        "/flow_runs/filter": _MockResponse(200, json_data=[
            _run(run_id="019e5cb0", name="romantic-harrier", minutes_ago=2100),
        ]),
    })
    summary = await psfp.run_prefect_stuck_flow_probe(
        pool, notify_fn=notify, http_client_factory=lambda: client,
    )
    assert summary["stuck_count"] == 1
    assert summary["auto_crashed_count"] == 0
    assert summary["crash_failed_count"] == 0

    notify.assert_called_once()
    kwargs = notify.call_args.kwargs
    assert "romantic-harrier" in kwargs["title"]
    assert kwargs["severity"] == "warning"
    assert "set_state" in kwargs["detail"]  # manual-unstick hint

    # Audit row recorded — no crash event yet because auto_crash=false.
    audit_event_types = [
        args[0] for query, args in pool._audit_rows
        if "audit_log" in query
    ]
    assert "probe.prefect_stuck_flow_detected" in audit_event_types
    assert "probe.prefect_stuck_flow_auto_crashed" not in audit_event_types

    # Only the filter POST — no /set_state when auto_crash=false.
    assert len(client.posts) == 1
    assert client.posts[0][0].endswith("/flow_runs/filter")


@pytest.mark.asyncio
async def test_stuck_flow_auto_crashes_when_opted_in():
    """With auto_crash=true the probe issues a force-CRASHED set_state
    and records both the detection and the crash in audit_log."""
    pool = _make_pool(setting_values={
        "prefect_stuck_flow_auto_crash": "true",
    })
    notify = MagicMock()
    client = _MockHttpClient({
        "/flow_runs/filter": _MockResponse(200, json_data=[
            _run(run_id="abc-123", name="romantic-harrier", minutes_ago=120),
        ]),
        "/set_state": _MockResponse(201, json_data={
            "state": {"type": "CRASHED"},
        }),
    })
    summary = await psfp.run_prefect_stuck_flow_probe(
        pool, notify_fn=notify, http_client_factory=lambda: client,
    )
    assert summary["stuck_count"] == 1
    assert summary["auto_crashed_count"] == 1
    assert summary["crash_failed_count"] == 0

    # Two POSTs — filter, then set_state with force=true.
    paths = [p[0].rsplit("/", 1)[-1] for p in client.posts]
    assert "filter" in paths
    assert "set_state" in paths
    set_state_body = next(
        body for url, body in client.posts if url.endswith("/set_state")
    )
    assert set_state_body["force"] is True
    assert set_state_body["state"]["type"] == "CRASHED"

    audit_event_types = [
        args[0] for query, args in pool._audit_rows
        if "audit_log" in query
    ]
    assert "probe.prefect_stuck_flow_detected" in audit_event_types
    assert "probe.prefect_stuck_flow_auto_crashed" in audit_event_types


@pytest.mark.asyncio
async def test_set_state_failure_records_crash_failed():
    """If Prefect rejects the set_state (e.g. 422 invalid transition),
    the probe records the failure separately so the operator sees the
    crash didn't take effect."""
    pool = _make_pool(setting_values={
        "prefect_stuck_flow_auto_crash": "true",
    })
    notify = MagicMock()
    client = _MockHttpClient({
        "/flow_runs/filter": _MockResponse(200, json_data=[
            _run(run_id="r1", name="r1", minutes_ago=120),
        ]),
        "/set_state": _MockResponse(422, text="state transition rejected"),
    })
    summary = await psfp.run_prefect_stuck_flow_probe(
        pool, notify_fn=notify, http_client_factory=lambda: client,
    )
    assert summary["stuck_count"] == 1
    assert summary["auto_crashed_count"] == 0
    assert summary["crash_failed_count"] == 1


@pytest.mark.asyncio
async def test_prefect_api_error_returns_error_summary():
    """Prefect API 500 → probe degrades to status=ran with running_seen=0
    (the filter call returned no usable data). The brain cycle continues."""
    pool = _make_pool()
    notify = MagicMock()
    client = _MockHttpClient({
        "/flow_runs/filter": _MockResponse(500, text="prefect internal error"),
    })
    summary = await psfp.run_prefect_stuck_flow_probe(
        pool, notify_fn=notify, http_client_factory=lambda: client,
    )
    assert summary["ok"] is True
    assert summary["running_seen"] == 0
    assert summary["stuck_count"] == 0
    notify.assert_not_called()


@pytest.mark.asyncio
async def test_filter_payload_uses_flow_names_setting():
    """Operator-configurable flow_names — the POST body must carry the
    parsed list. Pins the seam so a future operator who adds
    ``dev_diary_compositor`` to the comma-separated setting gets it
    actually queried.

    The state filter must include both RUNNING and PENDING so the
    PENDING-stranded case (Glad-Labs/poindexter#518) is caught in the
    same single API call.
    """
    pool = _make_pool(setting_values={
        "prefect_stuck_flow_flow_names": "content_generation, custom_flow",
    })
    notify = MagicMock()
    client = _MockHttpClient({
        "/flow_runs/filter": _MockResponse(200, json_data=[]),
    })
    await psfp.run_prefect_stuck_flow_probe(
        pool, notify_fn=notify, http_client_factory=lambda: client,
    )
    assert len(client.posts) == 1
    body = client.posts[0][1]
    assert body["flows"]["name"]["any_"] == ["content_generation", "custom_flow"]
    assert body["flow_runs"]["state"]["type"]["any_"] == ["RUNNING", "PENDING"]


@pytest.mark.asyncio
async def test_threshold_setting_respected():
    """Operator lowers threshold to 10m; a 15m-old run flips from
    'normal' to 'stuck'. Pin the threshold seam."""
    pool = _make_pool(setting_values={
        "prefect_stuck_flow_threshold_minutes": "10",
    })
    notify = MagicMock()
    client = _MockHttpClient({
        "/flow_runs/filter": _MockResponse(200, json_data=[
            _run(run_id="r1", name="r1", minutes_ago=15),
        ]),
    })
    summary = await psfp.run_prefect_stuck_flow_probe(
        pool, notify_fn=notify, http_client_factory=lambda: client,
    )
    assert summary["stuck_count"] == 1
    notify.assert_called_once()


# ---------------------------------------------------------------------------
# PENDING / Submitting detection — Glad-Labs/poindexter#518
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pending_under_threshold_is_not_stuck():
    """A flow that's been PENDING for 2 minutes is not stuck — default
    PENDING threshold is 5 minutes. No page, no audit."""
    pool = _make_pool()
    notify = MagicMock()
    client = _MockHttpClient({
        "/flow_runs/filter": _MockResponse(200, json_data=[
            _pending_run(run_id="abc", name="just-claimed", minutes_ago=2),
        ]),
    })
    summary = await psfp.run_prefect_stuck_flow_probe(
        pool, notify_fn=notify, http_client_factory=lambda: client,
    )
    assert summary["stuck_count"] == 0
    assert summary["running_seen"] == 1
    notify.assert_not_called()


@pytest.mark.asyncio
async def test_stranded_pending_flow_pages_operator():
    """The Glad-Labs/poindexter#518 regression: a PENDING/Submitting
    flow run that's been stranded for 50+ hours because the worker
    died between claim and fork. Probe pages + audits with the
    state-aware title."""
    pool = _make_pool()
    notify = MagicMock()
    client = _MockHttpClient({
        "/flow_runs/filter": _MockResponse(200, json_data=[
            _pending_run(run_id="019e6010", name="smoky-chowchow", minutes_ago=3000),
        ]),
    })
    summary = await psfp.run_prefect_stuck_flow_probe(
        pool, notify_fn=notify, http_client_factory=lambda: client,
    )
    assert summary["stuck_count"] == 1
    assert summary["auto_crashed_count"] == 0

    notify.assert_called_once()
    kwargs = notify.call_args.kwargs
    # Title must surface the PENDING state so the operator can tell
    # the two failure modes apart at a glance.
    assert "PENDING" in kwargs["title"]
    assert "smoky-chowchow" in kwargs["title"]
    assert kwargs["severity"] == "warning"
    # Detail mentions PENDING in the state= clause.
    assert "state=PENDING" in kwargs["detail"]

    audit_event_types = [
        args[0] for query, args in pool._audit_rows
        if "audit_log" in query
    ]
    assert "probe.prefect_stuck_flow_detected" in audit_event_types


@pytest.mark.asyncio
async def test_pending_threshold_setting_respected():
    """Operator can tune the PENDING threshold independently of the
    RUNNING threshold. Lower it to 1m → a 2m-old PENDING run flips
    to stuck."""
    pool = _make_pool(setting_values={
        "prefect_stuck_flow_pending_threshold_minutes": "1",
    })
    notify = MagicMock()
    client = _MockHttpClient({
        "/flow_runs/filter": _MockResponse(200, json_data=[
            _pending_run(run_id="r1", name="r1", minutes_ago=2),
        ]),
    })
    summary = await psfp.run_prefect_stuck_flow_probe(
        pool, notify_fn=notify, http_client_factory=lambda: client,
    )
    assert summary["stuck_count"] == 1
    notify.assert_called_once()


@pytest.mark.asyncio
async def test_pending_thresholds_are_independent_of_running():
    """RUNNING and PENDING share the same probe but their thresholds
    are tuned separately. A 10m RUNNING run (under default 30m) is
    NOT stuck even when the PENDING threshold is 1m."""
    pool = _make_pool(setting_values={
        "prefect_stuck_flow_pending_threshold_minutes": "1",
    })
    notify = MagicMock()
    client = _MockHttpClient({
        "/flow_runs/filter": _MockResponse(200, json_data=[
            _run(run_id="run-r", name="running-fine", minutes_ago=10),
            _pending_run(run_id="run-p", name="pending-stuck", minutes_ago=10),
        ]),
    })
    summary = await psfp.run_prefect_stuck_flow_probe(
        pool, notify_fn=notify, http_client_factory=lambda: client,
    )
    assert summary["stuck_count"] == 1
    assert summary["running_seen"] == 2
    # Only the PENDING run should be paged.
    paged_titles = [c.kwargs["title"] for c in notify.call_args_list]
    assert any("pending-stuck" in t for t in paged_titles)
    assert not any("running-fine" in t for t in paged_titles)


@pytest.mark.asyncio
async def test_stranded_pending_auto_crashes_when_opted_in():
    """When auto_crash=true is on, PENDING strands also get the
    force-CRASHED treatment — freeing the work-pool slot without
    operator intervention."""
    pool = _make_pool(setting_values={
        "prefect_stuck_flow_auto_crash": "true",
    })
    notify = MagicMock()
    client = _MockHttpClient({
        "/flow_runs/filter": _MockResponse(200, json_data=[
            _pending_run(run_id="run-pp", name="stuck-pending", minutes_ago=60),
        ]),
        "/set_state": _MockResponse(201, json_data={"state": {"type": "CRASHED"}}),
    })
    summary = await psfp.run_prefect_stuck_flow_probe(
        pool, notify_fn=notify, http_client_factory=lambda: client,
    )
    assert summary["stuck_count"] == 1
    assert summary["auto_crashed_count"] == 1

    audit_event_types = [
        args[0] for query, args in pool._audit_rows
        if "audit_log" in query
    ]
    assert "probe.prefect_stuck_flow_detected" in audit_event_types
    assert "probe.prefect_stuck_flow_auto_crashed" in audit_event_types

    # set_state body still carries force=true regardless of source state.
    set_state_body = next(
        body for url, body in client.posts if url.endswith("/set_state")
    )
    assert set_state_body["force"] is True
    assert set_state_body["state"]["type"] == "CRASHED"
