"""Unit tests for brain/gate_auto_expire_probe.py (Glad-Labs/poindexter#338).

Covers the auto-reject sweep: ``state='pending'`` gates older than the
configured threshold are transitioned to ``state='rejected'`` with a
sentinel reason, a ``pipeline_gate_history`` row is written for each
expiry, a single batch-summary ``audit_log`` row records the cycle, and
ONE coalesced Telegram notification fires when the batch meets the
configurable threshold (per Matt's
``feedback_telegram_vs_discord.md`` rule: never per-gate).

All external I/O (the asyncpg pool, ``notify_operator``,
``datetime.now``) is mocked. The pool is a ``MagicMock`` whose async
methods are ``AsyncMock``s; we seed app_settings reads via the
``setting_values`` dict passed to ``_make_pool``.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from unittest.mock import AsyncMock, MagicMock

import pytest

# pythonpath in pyproject.toml includes "../.." so the brain package
# resolves the same way the backup_watcher tests import it.
from brain import gate_auto_expire_probe as ge


# ---------------------------------------------------------------------------
# Helpers — pool builder + fixed clock
# ---------------------------------------------------------------------------


_FIXED_NOW = datetime(2026, 5, 6, 12, 0, 0, tzinfo=timezone.utc)


def _now_fn():
    return _FIXED_NOW


def _default_settings() -> dict[str, str]:
    """Match the migration's seed values."""
    return {
        ge.ENABLED_KEY: "true",
        ge.MAX_AGE_HOURS_KEY: "168",
        ge.POLL_INTERVAL_MINUTES_KEY: "30",
        ge.BATCH_SIZE_KEY: "50",
        ge.NOTIFY_THRESHOLD_KEY: "1",
    }


def _make_stale_gate(
    *,
    gate_id: str,
    post_id: str = "11111111-1111-1111-1111-111111111111",
    gate_name: str = "draft",
    age_hours: float = 200.0,
    post_title: str = "Some stale post",
) -> dict[str, Any]:
    """Build a row dict the way ``pool.fetch`` would return it."""
    return {
        "id": gate_id,
        "post_id": post_id,
        "gate_name": gate_name,
        "created_at": _FIXED_NOW - timedelta(hours=age_hours),
        "post_title": post_title,
    }


def _make_pool(
    *,
    setting_values: Optional[dict[str, str]] = None,
    stale_rows: Optional[list[dict[str, Any]]] = None,
    update_returns: Optional[dict[str, bool]] = None,
    select_raises: Optional[Exception] = None,
):
    """Build an asyncpg-style mock pool that:

    - returns ``setting_values[key]`` for ``SELECT value FROM app_settings``
      lookups via ``fetchval``,
    - returns ``stale_rows`` for the ``post_approval_gates`` SELECT via
      ``fetch``,
    - returns ``{"id": gate_id}`` (success) or ``None`` (race-loss) for
      the per-gate ``UPDATE ... RETURNING id`` based on the
      ``update_returns`` map (gate_id → bool, defaults to True for any
      id not in the map),
    - records every ``execute`` call so tests can assert on history /
      audit writes.
    """
    pool = MagicMock()
    settings = {**_default_settings(), **(setting_values or {})}
    rows = list(stale_rows or [])
    update_map = update_returns or {}

    async def _fetchval(query, *args):
        if "app_settings" in query and args:
            return settings.get(args[0])
        return None

    async def _fetch(query, *args):
        if "post_approval_gates" in query:
            if select_raises is not None:
                raise select_raises
            return list(rows)
        return []

    async def _fetchrow(query, *args):
        # Only the per-gate UPDATE issues fetchrow.
        if "UPDATE post_approval_gates" in query and args:
            # gate_id is the 4th positional arg in the helper.
            gate_id = args[3]
            ok = update_map.get(gate_id, True)
            return {"id": gate_id} if ok else None
        return None

    pool.fetchval = AsyncMock(side_effect=_fetchval)
    pool.fetch = AsyncMock(side_effect=_fetch)
    pool.fetchrow = AsyncMock(side_effect=_fetchrow)
    pool.execute = AsyncMock()
    return pool


