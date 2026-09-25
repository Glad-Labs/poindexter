"""Unit tests — brain/failure_episode.py, the shared "page once per episode" rule.

The probes' own suites (``test_pr_staleness_probe_failure_episodes.py``,
``test_branch_drift_probe_failure_episodes.py``) replay whole incidents. This
file pins the decision table directly, the rows written before this module
existed, and the never-raise contract of the brain_knowledge helpers.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from poindexter.brain import failure_episode as fe

_T0 = datetime(2026, 9, 23, 23, 37, 0, tzinfo=UTC)
_TOKEN_A = "2026-09-23T23:13:20+00:00"
_TOKEN_B = "2026-09-24T08:00:00+00:00"
_KEY = fe.EpisodeKey(entity="some_probe", attribute="failure_episode:o/r", label="SOME")
_LOGGER = "brain.failure_episode"


def _told(signature: str = "x:404", **extra: Any) -> dict[str, Any]:
    """An episode whose first page reached the operator at T0."""
    episode, reason = fe.decide_page(
        None, signature=signature, now_utc=_T0, token_changed_at=_TOKEN_A,
    )
    assert reason == fe.PAGE_NEW
    fe.mark_delivered(episode, now_utc=_T0)
    episode.update(extra)
    return episode


def _decide(prev: dict[str, Any] | None, signature: str = "x:404", *,
            at: timedelta = timedelta(minutes=15), **kwargs: Any):
    kwargs.setdefault("token_changed_at", _TOKEN_A)
    kwargs.setdefault("repage_hours", 24)
    return fe.decide_page(prev, signature=signature, now_utc=_T0 + at, **kwargs)


# ---------------------------------------------------------------------------
# The decision table
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestDecidePage:
    def test_a_loud_failure_pages_when_the_episode_opens(self):
        episode, reason = fe.decide_page(None, signature="x:404", now_utc=_T0)
        assert reason == fe.PAGE_NEW
        assert episode["since"] == _T0.isoformat()
        assert episode["attempts"] == 1
        assert episode["paged_at"] is None
        assert episode["owed"] is None

    def test_the_same_failure_stays_quiet(self):
        assert _decide(_told())[1] is None

    def test_a_different_loud_failure_is_news(self):
        episode, reason = _decide(_told(), "x:401")
        assert reason == fe.PAGE_CHANGED
        assert episode["previous_signature"] == "x:404"
        assert episode["signature"] == "x:401"

    def test_a_quiet_failure_after_a_page_is_not_news(self):
        episode, reason = _decide(_told(), "x:5xx", loud=False)
        assert reason is None
        assert episode["signature"] == "x:5xx"
        assert episode["paged_signature"] == "x:404"

    def test_a_replaced_token_that_fails_the_same_way_is_news(self):
        assert _decide(_told(), token_changed_at=_TOKEN_B)[1] == fe.PAGE_TOKEN_REPLACED

    def test_a_replaced_token_during_a_quiet_failure_waits_for_a_loud_one(self):
        """A 503 says nothing about the new token; the next 404 does."""
        episode, reason = _decide(_told(), "x:5xx", loud=False, token_changed_at=_TOKEN_B)
        assert reason is None
        _, reason = _decide(episode, at=timedelta(minutes=30), token_changed_at=_TOKEN_B)
        assert reason == fe.PAGE_TOKEN_REPLACED

    def test_an_unknown_token_timestamp_at_page_time_is_bookkeeping(self):
        told = _told(paged_token_changed_at=None, token_changed_at=None)
        episode, reason = _decide(told, token_changed_at=_TOKEN_B)
        assert reason is None
        assert episode["paged_token_changed_at"] == _TOKEN_B
        # ...and a replacement AFTER that first read is still caught.
        _, reason = _decide(episode, at=timedelta(hours=1), token_changed_at="2026-09-24T09:00:00+00:00")
        assert reason == fe.PAGE_TOKEN_REPLACED

    def test_an_unreadable_token_timestamp_is_not_a_replacement(self):
        episode, reason = _decide(_told(), token_changed_at=None)
        assert reason is None
        assert episode["token_changed_at"] == _TOKEN_A  # last known value kept

    def test_reminder_after_the_window_and_never_at_zero(self):
        assert _decide(_told(), at=timedelta(hours=23, minutes=59))[1] is None
        assert _decide(_told(), at=timedelta(hours=24))[1] == fe.PAGE_REMINDER
        assert _decide(_told(), at=timedelta(days=9), repage_hours=0)[1] is None

    def test_a_quiet_failure_can_be_reminded_about(self):
        assert _decide(_told(), "x:5xx", loud=False, at=timedelta(hours=24))[1] == fe.PAGE_REMINDER

    def test_quiet_failures_page_only_once_they_persist(self):
        episode, reason = fe.decide_page(
            None, signature="x:5xx", now_utc=_T0, loud=False,
            quiet_page_after=timedelta(hours=6),
        )
        assert reason is None
        for minutes in (15, 60, 359):
            episode, reason = fe.decide_page(
                episode, signature="x:5xx", now_utc=_T0 + timedelta(minutes=minutes),
                loud=False, quiet_page_after=timedelta(hours=6),
            )
            assert reason is None
        _, reason = fe.decide_page(
            episode, signature="Timeout", now_utc=_T0 + timedelta(hours=6),
            loud=False, quiet_page_after=timedelta(hours=6),
        )
        assert reason == fe.PAGE_PERSISTING

    def test_quiet_failures_never_page_without_a_threshold(self):
        _, reason = fe.decide_page(
            None, signature="x:5xx", now_utc=_T0, loud=False, quiet_page_after=None,
        )
        assert reason is None
        _, reason = fe.decide_page(
            {"since": (_T0 - timedelta(days=30)).isoformat(), "owed": None, "paged_at": None},
            signature="x:5xx", now_utc=_T0, loud=False, quiet_page_after=None,
        )
        assert reason is None

    def test_an_undelivered_first_page_is_owed(self):
        episode, reason = fe.decide_page(None, signature="x:404", now_utc=_T0)
        episode["owed"] = reason  # what record_failure does when the send fails
        assert _decide(episode)[1] == fe.PAGE_UNDELIVERED

    def test_an_owed_loud_page_waits_out_a_quiet_blip(self):
        episode, reason = fe.decide_page(None, signature="x:404", now_utc=_T0)
        episode["owed"] = reason
        episode, reason = _decide(episode, "x:5xx", loud=False,
                                  quiet_page_after=timedelta(hours=6))
        assert reason is None
        assert episode["owed"] == fe.PAGE_NEW
        _, reason = _decide(episode, at=timedelta(minutes=30))
        assert reason == fe.PAGE_UNDELIVERED

    def test_an_owed_page_about_a_superseded_failure_is_dropped(self):
        """401 was news but never sent; the failure is back to the 404 the
        operator already has, so nothing is owed any more."""
        told = _told()
        episode, reason = _decide(told, "x:401")
        assert reason == fe.PAGE_CHANGED
        episode["owed"] = reason
        episode, reason = _decide(episode, "x:404", at=timedelta(minutes=30))
        assert reason is None
        assert episode["owed"] is None

    def test_mark_delivered_records_what_the_operator_now_knows(self):
        episode, _ = _decide(_told(), "x:401", token_changed_at=_TOKEN_B)
        episode["owed"] = fe.PAGE_CHANGED
        fe.mark_delivered(episode, now_utc=_T0 + timedelta(hours=1))
        assert episode["paged_at"] == (_T0 + timedelta(hours=1)).isoformat()
        assert episode["pages"] == 2
        assert episode["paged_signature"] == "x:401"
        assert episode["paged_token_changed_at"] == _TOKEN_B
        assert episode["owed"] is None


@pytest.mark.unit
class TestRowsWrittenBeforeThisModule:
    """#4041 wrote pr_staleness episodes without paged_signature /
    paged_token_changed_at / owed. Deploying this must neither re-page an
    episode it already paged nor forget one whose page never went out."""

    def test_a_paged_legacy_row_stays_quiet(self):
        legacy = {
            "signature": "pulls:404", "token_changed_at": _TOKEN_A,
            "since": _T0.isoformat(), "attempts": 7, "paged_at": _T0.isoformat(),
            "pages": 1, "last_detail": "…",
        }
        episode, reason = fe.decide_page(
            legacy, signature="pulls:404", now_utc=_T0 + timedelta(hours=7),
            token_changed_at=_TOKEN_A, repage_hours=24,
        )
        assert reason is None
        assert episode["attempts"] == 8
        assert episode["paged_signature"] == "pulls:404"

    def test_a_paged_legacy_row_still_notices_a_replaced_token(self):
        legacy = {"signature": "pulls:404", "token_changed_at": _TOKEN_A,
                  "since": _T0.isoformat(), "attempts": 2, "paged_at": _T0.isoformat(), "pages": 1}
        _, reason = fe.decide_page(
            legacy, signature="pulls:404", now_utc=_T0 + timedelta(hours=2),
            token_changed_at=_TOKEN_B, repage_hours=24,
        )
        assert reason == fe.PAGE_TOKEN_REPLACED

    def test_an_unpaged_legacy_row_owes_its_first_page(self):
        legacy = {"signature": "pulls:404", "token_changed_at": _TOKEN_A,
                  "since": _T0.isoformat(), "attempts": 1, "paged_at": None, "pages": 0}
        _, reason = fe.decide_page(
            legacy, signature="pulls:404", now_utc=_T0 + timedelta(hours=1),
            token_changed_at=_TOKEN_A, repage_hours=24,
        )
        assert reason == fe.PAGE_UNDELIVERED


# ---------------------------------------------------------------------------
# Delivery
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestDelivery:
    @pytest.mark.parametrize(
        ("results", "delivered"),
        [
            ({"telegram": "skipped (severity below error)", "discord": "discord"}, True),
            ({"telegram": "telegram", "discord": "discord send failed: timeout"}, True),
            ({"discord": "suppressed (page cooldown)"}, True),
            ({"discord": "discord send failed: URLError('dns')"}, False),
            ({"telegram": "telegram send failed: x", "discord": "discord send failed: y"}, False),
            ({"discord": "no DISCORD_*_WEBHOOK_URL set"}, True),  # nothing to retry
            (None, True),  # a notifier whose answer we can't read
        ],
    )
    def test_page_delivered(self, results, delivered):
        assert fe.page_delivered(results) is delivered

    def test_send_page_survives_a_raising_notifier(self, caplog):
        def _down(**_kwargs: Any) -> None:
            raise RuntimeError("notifier down")

        with caplog.at_level(logging.WARNING, logger=_LOGGER):
            ok = fe.send_page(_down, label="SOME", title="t", detail="d", source="s",
                              severity="warning", dedup_key="k")
        assert ok is False
        assert "NOT told" in caplog.text
        assert "notifier down" in caplog.text

    def test_send_page_says_what_happens_next(self, caplog):
        with caplog.at_level(logging.WARNING, logger=_LOGGER):
            ok = fe.send_page(
                lambda **_: {"discord": "discord send failed: x"}, label="SOME",
                title="t", detail="d", source="s", severity="info", dedup_key="k",
                if_undelivered="the operator still believes it is broken",
            )
        assert ok is False
        assert "the operator still believes it is broken" in caplog.text


# ---------------------------------------------------------------------------
# record_failure: the notifier's dedup key
# ---------------------------------------------------------------------------


class _Pool:
    def __init__(self) -> None:
        self.rows: dict[tuple[str, str], str] = {}

    async def fetchval(self, query: str, *args: Any) -> Any:
        if "FROM brain_knowledge" in query:
            return self.rows.get((args[0], args[1]))
        return None

    async def execute(self, query: str, *args: Any) -> str:
        if "INSERT INTO brain_knowledge" in query:
            self.rows[(args[0], args[1])] = args[2]
        elif "DELETE FROM brain_knowledge" in query:
            self.rows.pop((args[0], args[1]), None)
        return "OK"


@pytest.mark.unit
class TestRecordFailure:
    @pytest.mark.asyncio
    async def test_every_decided_page_is_new_to_the_notifier_cooldown(self):
        pool, keys = _Pool(), []

        def _notify(**kwargs: Any) -> dict[str, str]:
            keys.append(kwargs["dedup_key"])
            return {"discord": "discord"}

        for i, signature in enumerate(("x:404", "x:404", "x:401", "x:404")):
            await fe.record_failure(
                pool, _KEY, signature=signature, detail="d",
                now_utc=_T0 + timedelta(minutes=15 * i), notify_fn=_notify,
                render=lambda _e, reason: (reason, "body"), source="s",
                dedup_prefix="probe_failed:o/r",
            )
        assert keys == ["probe_failed:o/r:x:404:0", "probe_failed:o/r:x:401:1",
                        "probe_failed:o/r:x:404:2"]

    @pytest.mark.asyncio
    async def test_the_episode_is_persisted_and_closed(self):
        pool = _Pool()
        outcome = await fe.record_failure(
            pool, _KEY, signature="x:404", detail="d" * 900, now_utc=_T0,
            notify_fn=lambda **_: {"discord": "discord"},
            render=lambda _e, _r: ("t", "b"), source="s", dedup_prefix="p",
        )
        assert outcome.paged is True
        stored = json.loads(pool.rows[(_KEY.entity, _KEY.attribute)])
        assert stored["pages"] == 1
        assert len(stored["last_detail"]) == 500

        closed = await fe.close_episode(pool, _KEY)
        assert closed is not None and closed["signature"] == "x:404"
        assert pool.rows == {}
        assert await fe.close_episode(pool, _KEY) is None


# ---------------------------------------------------------------------------
# Never-raise contract of the storage helpers
# ---------------------------------------------------------------------------


def _broken_pool() -> MagicMock:
    pool = MagicMock()
    pool.fetchval = AsyncMock(side_effect=RuntimeError("db down"))
    pool.execute = AsyncMock(side_effect=RuntimeError("db down"))
    return pool


@pytest.mark.unit
@pytest.mark.asyncio
class TestStorageFailuresAreVisible:
    async def test_read_failure_warns_and_reads_as_no_episode(self, caplog):
        with caplog.at_level(logging.WARNING, logger=_LOGGER):
            assert await fe.read_episode(_broken_pool(), _KEY) is None
        assert "may be paged again" in caplog.text

    async def test_a_corrupt_row_warns_and_starts_over(self, caplog):
        pool = MagicMock()
        pool.fetchval = AsyncMock(return_value="{not json")
        with caplog.at_level(logging.WARNING, logger=_LOGGER):
            assert await fe.read_episode(pool, _KEY) is None
        assert "not JSON" in caplog.text

    async def test_write_and_clear_failures_warn(self, caplog):
        with caplog.at_level(logging.WARNING, logger=_LOGGER):
            await fe.write_episode(_broken_pool(), _KEY, {"signature": "x"})
            await fe.clear_episode(_broken_pool(), _KEY)
        assert "write failed" in caplog.text
        assert "clear failed" in caplog.text

    async def test_setting_changed_at_reads_the_timestamp_only(self, caplog):
        pool = MagicMock()
        pool.fetchval = AsyncMock(return_value=datetime(2026, 9, 23, 23, 13, 20))
        assert await fe.read_setting_changed_at(pool, "gh_token", label="SOME") == _TOKEN_A
        query = pool.fetchval.await_args.args[0]
        assert query.startswith("SELECT updated_at FROM app_settings")

        pool.fetchval = AsyncMock(return_value="not a timestamp")
        assert await fe.read_setting_changed_at(pool, "gh_token", label="SOME") is None

        with caplog.at_level(logging.WARNING, logger=_LOGGER):
            assert await fe.read_setting_changed_at(_broken_pool(), "gh_token", label="SOME") is None
        assert "gh_token.updated_at" in caplog.text


# ---------------------------------------------------------------------------
# Shared page text
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestEpisodeLines:
    def _lines(self, reason: str, episode: dict[str, Any], **kwargs: Any) -> str:
        kwargs.setdefault("repage_hours", 24)
        return "\n".join(fe.episode_lines(
            episode, reason=reason, retry_minutes=15,
            repage_setting_key="x_failure_repage_hours", **kwargs,
        ))

    def test_persisting_names_the_threshold(self):
        text = self._lines(fe.PAGE_PERSISTING, {"since": _T0.isoformat(), "attempts": 25},
                           quiet_page_after=timedelta(hours=6))
        assert "only once it has lasted 6h. This one has." in text
        assert "Failing since 2026-09-23 23:37 UTC (25 attempts)" in text

    def test_an_owed_page_is_mentioned_whatever_the_reason(self):
        text = self._lines(fe.PAGE_CHANGED, {
            "signature": "x:401", "previous_signature": "x:404", "owed": fe.PAGE_CHANGED,
            "since": _T0.isoformat(), "attempts": 3,
        })
        assert "The failure changed (was x:404, now x:401)." in text
        assert "The previous page about this failure reached no channel." in text

    def test_reminders_off(self):
        text = self._lines(fe.PAGE_NEW, {"since": _T0.isoformat(), "attempts": 1}, repage_hours=0)
        assert "Reminders are off (app_settings.x_failure_repage_hours=0)" in text
        assert "(1 attempt)" in text

    def test_recovery_summary(self):
        summary = fe.recovery_summary({"attempts": 3, "since": _T0.isoformat(), "signature": "x:404"})
        assert summary == "after 3 failed attempts since 2026-09-23 23:37 UTC (last failure: x:404)"
