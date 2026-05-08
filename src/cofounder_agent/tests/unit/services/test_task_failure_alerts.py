"""Regression tests for the task-failure dedup + severity routing helper.

Glad-Labs/poindexter#370 — a single fast-failing task produced 8+
Telegram alerts in 35 seconds. These tests pin the new behavior:

* a fast-failing task fires AT MOST ONE Telegram POST inside the
  configured dedup window;
* routine task failures route to Discord (``critical=False``) by
  default; only operators that explicitly opt into the Telegram
  severity get a phone-buzz;
* invalid severity values fail loud and fall back to Discord (the safe
  spam channel) per ``feedback_no_silent_defaults``;
* the auto-retry sweeper does NOT re-claim a failed task faster than
  the configured backoff, and stays disabled at ``max_attempts=0``.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from services import task_failure_alerts as tfa


def _make_get_setting(overrides: dict[str, str] | None = None):
    """Build a get_setting coroutine matching the executor's contract."""
    overrides = overrides or {}

    async def _gs(key: str, default: str = "") -> str:
        if key in overrides:
            return overrides[key]
        return default

    return _gs


# ---------------------------------------------------------------------------
# Acceptance Criterion 1 + 4: dedup + at-most-one Telegram POST per window
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_dedup_window_blocks_repeat_alerts_for_same_task_and_error():
    """Eight rapid identical failures = ONE outbound notify_operator call."""
    tfa._reset_lru_for_tests()

    overrides = {
        "task_failure_alert_dedup_window_seconds": "900",
        "task_failure_alert_severity": "telegram",  # worst case
    }
    with patch(
        "services.integrations.operator_notify.notify_operator",
        new_callable=AsyncMock,
    ) as mock_notify:
        results = []
        for _ in range(8):
            r = await tfa.send_failure_alert(
                task_id="task-storm-abc",
                topic="A failing topic",
                error_message="OllamaConnectionError: 502",
                pool=None,  # rely on LRU only — no DB dep in this test
                get_setting=_make_get_setting(overrides),
            )
            results.append(r)

    # Exactly one alert delivered, the rest deduped.
    assert mock_notify.await_count == 1, (
        f"Expected exactly 1 Telegram POST inside the dedup window, "
        f"got {mock_notify.await_count}"
    )
    assert results[0]["sent"] is True
    assert results[0]["deduped"] is False
    assert results[0]["channel"] == "telegram"
    for r in results[1:]:
        assert r["sent"] is False
        assert r["deduped"] is True


@pytest.mark.unit
@pytest.mark.asyncio
async def test_distinct_errors_for_same_task_are_not_deduped():
    """Different error_message → different hash → independent dedup key."""
    tfa._reset_lru_for_tests()

    overrides = {"task_failure_alert_dedup_window_seconds": "900"}
    with patch(
        "services.integrations.operator_notify.notify_operator",
        new_callable=AsyncMock,
    ) as mock_notify:
        await tfa.send_failure_alert(
            task_id="t1",
            topic="Topic",
            error_message="ErrorA",
            pool=None,
            get_setting=_make_get_setting(overrides),
        )
        await tfa.send_failure_alert(
            task_id="t1",
            topic="Topic",
            error_message="ErrorB",
            pool=None,
            get_setting=_make_get_setting(overrides),
        )
    assert mock_notify.await_count == 2


# ---------------------------------------------------------------------------
# Acceptance Criterion 2: severity routing
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_default_severity_routes_to_discord_not_telegram():
    """No explicit setting → default 'discord' → notify_operator(critical=False)."""
    tfa._reset_lru_for_tests()

    with patch(
        "services.integrations.operator_notify.notify_operator",
        new_callable=AsyncMock,
    ) as mock_notify:
        out = await tfa.send_failure_alert(
            task_id="t-routing-1",
            topic="Default-routed topic",
            error_message="boom",
            pool=None,
            get_setting=_make_get_setting({}),  # all defaults
        )
    assert out["sent"] is True
    assert out["channel"] == "discord"
    mock_notify.assert_awaited_once()
    # critical kwarg must be False — Discord, not Telegram.
    assert mock_notify.await_args.kwargs.get("critical") is False


@pytest.mark.unit
@pytest.mark.asyncio
async def test_explicit_telegram_severity_routes_critical_true():
    tfa._reset_lru_for_tests()

    with patch(
        "services.integrations.operator_notify.notify_operator",
        new_callable=AsyncMock,
    ) as mock_notify:
        out = await tfa.send_failure_alert(
            task_id="t-routing-2",
            topic="Topic",
            error_message="boom",
            pool=None,
            get_setting=_make_get_setting(
                {"task_failure_alert_severity": "telegram"}
            ),
        )
    assert out["sent"] is True
    assert out["channel"] == "telegram"
    assert mock_notify.await_args.kwargs.get("critical") is True