def _executed_audit_event_types(pool) -> list[str]:
    out: list[str] = []
    for call in pool.execute.call_args_list:
        sql = call.args[0]
        if "INSERT INTO audit_log" in sql:
            # event_type is positional arg 1 (after sql).
            out.append(call.args[1])
    return out


def _executed_history_post_ids(pool) -> list[str]:
    out: list[str] = []
    for call in pool.execute.call_args_list:
        sql = call.args[0]
        if "INSERT INTO pipeline_gate_history" in sql:
            # The helper passes (post_id, gate_name, sentinel_reason, metadata).
            # post_id is positional arg 1 (after sql).
            out.append(call.args[1])
    return out


# ---------------------------------------------------------------------------
# Test 1 — nothing stale → no-op
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestNothingStale:
    @pytest.mark.asyncio
    async def test_no_stale_gates_returns_noop_no_notify(self):
        pool = _make_pool(stale_rows=[])

        notify_calls: list[dict] = []

        def fake_notify(**kwargs):
            notify_calls.append(kwargs)

        summary = await ge.run_gate_auto_expire_probe(
            pool, now_fn=_now_fn, notify_fn=fake_notify,
        )

        assert summary["ok"] is True
        assert summary["status"] == "noop"
        assert summary["expired"] == 0
        # No notification, no history rows, no audit row when nothing
        # stale (the audit_log row is only written when expired > 0
        # OR is conditionally cheap — our impl writes it only when
        # expired > 0; verify both no-history and no-audit here).
        assert notify_calls == []
        assert _executed_history_post_ids(pool) == []
        assert _executed_audit_event_types(pool) == []


# ---------------------------------------------------------------------------
# Test 2 — one stale gate → expires + history row + audit row + (no notify
# below threshold of 2)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSingleStaleGate:
    @pytest.mark.asyncio
    async def test_one_stale_expires_writes_history_audit_no_notify_below_threshold(self):
        gate = _make_stale_gate(
            gate_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaa1",
            age_hours=200.0,
        )
        pool = _make_pool(
            setting_values={ge.NOTIFY_THRESHOLD_KEY: "2"},  # threshold 2 → 1 stale = no notify
            stale_rows=[gate],
        )

        notify_calls: list[dict] = []

        def fake_notify(**kwargs):
            notify_calls.append(kwargs)

        summary = await ge.run_gate_auto_expire_probe(
            pool, now_fn=_now_fn, notify_fn=fake_notify,
        )

        assert summary["ok"] is True
        assert summary["expired"] == 1
        assert summary["status"] == "expired"
        assert summary["sentinel_reason"] == "auto_rejected_after_168_hours"

        # The pipeline_gate_history row was written for this gate.
        history_post_ids = _executed_history_post_ids(pool)
        assert history_post_ids == [gate["post_id"]]

        # The audit_log row was written for the cycle.
        audit_events = _executed_audit_event_types(pool)
        assert audit_events == ["gate_auto_expired"]

        # Threshold is 2; we expired 1 — no notify.
        assert notify_calls == [], notify_calls
        assert summary["notified"] is False


