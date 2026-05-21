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
# Acceptance Criterion 3: auto-retry sweeper was a TaskExecutor concern
# that was deleted alongside ``services/task_executor.py`` in
# Glad-Labs/poindexter#410 Stage 4 (2026-05-16). The
# ``task_retry_max_attempts=0`` default kept it disabled in production
# the whole time; operators retry via the CLI / approval UI now. Retry
# semantics on a fundamentally failed flow run are owned by Prefect's
# native ``retries=`` + ``retry_delay_seconds=`` on the flow definition
# (see ``services/flows/content_generation.py``).
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Input-coercion edge cases — _hash_error
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_hash_error_handles_none_without_raising():
    """``error_message=None`` is reachable from upstream callers — the
    helper must coerce to '' rather than raising AttributeError.

    Regression guard for the line-54 ``if error_message is None`` branch.
    """
    h = tfa._hash_error(None)  # type: ignore[arg-type]
    assert isinstance(h, str)
    assert len(h) == 16
    # Must equal the explicit-empty-string hash so dedup keys are stable
    # whether the upstream caller passes None or "".
    assert h == tfa._hash_error("")


@pytest.mark.unit
def test_hash_error_distinguishes_distinct_messages():
    """Two different error strings must produce different short hashes;
    a collision here would silently dedup unrelated failures.
    """
    a = tfa._hash_error("OllamaConnectionError: 502")
    b = tfa._hash_error("OllamaConnectionError: 503")
    assert a != b
    assert len(a) == 16 and len(b) == 16


# ---------------------------------------------------------------------------
# send_failure_alert — input-coercion & "never raises" contract
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_none_error_message_does_not_crash_alert_path():
    """``error_message=None`` flows through ``_hash_error`` AND the
    notify_operator format string. Both must tolerate it without
    raising, per the docstring's "never raises" guarantee.
    """
    tfa._reset_lru_for_tests()

    with patch(
        "services.integrations.operator_notify.notify_operator",
        new_callable=AsyncMock,
    ) as mock_notify:
        out = await tfa.send_failure_alert(
            task_id="t-none-err",
            topic="Topic",
            error_message=None,  # type: ignore[arg-type]
            pool=None,
            get_setting=_make_get_setting({}),
        )

    assert out["sent"] is True
    assert out["deduped"] is False
    mock_notify.assert_awaited_once()
    # The "Unknown error" fallback must surface in the formatted message.
    sent_msg = mock_notify.await_args.args[0]
    assert "Unknown error" in sent_msg