@pytest.mark.unit
@pytest.mark.asyncio
async def test_invalid_severity_fails_loud_and_falls_back_to_discord():
    """Typo'd severity must NOT silently spam Telegram."""
    tfa._reset_lru_for_tests()

    with patch(
        "services.integrations.operator_notify.notify_operator",
        new_callable=AsyncMock,
    ) as mock_notify:
        out = await tfa.send_failure_alert(
            task_id="t-routing-3",
            topic="Topic",
            error_message="boom",
            pool=None,
            get_setting=_make_get_setting(
                {"task_failure_alert_severity": "phone-it-in"}
            ),
        )
    # Channel falls back to discord (the safer choice) so an operator
    # typo can't accidentally page Matt's phone.
    assert out["channel"] == "discord"
    assert mock_notify.await_args.kwargs.get("critical") is False


# ---------------------------------------------------------------------------
# Acceptance Criterion 1: window=0 disables dedup entirely
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_window_zero_disables_dedup():
    tfa._reset_lru_for_tests()

    overrides = {"task_failure_alert_dedup_window_seconds": "0"}
    with patch(
        "services.integrations.operator_notify.notify_operator",
        new_callable=AsyncMock,
    ) as mock_notify:
        for _ in range(3):
            await tfa.send_failure_alert(
                task_id="t-no-dedup",
                topic="Topic",
                error_message="same error",
                pool=None,
                get_setting=_make_get_setting(overrides),
            )
    # Window=0 means every call goes through.
    assert mock_notify.await_count == 3


# ---------------------------------------------------------------------------
# Acceptance Criterion 3: backoff on re-claim — auto-retry stays off by default,
# and when enabled it respects the exponential window.
# ---------------------------------------------------------------------------


def _make_executor_for_retry_test(get_setting_overrides: dict[str, str]):
    """Build a TaskExecutor with the heavy collaborators stubbed so we can
    exercise ``_auto_retry_failed_tasks`` in isolation."""
    from unittest.mock import MagicMock

    db = AsyncMock()
    db.pool = MagicMock()
    db.pool.fetch = AsyncMock(return_value=[])
    db.update_task = AsyncMock(return_value=True)

    with (
        patch("services.task_executor.UnifiedQualityService"),
        patch("services.task_executor.AIContentGenerator"),
    ):
        from services.task_executor import TaskExecutor
        executor = TaskExecutor(database_service=db, poll_interval=1)

    async def _fake_get_setting(key: str, default: str = "") -> str:
        return get_setting_overrides.get(key, default)

    executor._get_setting = _fake_get_setting
    return executor, db


@pytest.mark.unit
@pytest.mark.asyncio
async def test_auto_retry_disabled_when_max_attempts_zero():
    """Default ``task_retry_max_attempts=0`` means the sweeper never queries.

    This is the operator-must-explicitly-retry mode that makes the
    failure-storm scenario impossible. The DB query is the proof —
    if we never SELECT failed rows, we can never re-claim them.
    """
    executor, db = _make_executor_for_retry_test(
        {"task_retry_max_attempts": "0"}
    )
    await executor._auto_retry_failed_tasks()
    db.pool.fetch.assert_not_awaited()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_auto_retry_invalid_value_falls_back_to_disabled():
    """Garbage value in app_settings → treated as 0, sweeper stays off."""
    executor, db = _make_executor_for_retry_test(
        {"task_retry_max_attempts": "abc"}
    )
    await executor._auto_retry_failed_tasks()
    db.pool.fetch.assert_not_awaited()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_auto_retry_enabled_uses_exponential_backoff_in_query():
    """When opted in, the SELECT must enforce the backoff window inline.

    The query must reference both ``task_retry_max_attempts`` (the cap)
    and the ``backoff * 2^retry_count`` exponential window so the
    database — not Python — gates eligibility. Asserting on the SQL
    string keeps the regression cheap and deterministic.
    """
    executor, db = _make_executor_for_retry_test(
        {
            "task_retry_max_attempts": "3",
            "task_retry_backoff_initial_seconds": "60",
            "task_retry_window_hours": "24",
        }
    )
    await executor._auto_retry_failed_tasks()
    db.pool.fetch.assert_awaited_once()
    sql = db.pool.fetch.call_args[0][0]
    # Backoff math is enforced in the WHERE clause itself.
    assert "POWER(2" in sql
    assert "EXTRACT(EPOCH FROM (NOW() - updated_at))" in sql
    # Parameters: $1 = max_attempts (cap), $2 = backoff_initial (base).
    args = db.pool.fetch.call_args[0]
    assert args[1] == 3  # max_attempts
    assert args[2] == 60  # backoff_initial