# ---------------------------------------------------------------------------
# Test 3 — batch of N (threshold 1) → ONE coalesced Telegram call
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestBatchCoalescedNotify:
    @pytest.mark.asyncio
    async def test_five_stale_one_coalesced_notify(self):
        # Build 5 stale gates with different ages so we can verify
        # the "oldest was for post X" logic in the message.
        gates = [
            _make_stale_gate(
                gate_id=f"aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaa{i}",
                post_id=f"22222222-2222-2222-2222-22222222222{i}",
                age_hours=200.0 + i * 24,  # ages 200, 224, 248, 272, 296h
                post_title=f"Stale post {i}",
            )
            for i in range(5)
        ]
        # Oldest is gates[4] (296h ≈ 12.3 days).
        pool = _make_pool(
            setting_values={ge.NOTIFY_THRESHOLD_KEY: "1"},
            stale_rows=gates,
        )

        notify_calls: list[dict] = []

        def fake_notify(**kwargs):
            notify_calls.append(kwargs)

        summary = await ge.run_gate_auto_expire_probe(
            pool, now_fn=_now_fn, notify_fn=fake_notify,
        )

        assert summary["expired"] == 5
        assert summary["notified"] is True

        # Exactly ONE notification — coalesced, not per-gate.
        assert len(notify_calls) == 1, notify_calls
        call = notify_calls[0]
        assert "5" in call["title"], call
        # Message body mentions count + threshold + oldest.
        detail = call["detail"]
        assert "5" in detail
        assert "168" in detail  # max_age_hours from default settings
        assert "Stale post 4" in detail  # oldest title
        # ~12.3 days → "12.3 days" or similar; just check "days" appears
        assert "days old" in detail
        # Source + severity match the brain conventions.
        assert call["source"] == "brain.gate_auto_expire"
        assert call["severity"] == "warning"

        # Five history rows + one audit row.
        assert len(_executed_history_post_ids(pool)) == 5
        assert _executed_audit_event_types(pool) == ["gate_auto_expired"]


# ---------------------------------------------------------------------------
# Test 4 — batch size cap honored
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestBatchSizeCap:
    @pytest.mark.asyncio
    async def test_batch_size_cap_passed_to_select(self):
        """The probe is responsible for capping the per-cycle batch via
        the ``LIMIT`` clause in the SELECT (rather than slicing in
        Python). Assert the cap value reaches the pool.

        We can't directly count "100 stale → 50 returned" because the
        mock returns whatever ``stale_rows`` we hand it; what we CAN
        verify is that the LIMIT positional arg passed to ``pool.fetch``
        equals the configured ``batch_size``. The 50/50 split (50
        expired this cycle, 50 remain) is then a property of the SQL
        the live DB executes — covered by the migration smoke test.
        """
        pool = _make_pool(
            setting_values={ge.BATCH_SIZE_KEY: "50"},
            stale_rows=[],
        )

        await ge.run_gate_auto_expire_probe(
            pool, now_fn=_now_fn, notify_fn=lambda **k: None,
        )

        # The fetch call's third positional arg is the LIMIT.
        fetch_calls = [
            c for c in pool.fetch.call_args_list
            if "post_approval_gates" in c.args[0]
        ]
        assert len(fetch_calls) == 1, fetch_calls
        # args = (sql, now_utc, max_age_hours_str, batch_size)
        assert fetch_calls[0].args[3] == 50

    @pytest.mark.asyncio
    async def test_50_stale_with_cap_50_all_expired_this_cycle(self):
        """When the live DB has 100 stale rows but our LIMIT is 50, the
        SELECT only sees 50. We model that here by handing the mock 50
        rows + a cap of 50; the probe should expire all 50 in one cycle.
        """
        gates = [
            _make_stale_gate(
                gate_id=f"aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaa{i:03d}",
                post_id=f"33333333-3333-3333-3333-3333333333{i:02d}",
                age_hours=200.0 + i,
            )
            for i in range(50)
        ]
        pool = _make_pool(
            setting_values={ge.BATCH_SIZE_KEY: "50"},
            stale_rows=gates,
        )

        summary = await ge.run_gate_auto_expire_probe(
            pool, now_fn=_now_fn, notify_fn=lambda **k: None,
        )
        assert summary["expired"] == 50


# ---------------------------------------------------------------------------
# Test 5 — master switch off → no SELECT, no notify
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestMasterSwitchOff:
    @pytest.mark.asyncio
    async def test_disabled_short_circuits_without_select(self):
        pool = _make_pool(
            setting_values={ge.ENABLED_KEY: "false"},
            stale_rows=[
                _make_stale_gate(
                    gate_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaa1",
                ),
            ],
        )

        notify_calls: list[dict] = []

        def fake_notify(**kwargs):
            notify_calls.append(kwargs)

        summary = await ge.run_gate_auto_expire_probe(
            pool, now_fn=_now_fn, notify_fn=fake_notify,
        )

        assert summary["status"] == "disabled"
        assert summary["expired"] == 0
        # No SELECT against post_approval_gates was issued.
        gate_fetches = [
            c for c in pool.fetch.call_args_list
            if "post_approval_gates" in c.args[0]
        ]
        assert gate_fetches == []
        # No UPDATE either.
        gate_updates = [
            c for c in pool.fetchrow.call_args_list
            if "UPDATE post_approval_gates" in c.args[0]
        ]
        assert gate_updates == []
        assert notify_calls == []