@pytest.mark.unit
@pytest.mark.asyncio
async def test_notify_operator_raising_does_not_propagate():
    """When ``notify_operator`` raises, ``send_failure_alert`` must
    return ``sent=False`` with the failure captured in ``reason`` —
    never bubble. A propagating exception here would crash the
    failure-handling path inside Prefect and lose the original error
    context.
    """
    tfa._reset_lru_for_tests()

    with patch(
        "services.integrations.operator_notify.notify_operator",
        new_callable=AsyncMock,
        side_effect=RuntimeError("telegram bot offline"),
    ):
        out = await tfa.send_failure_alert(
            task_id="t-notify-raises",
            topic="Topic",
            error_message="boom",
            pool=None,
            get_setting=_make_get_setting({}),
        )

    assert out["sent"] is False
    assert out["deduped"] is False
    assert "telegram bot offline" in out["reason"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_setting_raising_falls_back_to_safe_defaults():
    """If ``get_setting`` itself raises (e.g. pool down during boot),
    the helper must fall back to the seeded defaults (window=900,
    severity='discord') rather than failing the whole alert.
    """
    tfa._reset_lru_for_tests()

    async def _broken_get_setting(_key: str, _default: str = "") -> str:
        raise RuntimeError("settings cache not yet warmed")

    with patch(
        "services.integrations.operator_notify.notify_operator",
        new_callable=AsyncMock,
    ) as mock_notify:
        out = await tfa.send_failure_alert(
            task_id="t-broken-settings",
            topic="Topic",
            error_message="boom",
            pool=None,
            get_setting=_broken_get_setting,
        )

    # Falls back to discord (the safe default), not telegram.
    assert out["sent"] is True
    assert out["channel"] == "discord"
    assert mock_notify.await_args.kwargs.get("critical") is False


@pytest.mark.unit
@pytest.mark.asyncio
async def test_non_integer_window_falls_back_to_default_900():
    """Operator typo (``window_raw="never"``) must not blow up the
    ``int()`` cast — the helper catches ValueError and reverts to the
    900s default. Dedup remains active afterwards.
    """
    tfa._reset_lru_for_tests()

    overrides = {"task_failure_alert_dedup_window_seconds": "never"}
    with patch(
        "services.integrations.operator_notify.notify_operator",
        new_callable=AsyncMock,
    ) as mock_notify:
        await tfa.send_failure_alert(
            task_id="t-bad-window",
            topic="Topic",
            error_message="same error",
            pool=None,
            get_setting=_make_get_setting(overrides),
        )
        # Second call inside the (default) 900s window must be deduped.
        out2 = await tfa.send_failure_alert(
            task_id="t-bad-window",
            topic="Topic",
            error_message="same error",
            pool=None,
            get_setting=_make_get_setting(overrides),
        )

    assert mock_notify.await_count == 1
    assert out2["deduped"] is True


@pytest.mark.unit
@pytest.mark.asyncio
async def test_severity_normalization_strips_and_lowercases():
    """``" TELEGRAM "`` (operator copy-paste with trailing space, caps)
    must normalize to ``telegram`` — otherwise an invisible whitespace
    typo would silently route to the safer Discord channel without
    surfacing the operator's actual intent.
    """
    tfa._reset_lru_for_tests()

    with patch(
        "services.integrations.operator_notify.notify_operator",
        new_callable=AsyncMock,
    ) as mock_notify:
        out = await tfa.send_failure_alert(
            task_id="t-severity-norm",
            topic="Topic",
            error_message="boom",
            pool=None,
            get_setting=_make_get_setting(
                {"task_failure_alert_severity": "  TELEGRAM  "}
            ),
        )

    assert out["channel"] == "telegram"
    assert mock_notify.await_args.kwargs.get("critical") is True


@pytest.mark.unit
@pytest.mark.asyncio
async def test_distinct_task_ids_with_same_error_are_independent():
    """Dedup key is ``(task_id, error_hash)`` — two DIFFERENT tasks
    failing with the SAME error must each get their own alert. Pinning
    this so a future "dedup by error_hash alone" refactor doesn't
    silently swallow unrelated tasks' failures.
    """
    tfa._reset_lru_for_tests()

    overrides = {"task_failure_alert_dedup_window_seconds": "900"}
    with patch(
        "services.integrations.operator_notify.notify_operator",
        new_callable=AsyncMock,
    ) as mock_notify:
        await tfa.send_failure_alert(
            task_id="task-A",
            topic="Topic A",
            error_message="shared error",
            pool=None,
            get_setting=_make_get_setting(overrides),
        )
        await tfa.send_failure_alert(
            task_id="task-B",
            topic="Topic B",
            error_message="shared error",
            pool=None,
            get_setting=_make_get_setting(overrides),
        )

    assert mock_notify.await_count == 2


@pytest.mark.unit
@pytest.mark.asyncio
async def test_negative_window_clamps_to_zero_and_disables_dedup():
    """``max(0, int(window_raw))`` must clamp negatives — otherwise a
    negative value would feed straight into the ``(ts - last) < window``
    comparison and inadvertently keep dedup active forever (negative <
    any positive age).
    """
    tfa._reset_lru_for_tests()

    overrides = {"task_failure_alert_dedup_window_seconds": "-60"}
    with patch(
        "services.integrations.operator_notify.notify_operator",
        new_callable=AsyncMock,
    ) as mock_notify:
        for _ in range(3):
            await tfa.send_failure_alert(
                task_id="t-neg-window",
                topic="Topic",
                error_message="same error",
                pool=None,
                get_setting=_make_get_setting(overrides),
            )

    # Clamped to 0 → dedup disabled → all 3 sent.
    assert mock_notify.await_count == 3


# ---------------------------------------------------------------------------
# Persistent dedup (DB layer) — best-effort failure semantics
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_persistent_dedup_db_error_is_swallowed():
    """If the persistent dedup table is missing (fresh DB) or the pool
    is mid-restart, the DB exception must not crash the alert path —
    the LRU is the source of truth and the persistent layer is a
    best-effort mirror.
    """
    tfa._reset_lru_for_tests()

    # Pool that raises on acquire() — simulates DB down.
    class _BrokenPool:
        def acquire(self):
            raise RuntimeError("pool is closed")

    with patch(
        "services.integrations.operator_notify.notify_operator",
        new_callable=AsyncMock,
    ) as mock_notify:
        out = await tfa.send_failure_alert(
            task_id="t-broken-pool",
            topic="Topic",
            error_message="boom",
            pool=_BrokenPool(),
            get_setting=_make_get_setting({}),
        )

    # First-time call still routes through (LRU records, persistent
    # layer silently fails). Alert still sent.
    assert out["sent"] is True
    assert out["deduped"] is False
    mock_notify.assert_awaited_once()
