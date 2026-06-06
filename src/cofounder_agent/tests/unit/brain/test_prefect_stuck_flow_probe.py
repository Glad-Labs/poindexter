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

    Both the stuck-run query and the queue-backlog query POST to the same
    ``/flow_runs/filter`` path; they differ only in the requested state
    type in the body. To route them independently, register the SCHEDULED
    response under the special key ``"/flow_runs/filter?SCHEDULED"`` — the
    mock inspects the posted body's ``state.type.any_`` and prefers the
    ``?SCHEDULED`` entry when the request is the scheduled-runs filter. A
    plain ``/flow_runs/filter`` entry still serves the RUNNING/PENDING
    query (and is the SCHEDULED fallback when no ``?SCHEDULED`` entry
    exists — defaults to an empty list of runs).
    """

    def __init__(self, responses: dict[str, _MockResponse]):
        self._responses = responses
        self.posts: list[tuple[str, dict[str, Any]]] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url: str, *, json=None, **_kwargs):  # noqa: A002
        body = json or {}
        self.posts.append((url, body))
        if url.endswith("/flow_runs/filter"):
            state_types = (
                (body.get("flow_runs") or {})
                .get("state", {})
                .get("type", {})
                .get("any_", [])
            )
            is_scheduled = state_types == ["SCHEDULED"]
            if is_scheduled:
                scheduled = self._responses.get("/flow_runs/filter?SCHEDULED")
                if scheduled is not None:
                    return scheduled
                # No scheduled-specific mock → empty queue (don't reuse the
                # RUNNING/PENDING canned runs, which would be wrong).
                return _MockResponse(200, json_data=[])
            running = self._responses.get("/flow_runs/filter")
            if running is not None:
                return running
            return _MockResponse(200, json_data=[])
        for path_suffix, resp in self._responses.items():
            if path_suffix.startswith("/flow_runs/filter"):
                continue
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


def _scheduled_run(
    *, run_id: str, name: str, minutes: int, use_state_details: bool = False,
) -> dict[str, Any]:
    """Build a fake SCHEDULED flow_run dict.

    Positive ``minutes`` → scheduled start is in the PAST (overdue).
    Negative ``minutes`` → scheduled start is in the FUTURE (not overdue).
    By default the scheduled time is on ``next_scheduled_start_time`` (the
    field the probe checks first); set ``use_state_details=True`` to put it
    under ``state.state_details.scheduled_time`` to exercise the fallback.
    """
    ts = _iso_minutes_ago(minutes)
    run: dict[str, Any] = {
        "id": run_id,
        "name": name,
        "state": {"type": "SCHEDULED", "name": "Scheduled"},
    }
    if use_state_details:
        run["state"]["state_details"] = {"scheduled_time": ts}
    else:
        run["next_scheduled_start_time"] = ts
    return run


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
async def test_stuck_flow_auto_crashes_by_default_no_setting():
    """Glad-Labs/poindexter#526: auto_crash now DEFAULTS ON. With NO
    ``prefect_stuck_flow_auto_crash`` app_setting present at all, a 35h-old
    RUNNING flow (the captured romantic-harrier regression) is both paged
    AND force-CRASHED — hands-off recovery is the default after the
    threshold was tuned across two incidents."""
    pool = _make_pool()  # NB: no setting_values — exercises the default
    notify = MagicMock()
    client = _MockHttpClient({
        "/flow_runs/filter": _MockResponse(200, json_data=[
            _run(run_id="019e5cb0", name="romantic-harrier", minutes_ago=2100),
        ]),
        "/set_state": _MockResponse(201, json_data={"state": {"type": "CRASHED"}}),
    })
    summary = await psfp.run_prefect_stuck_flow_probe(
        pool, notify_fn=notify, http_client_factory=lambda: client,
    )
    assert summary["stuck_count"] == 1
    assert summary["auto_crashed_count"] == 1
    assert summary["crash_failed_count"] == 0

    notify.assert_called_once()
    kwargs = notify.call_args.kwargs
    assert "romantic-harrier" in kwargs["title"]
    assert kwargs["severity"] == "warning"
    assert "set_state" in kwargs["detail"]  # manual-unstick hint

    # Both detection AND crash audited because auto_crash defaults true.
    audit_event_types = [
        args[0] for query, args in pool._audit_rows
        if "audit_log" in query
    ]
    assert "probe.prefect_stuck_flow_detected" in audit_event_types
    assert "probe.prefect_stuck_flow_auto_crashed" in audit_event_types

    # filter (RUNNING/PENDING) + filter (SCHEDULED queue check) + set_state.
    set_state_body = next(
        body for url, body in client.posts if url.endswith("/set_state")
    )
    assert set_state_body["force"] is True
    assert set_state_body["state"]["type"] == "CRASHED"


@pytest.mark.asyncio
async def test_stuck_flow_pages_only_when_auto_crash_disabled():
    """An operator who deliberately sets auto_crash=false keeps page-only
    behaviour — the probe pages + audits but never touches the flow run
    state."""
    pool = _make_pool(setting_values={"prefect_stuck_flow_auto_crash": "false"})
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

    audit_event_types = [
        args[0] for query, args in pool._audit_rows
        if "audit_log" in query
    ]
    assert "probe.prefect_stuck_flow_detected" in audit_event_types
    assert "probe.prefect_stuck_flow_auto_crashed" not in audit_event_types

    # No /set_state POST when auto_crash=false (only the two filter calls).
    assert not any(url.endswith("/set_state") for url, _ in client.posts)


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
    # Two filter POSTs: the stuck-run query (RUNNING/PENDING) + the
    # queue-backlog query (SCHEDULED). Both must carry the flow names.
    filter_bodies = [
        body for url, body in client.posts if url.endswith("/flow_runs/filter")
    ]
    assert len(filter_bodies) == 2
    stuck_body = next(
        b for b in filter_bodies
        if b["flow_runs"]["state"]["type"]["any_"] == ["RUNNING", "PENDING"]
    )
    sched_body = next(
        b for b in filter_bodies
        if b["flow_runs"]["state"]["type"]["any_"] == ["SCHEDULED"]
    )
    assert stuck_body["flows"]["name"]["any_"] == ["content_generation", "custom_flow"]
    assert sched_body["flows"]["name"]["any_"] == ["content_generation", "custom_flow"]


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


# ---------------------------------------------------------------------------
# Queue-backlog detection — Glad-Labs/poindexter#526
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_queue_backlog_pages_only_when_overdue_exceeds_threshold():
    """The #526 backlog symptom: scheduled runs piled up behind a held
    slot. With the default threshold of 3, a queue of 5 OVERDUE + 2 FUTURE
    scheduled runs (5 overdue > 3) fires the DISTINCT backlog page + audit
    event. Only the overdue runs count — future-scheduled runs are normal
    cron lookahead, not a backlog."""
    pool = _make_pool()
    notify = MagicMock()
    client = _MockHttpClient({
        # No RUNNING/PENDING runs — the held slot's run may have already
        # been crashed; the backlog is the standalone signal here.
        "/flow_runs/filter": _MockResponse(200, json_data=[]),
        "/flow_runs/filter?SCHEDULED": _MockResponse(200, json_data=[
            _scheduled_run(run_id="s1", name="content_generation", minutes=10),
            _scheduled_run(run_id="s2", name="content_generation", minutes=8),
            _scheduled_run(run_id="s3", name="content_generation", minutes=6),
            _scheduled_run(run_id="s4", name="content_generation", minutes=4),
            _scheduled_run(run_id="s5", name="content_generation", minutes=2),
            # Future-scheduled (negative = in the future) → NOT overdue.
            _scheduled_run(run_id="s6", name="content_generation", minutes=-5),
            _scheduled_run(run_id="s7", name="content_generation", minutes=-10),
        ]),
    })
    summary = await psfp.run_prefect_stuck_flow_probe(
        pool, notify_fn=notify, http_client_factory=lambda: client,
    )
    assert summary["overdue_scheduled_count"] == 5
    assert summary["queue_depth_threshold"] == 3
    assert summary["queue_backlog_detected"] is True
    assert summary["stuck_count"] == 0  # backlog is independent of stuck runs

    # A DISTINCT page fired (not the stuck-flow title).
    notify.assert_called_once()
    kwargs = notify.call_args.kwargs
    assert "backlog" in kwargs["title"].lower()
    assert kwargs["severity"] == "warning"
    assert "5" in kwargs["detail"]  # overdue count surfaced

    # The DISTINCT audit event — NOT the stuck-flow one.
    audit_event_types = [
        args[0] for query, args in pool._audit_rows
        if "audit_log" in query
    ]
    assert "probe.prefect_queue_backlog_detected" in audit_event_types
    assert "probe.prefect_stuck_flow_detected" not in audit_event_types


@pytest.mark.asyncio
async def test_queue_backlog_silent_at_or_below_threshold():
    """Exactly threshold-many overdue runs does NOT page (strict >). Three
    overdue runs at the default threshold of 3 is within tolerance — a
    couple of missed cron ticks is noise, not a pile-up."""
    pool = _make_pool()
    notify = MagicMock()
    client = _MockHttpClient({
        "/flow_runs/filter": _MockResponse(200, json_data=[]),
        "/flow_runs/filter?SCHEDULED": _MockResponse(200, json_data=[
            _scheduled_run(run_id="s1", name="content_generation", minutes=10),
            _scheduled_run(run_id="s2", name="content_generation", minutes=8),
            _scheduled_run(run_id="s3", name="content_generation", minutes=6),
        ]),
    })
    summary = await psfp.run_prefect_stuck_flow_probe(
        pool, notify_fn=notify, http_client_factory=lambda: client,
    )
    assert summary["overdue_scheduled_count"] == 3
    assert summary["queue_backlog_detected"] is False
    notify.assert_not_called()
    audit_event_types = [
        args[0] for query, args in pool._audit_rows
        if "audit_log" in query
    ]
    assert "probe.prefect_queue_backlog_detected" not in audit_event_types


@pytest.mark.asyncio
async def test_queue_backlog_uses_state_details_fallback():
    """When ``next_scheduled_start_time`` is absent the probe reads the
    nested ``state.state_details.scheduled_time``. Four overdue via the
    fallback field still trips the default threshold of 3."""
    pool = _make_pool()
    notify = MagicMock()
    client = _MockHttpClient({
        "/flow_runs/filter": _MockResponse(200, json_data=[]),
        "/flow_runs/filter?SCHEDULED": _MockResponse(200, json_data=[
            _scheduled_run(run_id="s1", name="content_generation", minutes=10, use_state_details=True),
            _scheduled_run(run_id="s2", name="content_generation", minutes=8, use_state_details=True),
            _scheduled_run(run_id="s3", name="content_generation", minutes=6, use_state_details=True),
            _scheduled_run(run_id="s4", name="content_generation", minutes=4, use_state_details=True),
        ]),
    })
    summary = await psfp.run_prefect_stuck_flow_probe(
        pool, notify_fn=notify, http_client_factory=lambda: client,
    )
    assert summary["overdue_scheduled_count"] == 4
    assert summary["queue_backlog_detected"] is True
    notify.assert_called_once()


@pytest.mark.asyncio
async def test_queue_backlog_threshold_setting_respected():
    """Operator can tune ``prefect_stuck_flow_queue_depth_threshold``.
    Lower it to 1 → two overdue runs trips the backlog page."""
    pool = _make_pool(setting_values={
        "prefect_stuck_flow_queue_depth_threshold": "1",
    })
    notify = MagicMock()
    client = _MockHttpClient({
        "/flow_runs/filter": _MockResponse(200, json_data=[]),
        "/flow_runs/filter?SCHEDULED": _MockResponse(200, json_data=[
            _scheduled_run(run_id="s1", name="content_generation", minutes=10),
            _scheduled_run(run_id="s2", name="content_generation", minutes=8),
        ]),
    })
    summary = await psfp.run_prefect_stuck_flow_probe(
        pool, notify_fn=notify, http_client_factory=lambda: client,
    )
    assert summary["overdue_scheduled_count"] == 2
    assert summary["queue_depth_threshold"] == 1
    assert summary["queue_backlog_detected"] is True
    notify.assert_called_once()


@pytest.mark.asyncio
async def test_queue_backlog_check_failure_does_not_abort_stuck_detection():
    """A queue-check failure (the SCHEDULED filter 500s) must NOT abort the
    stuck-run detection that already ran. A stuck run is still detected +
    auto-crashed even though the backlog query failed."""
    pool = _make_pool()
    notify = MagicMock()
    client = _MockHttpClient({
        "/flow_runs/filter": _MockResponse(200, json_data=[
            _run(run_id="abc", name="romantic-harrier", minutes_ago=2100),
        ]),
        "/flow_runs/filter?SCHEDULED": _MockResponse(500, text="prefect boom"),
        "/set_state": _MockResponse(201, json_data={"state": {"type": "CRASHED"}}),
    })
    summary = await psfp.run_prefect_stuck_flow_probe(
        pool, notify_fn=notify, http_client_factory=lambda: client,
    )
    # Stuck-run detection + auto-crash unaffected by the failed queue check.
    assert summary["ok"] is True
    assert summary["stuck_count"] == 1
    assert summary["auto_crashed_count"] == 1
    # Queue check degraded gracefully: 0 overdue, no backlog page.
    assert summary["overdue_scheduled_count"] == 0
    assert summary["queue_backlog_detected"] is False
    audit_event_types = [
        args[0] for query, args in pool._audit_rows
        if "audit_log" in query
    ]
    assert "probe.prefect_stuck_flow_detected" in audit_event_types
    assert "probe.prefect_queue_backlog_detected" not in audit_event_types