# ---------------------------------------------------------------------------
# Test 6 — concurrent state mutation race: pending at SELECT time but
# already approved/rejected by UPDATE time → UPDATE returns 0 rows for
# that gate → no history row written for it.
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestConcurrentRace:
    @pytest.mark.asyncio
    async def test_race_loss_skips_history(self):
        """Two stale gates; the operator approves gate #1 between our
        SELECT and our UPDATE. The UPDATE for gate #1 returns NULL
        (because of the ``WHERE state='pending'`` race guard). We must:

        - NOT write a pipeline_gate_history row for gate #1.
        - DO write one for gate #2 (which was still pending).
        - Surface ``skipped_races=1`` in the summary.
        """
        gate1 = _make_stale_gate(
            gate_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaa1",
            post_id="44444444-4444-4444-4444-444444444441",
            age_hours=200.0,
        )
        gate2 = _make_stale_gate(
            gate_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaa2",
            post_id="44444444-4444-4444-4444-444444444442",
            age_hours=210.0,
        )
        # gate1 race-loses, gate2 wins.
        pool = _make_pool(
            stale_rows=[gate1, gate2],
            update_returns={
                gate1["id"]: False,
                gate2["id"]: True,
            },
        )

        notify_calls: list[dict] = []

        def fake_notify(**kwargs):
            notify_calls.append(kwargs)

        summary = await ge.run_gate_auto_expire_probe(
            pool, now_fn=_now_fn, notify_fn=fake_notify,
        )

        assert summary["expired"] == 1
        assert summary["skipped_races"] == 1

        history_post_ids = _executed_history_post_ids(pool)
        # Only gate2's post_id should appear.
        assert history_post_ids == [gate2["post_id"]]

        # ONE notify (default threshold 1; we expired 1 gate).
        assert len(notify_calls) == 1


# ---------------------------------------------------------------------------
# Probe wrapper coverage
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestProbeWrapper:
    @pytest.mark.asyncio
    async def test_probe_protocol_wrapper_returns_proberesult(self):
        pool = _make_pool(stale_rows=[])

        # Patch the probe entry point so the wrapper call is fast and
        # deterministic. We're only testing the adapter contract here.
        async def fake_probe(_pool, **_kwargs):
            return {
                "ok": True,
                "status": "noop",
                "expired": 0,
                "max_age_hours": 168,
                "detail": "fake",
                "notified": False,
            }

        import brain.gate_auto_expire_probe as _ge_mod
        original = _ge_mod.run_gate_auto_expire_probe
        _ge_mod.run_gate_auto_expire_probe = fake_probe  # type: ignore[assignment]
        try:
            probe = ge.GateAutoExpireProbe()
            result = await probe.check(pool, {})
        finally:
            _ge_mod.run_gate_auto_expire_probe = original  # type: ignore[assignment]

        assert result.ok is True
        assert result.detail == "fake"
        assert result.metrics["status"] == "noop"
        assert result.metrics["expired"] == 0
        assert result.metrics["max_age_hours"] == 168


# ---------------------------------------------------------------------------
# SELECT failure path — defensive coverage
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSelectFailure:
    @pytest.mark.asyncio
    async def test_select_failure_surfaces_select_failed_status(self):
        pool = _make_pool(select_raises=RuntimeError("synthetic db kaboom"))

        notify_calls: list[dict] = []

        def fake_notify(**kwargs):
            notify_calls.append(kwargs)

        summary = await ge.run_gate_auto_expire_probe(
            pool, now_fn=_now_fn, notify_fn=fake_notify,
        )
        assert summary["ok"] is False
        assert summary["status"] == "select_failed"
        assert summary["expired"] == 0
        assert notify_calls == []
