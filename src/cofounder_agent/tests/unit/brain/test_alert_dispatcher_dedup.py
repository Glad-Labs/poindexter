"""Unit tests for #420 — alert coalescing + AI summary + severity routing.

Covers the contract from Glad-Labs/poindexter#420:

1. **Synthetic warning burst** — 20 identical warnings in the suppression
   window dispatch ONCE on Discord, ZERO Telegrams.
2. **Synthetic critical burst** — 20 identical criticals in the
   suppression window dispatch ONCE (Telegram + Discord), the rest
   suppressed.
3. **Threshold escalation** — clock-mocked: 5 fires, then time advances
   past the threshold, the next fire dispatches an LLM-summary message
   (mocked out at the worker /api/triage call).
4. **Force-Telegram override** — a warning whose alertname is in the
   ``alert_force_telegram_event_types`` CSV reaches Telegram even though
   severity is below the bar.
5. **Mixed severity, same fingerprint base** — a warning + a critical
   with the same alertname/message do NOT collide because severity is
   part of the fingerprint, so escalating severity re-pages.

All DB I/O is mocked. The pool exposes ``fetch`` / ``fetchrow`` /
``fetchval`` / ``execute`` as AsyncMocks; we maintain an in-memory
``alert_dedup_state`` view inside each test so the dispatcher's reads
and writes round-trip realistically.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

# brain/ is a standalone package outside the cofounder_agent distro.
# Mirror the path-prelude pattern from test_brain_alert_dispatcher.py.
_REPO_ROOT = Path(__file__).resolve().parents[5]
_BRAIN_DIR = _REPO_ROOT / "brain"
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
if str(_BRAIN_DIR) not in sys.path:
    sys.path.insert(0, str(_BRAIN_DIR))

from brain import alert_dispatcher as ad  # noqa: E402

# ---------------------------------------------------------------------------
# Fixtures + helpers
# ---------------------------------------------------------------------------


def _make_row(
    *,
    row_id: int,
    alertname: str = "OpenclawDown",
    severity: str = "warning",
    category: str = "infrastructure",
    summary: str = "Service openclaw is down -- no container mapping",
    description: str = "",
    status: str = "firing",
) -> dict:
    """Match the asyncpg row shape ``alert_events`` returns."""
    labels = {
        "alertname": alertname,
        "severity": severity,
        "category": category,
    }
    annotations = {"summary": summary}
    if description:
        annotations["description"] = description
    return {
        "id": row_id,
        "alertname": alertname,
        "status": status,
        "severity": severity,
        "category": category,
        "labels": json.dumps(labels),
        "annotations": json.dumps(annotations),
    }


class _StatePool:
    """Mock pool that maintains a tiny in-memory alert_dedup_state.

    The dispatcher's dedup logic reads + writes this state across many
    rows in a single test, so a stub that just returns a fixed value
    isn't enough -- we need the second fire of the same fingerprint to
    actually see the row inserted by the first fire.

    Also tracks ``app_settings`` reads (returns the per-test override
    dict if provided, otherwise None so the defaults kick in) and
    swallows any other SELECT (returns ``None`` from fetchval, ``[]``
    from fetch). All values mirror what asyncpg would return.

    ``rows_to_serve`` is the queue ``poll_and_dispatch`` consumes via
    ``fetch(_POLL_SQL, batch_size)``. Each call returns the next chunk
    if any, else an empty list (so a second poll inside the same test
    sees no work).
    """

    def __init__(
        self,
        *,
        rows_to_serve: list[list[dict]] | None = None,
        app_settings: dict[str, str] | None = None,
    ):
        self._rows_queue = list(rows_to_serve or [])
        self._app_settings = dict(app_settings or {})
        self.dedup_state: dict[str, dict] = {}
        self.executes: list[tuple] = []
        self.fetch = AsyncMock(side_effect=self._fetch)
        self.fetchrow = AsyncMock(side_effect=self._fetchrow)
        self.fetchval = AsyncMock(side_effect=self._fetchval)
        self.execute = AsyncMock(side_effect=self._execute)

    async def _fetch(self, sql: str, *args):
        if "FROM alert_events" in sql:
            if self._rows_queue:
                return self._rows_queue.pop(0)
            return []
        if "FROM audit_log" in sql:
            return []
        return []

    async def _fetchrow(self, sql: str, *args):
        if "FROM alert_dedup_state" in sql:
            fingerprint = args[0]
            return self.dedup_state.get(fingerprint)
        return None

    async def _fetchval(self, sql: str, *args):
        if "FROM app_settings" in sql:
            key = args[0]
            return self._app_settings.get(key)
        return None

    async def _execute(self, sql: str, *args):
        self.executes.append((sql, args))
        if "INSERT INTO alert_dedup_state" in sql:
            (fingerprint, now, severity, source, sample_message) = args
            # ON CONFLICT DO NOTHING semantics.
            if fingerprint not in self.dedup_state:
                self.dedup_state[fingerprint] = {
                    "fingerprint": fingerprint,
                    "first_seen_at": now,
                    "last_seen_at": now,
                    "repeat_count": 1,
                    "summary_dispatched_at": None,
                    "severity": severity,
                    "source": source,
                    "sample_message": sample_message,
                }
            return "INSERT 0 1"
        if "UPDATE alert_dedup_state" in sql:
            # Different UPDATE shapes:
            # - bump (set last_seen + count++) -- 2 args
            # - latch (set summary_dispatched_at) -- 2 args
            # - reset (whole row, 5 args)
            # We branch on the SQL substring AND on arg count so two-space /
            # one-space whitespace differences in the UPDATE statement don't
            # silently misroute.
            if "summary_dispatched_at" in sql and len(args) == 2:
                fp, now = args
                if fp in self.dedup_state:
                    self.dedup_state[fp]["summary_dispatched_at"] = now
                    self.dedup_state[fp]["last_seen_at"] = now
            elif len(args) == 5:
                # Full reset (window expired) -- shape (fp, now, severity,
                # source, sample_message).
                fp, now, severity, source, sample_message = args
                self.dedup_state[fp] = {
                    "fingerprint": fp,
                    "first_seen_at": now,
                    "last_seen_at": now,
                    "repeat_count": 1,
                    "summary_dispatched_at": None,
                    "severity": severity,
                    "source": source,
                    "sample_message": sample_message,
                }
            else:
                # repeat-count bump.
                fp, now = args
                if fp in self.dedup_state:
                    self.dedup_state[fp]["repeat_count"] += 1
                    self.dedup_state[fp]["last_seen_at"] = now
            return "UPDATE 1"
        return "OK"


def _alert_event_updates(executes: list[tuple]) -> list[tuple]:
    """Return only the UPDATE alert_events rows the dispatcher wrote."""
    return [(sql, args) for sql, args in executes if "UPDATE alert_events" in sql]


def _classify_routed_calls(notify_calls):
    """Split routed_notify dispatches by ``critical=`` kwarg.

    Each call's ``critical`` kwarg drives the severity routing matrix
    on the test-injected (production-bypass) path:
    ``critical=True`` -> Telegram + Discord, ``critical=False`` ->
    Discord only. Returns ``(telegram_count, discord_only_count)``.
    """
    telegram = 0
    discord_only = 0
    for c in notify_calls:
        if c.kwargs.get("critical") is True:
            telegram += 1
        else:
            discord_only += 1
    return telegram, discord_only


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
class TestCoalesceWarningBurst:
    """Synthetic 20-warning burst in 5 min -> only 1 Discord, 0 Telegram."""

    async def test_twenty_identical_warnings_dispatch_once_on_discord(self):
        rows_per_cycle = [[_make_row(row_id=i, severity="warning")]
                          for i in range(20)]
        pool = _StatePool(rows_to_serve=rows_per_cycle)
        notify = AsyncMock(return_value=None)

        for _ in range(20):
            await ad.poll_and_dispatch(pool, notify_fn=notify)

        # Exactly one dispatch, with critical=False (warning -> Discord
        # only on the test-injected path).
        assert notify.await_count == 1
        telegrams, discord_only = _classify_routed_calls(notify.await_args_list)
        assert telegrams == 0, "warnings must NOT page Telegram"
        assert discord_only == 1, "exactly one Discord dispatch expected"

        # 19 of the 20 rows were marked suppressed; one was marked sent.
        alert_updates = _alert_event_updates(pool.executes)
        assert len(alert_updates) == 20
        sent_count = sum(
            1 for sql, _args in alert_updates
            if "dispatch_result = 'sent'" in sql
        )
        suppressed_count = sum(
            1 for sql, args in alert_updates
            if "dispatch_result = $2" in sql
            and isinstance(args[1], str)
            and args[1].startswith("suppressed")
        )
        assert sent_count == 1
        assert suppressed_count == 19


@pytest.mark.unit
@pytest.mark.asyncio
class TestCoalesceCriticalBurst:
    """20 critical alerts -> only 1 Telegram + 1 Discord dispatch."""

    async def test_twenty_identical_criticals_dispatch_once_to_both(self):
        rows_per_cycle = [[_make_row(row_id=i, severity="critical")]
                          for i in range(20)]
        pool = _StatePool(rows_to_serve=rows_per_cycle)
        notify = AsyncMock(return_value=None)

        for _ in range(20):
            await ad.poll_and_dispatch(pool, notify_fn=notify)

        # Exactly one dispatch -- on the test-injected path that's a
        # single notify_fn call with critical=True (which on production
        # would be Telegram + Discord both).
        assert notify.await_count == 1
        telegrams, discord_only = _classify_routed_calls(notify.await_args_list)
        assert telegrams == 1, "critical must page Telegram"
        assert discord_only == 0

        # 19 marked suppressed, 1 marked sent.
        alert_updates = _alert_event_updates(pool.executes)
        sent = sum(1 for sql, _ in alert_updates
                   if "dispatch_result = 'sent'" in sql)
        suppressed = sum(
            1 for sql, args in alert_updates
            if "dispatch_result = $2" in sql
            and isinstance(args[1], str)
            and args[1].startswith("suppressed")
        )
        assert sent == 1
        assert suppressed == 19


@pytest.mark.unit
@pytest.mark.asyncio
class TestThresholdEscalation:
    """Threshold-escalation -- clock-mocked.

    5 fires at t=0, then time advances 31 min, the next fire triggers
    the AI-summary dispatch (the LLM call is mocked at the worker
    /api/triage POST level).
    """

    async def test_threshold_triggers_summary_with_llm_payload(
        self, monkeypatch,
    ):
        # Pool gets N cycles' worth of rows -- 5 inside the suppression
        # window keeping last_seen_at fresh, then a final fire after
        # 31 minutes total but still within the rolling 30-min suppress
        # window from the prior fire (so we don't reset the burst, we
        # escalate to summary).
        rows_per_cycle = [[_make_row(row_id=i, severity="warning")]
                          for i in range(7)]
        pool = _StatePool(
            rows_to_serve=rows_per_cycle,
            app_settings={
                # api_base_url is required for the summary path to even
                # try the worker. Use a literal URL since the urllib
                # call is patched out below.
                "api_base_url": "http://worker:8002",
            },
        )

        # ----- clock control ----------------------------------------------
        # Fires every 5 minutes for 31 minutes. Each fire is well inside
        # the 30-min suppression window from the prior one (so we never
        # reset the burst), and by the 7th fire we've crossed the
        # threshold-from-first-seen so the AI summary kicks in.
        base = datetime(2026, 5, 6, 17, 0, 0, tzinfo=timezone.utc)
        clock = [base + timedelta(minutes=5 * i) for i in range(7)]
        # Last fire pushes total burst duration past 30 minutes.
        clock[-1] = base + timedelta(minutes=31)

        clock_iter = iter(clock * 5)  # generous cushion for repeat reads

        def _fake_now():
            return next(clock_iter)

        monkeypatch.setattr(ad, "_default_now", _fake_now)

        # ----- LLM stub ----------------------------------------------------
        # Patch the synchronous urllib helper the brain uses to POST to
        # /api/triage. Returns a fake 200 with a diagnosis paragraph in
        # the firefighter response shape so _request_summary_diagnosis
        # picks it up.
        captured: dict[str, object] = {}

        def _fake_post(url, payload, token, timeout):
            captured["url"] = url
            captured["payload"] = json.loads(payload.decode("utf-8"))
            return 200, json.dumps({
                "diagnosis": "Likely cause: docker socket unreachable.",
                "model": "ops_triage",
                "tokens": 42,
                "ms": 5,
            }).encode("utf-8")

        monkeypatch.setattr(ad, "_post_triage_sync", _fake_post)
        # OAuth mint is irrelevant to the contract under test -- skip it.
        async def _fake_token(_pool, _base_url):
            return "test-jwt"
        monkeypatch.setattr(ad, "_mint_oauth_token", _fake_token)

        notify = AsyncMock(return_value=None)
        for _ in range(7):
            await ad.poll_and_dispatch(pool, notify_fn=notify)

        # First fire dispatched + 5 suppressed + 7th fire promoted to
        # AI summary = 2 notify calls.
        assert notify.await_count == 2

        # The second notify call (the summary) carries the burst
        # context as its first argument.
        summary_message = notify.await_args_list[1].args[0]
        assert "[SUMMARY" in summary_message
        assert "Likely cause: docker socket unreachable." in summary_message

        # The LLM payload must include the burst metadata so the prompt
        # can produce the requested context-rich paragraph.
        assert "summary_request" in captured["payload"]
        assert captured["payload"]["summary_request"] is True
        annotations = captured["payload"]["annotations"]
        assert annotations["repeat_count"] >= 5
        assert annotations["duration_minutes"] >= 30

        # And summary_dispatched_at was latched on the dedup-state row.
        # Find the only fingerprint in the state -- it's the warning's.
        assert len(pool.dedup_state) == 1
        only_state = next(iter(pool.dedup_state.values()))
        assert only_state["summary_dispatched_at"] is not None


@pytest.mark.unit
@pytest.mark.asyncio
class TestForceTelegramOverride:
    """Warning-severity alert with event_type in the override CSV ->
    forced Telegram."""

    async def test_force_telegram_routes_warning_to_both_channels(self):
        rows_per_cycle = [
            [_make_row(
                row_id=1,
                severity="warning",
                alertname="cost_guard_tripped",
                category="cost",
            )],
        ]
        pool = _StatePool(
            rows_to_serve=rows_per_cycle,
            app_settings={
                "alert_force_telegram_event_types":
                    "cost_guard_tripped,worker_crashed_unrecoverable",
            },
        )
        notify = AsyncMock(return_value=None)
        await ad.poll_and_dispatch(pool, notify_fn=notify)

        # The override flips the routing decision so the test-injected
        # notify_fn was called with critical=True even though the
        # severity is warning. On production that maps to Telegram +
        # Discord both.
        assert notify.await_count == 1
        assert notify.await_args.kwargs.get("critical") is True


@pytest.mark.unit
@pytest.mark.asyncio
class TestMixedSeverityNoCollision:
    """Warning + critical with same alertname/message produce DIFFERENT
    fingerprints because severity is part of the key.

    Escalating severity should re-page; collapsing them under the same
    fingerprint would silently swallow a real escalation.
    """

    async def test_warning_then_critical_same_message_both_dispatch(self):
        rows = [
            [_make_row(row_id=1, severity="warning",
                       alertname="ServiceX", summary="ServiceX is unhealthy")],
            [_make_row(row_id=2, severity="critical",
                       alertname="ServiceX", summary="ServiceX is unhealthy")],
        ]
        pool = _StatePool(rows_to_serve=rows)
        notify = AsyncMock(return_value=None)

        await ad.poll_and_dispatch(pool, notify_fn=notify)
        await ad.poll_and_dispatch(pool, notify_fn=notify)

        # BOTH fires dispatched -- two distinct fingerprints in the
        # dedup-state, two distinct notify calls.
        assert notify.await_count == 2
        assert len(pool.dedup_state) == 2

        telegrams, discord_only = _classify_routed_calls(notify.await_args_list)
        # warning -> discord only (1), critical -> both (1, counted as
        # telegram in the classifier).
        assert telegrams == 1
        assert discord_only == 1


@pytest.mark.unit
class TestNormalizeMessage:
    """The fingerprint stays stable when only numbers / timestamps differ."""

    def test_strips_iso_timestamp(self):
        a = "Service down at 2026-05-06T22:15:00Z"
        b = "Service down at 2026-05-06T22:20:00Z"
        assert ad._normalize_message(a) == ad._normalize_message(b)

    def test_strips_clock_time(self):
        a = "alert raised 17:23"
        b = "alert raised 19:55"
        assert ad._normalize_message(a) == ad._normalize_message(b)

    def test_strips_run_of_numbers(self):
        a = "GPU temp 89.5C threshold 85"
        b = "GPU temp 91.2C threshold 85"
        assert ad._normalize_message(a) == ad._normalize_message(b)

    def test_distinct_text_stays_distinct(self):
        # The structural identity of the alert (alertname, prose) must
        # survive normalization -- only the per-fire numeric noise
        # collapses.
        a = ad._normalize_message("Service postgres down")
        b = ad._normalize_message("Service openclaw down")
        assert a != b


@pytest.mark.unit
class TestChannelsFor:
    """Severity matrix -> (telegram, discord) booleans."""

    def test_critical_to_both(self):
        tg, dc = ad._channels_for(
            "critical", alertname="X", category="y", force_telegram_set=frozenset(),
        )
        assert tg is True
        assert dc is True

    def test_error_to_both(self):
        tg, dc = ad._channels_for(
            "error", alertname="X", category="y", force_telegram_set=frozenset(),
        )
        assert tg is True
        assert dc is True

    def test_warning_discord_only(self):
        tg, dc = ad._channels_for(
            "warning", alertname="X", category="y", force_telegram_set=frozenset(),
        )
        assert tg is False
        assert dc is True

    def test_info_discord_only(self):
        tg, dc = ad._channels_for(
            "info", alertname="X", category="y", force_telegram_set=frozenset(),
        )
        assert tg is False
        assert dc is True

    def test_unknown_severity_discord_only(self):
        tg, dc = ad._channels_for(
            "moderate", alertname="X", category="y", force_telegram_set=frozenset(),
        )
        assert tg is False
        assert dc is True

    def test_force_telegram_alertname_match(self):
        tg, dc = ad._channels_for(
            "warning",
            alertname="cost_guard_tripped",
            category="cost",
            force_telegram_set=frozenset({"cost_guard_tripped"}),
        )
        assert tg is True
        assert dc is True

    def test_force_telegram_category_match(self):
        tg, dc = ad._channels_for(
            "info",
            alertname="something",
            category="business_critical",
            force_telegram_set=frozenset({"business_critical"}),
        )
        assert tg is True


@pytest.mark.unit
@pytest.mark.asyncio
class TestDegradedSummary:
    """When the worker /api/triage call fails, the summary still fires
    with a deterministic degraded body (no silent fallback)."""

    async def test_llm_failure_falls_back_to_degraded_summary(
        self, monkeypatch,
    ):
        # Fires every 10 minutes for 50 minutes -- last_seen_at stays
        # fresh on each fire so the burst is never reset, and by fire
        # #6 we're past the 30-min summarize threshold from first_seen.
        rows_per_cycle = [[_make_row(row_id=i, severity="warning")]
                          for i in range(6)]
        pool = _StatePool(
            rows_to_serve=rows_per_cycle,
            app_settings={"api_base_url": "http://worker:8002"},
        )

        base = datetime(2026, 5, 6, 17, 0, 0, tzinfo=timezone.utc)
        clock = [base + timedelta(minutes=10 * i) for i in range(6)]
        # Push the last one to 50 min total so duration_minutes > 30.
        clock[-1] = base + timedelta(minutes=50)
        clock_iter = iter(clock * 5)

        def _fake_now():
            return next(clock_iter)

        monkeypatch.setattr(ad, "_default_now", _fake_now)

        # OAuth mint succeeds, but the worker is down (network error
        # on every retry).
        async def _fake_token(_pool, _base_url):
            return "test-jwt"
        monkeypatch.setattr(ad, "_mint_oauth_token", _fake_token)

        def _broken_post(*_a, **_kw):
            raise ConnectionError("worker unreachable")
        monkeypatch.setattr(ad, "_post_triage_sync", _broken_post)

        # Speed up the retry sleeps so the test isn't bound by backoff.
        async def _fast_sleep(_seconds):
            return None
        monkeypatch.setattr(ad.asyncio, "sleep", _fast_sleep)

        notify = AsyncMock(return_value=None)
        for _ in range(6):
            await ad.poll_and_dispatch(pool, notify_fn=notify)

        # 2 dispatches: the first fire + the threshold-escalated summary.
        assert notify.await_count == 2
        summary_text = notify.await_args_list[1].args[0]
        # Degraded message body matches the spec verbatim.
        assert "LLM summary unavailable" in summary_text
        # Repeat count + duration must still be present so the operator
        # can tell something is up.
        assert "fired" in summary_text or "times" in summary_text
