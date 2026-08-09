"""The dev-diary cron and its lookback window must describe the same period.

The failure this guards is silent and shaped badly: change the cron to weekly,
leave ``hours_lookback`` at 24, and the job still runs, still finds material,
still publishes — covering one day in seven and claiming to be the week's diary.
Nothing errors. Six days of work just never appear.

So the two live as one constant pair in the job module, and this file asserts
they agree, that the seeded default agrees with both, and that the topic label
is derived from the window rather than hardcoded.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from services.jobs.run_dev_diary_post import (
    _DEFAULT_LOOKBACK_HOURS,
    _DEFAULT_SCHEDULE,
    RunDevDiaryPostJob,
)
from services.topic_sources.dev_diary_source import DevDiaryContext


def _cron_interval_hours(expr: str) -> int:
    """Hours between fires for the simple crons this job uses.

    Deliberately narrow: it understands ``m h * * <dow>`` (weekly) and
    ``m h * * *`` (daily) and nothing else, so an exotic expression fails the
    test rather than being silently mis-measured.
    """
    fields = expr.split()
    assert len(fields) == 5, f"unexpected cron shape: {expr!r}"
    _minute, _hour, dom, month, dow = fields
    assert dom == "*" and month == "*", f"unsupported cron: {expr!r}"
    if dow == "*":
        return 24
    assert re.fullmatch(r"\d", dow), f"unsupported day-of-week: {dow!r}"
    return 24 * 7


class TestCronAndLookbackAgree:
    def test_lookback_covers_the_whole_interval_between_fires(self):
        assert _DEFAULT_LOOKBACK_HOURS == _cron_interval_hours(_DEFAULT_SCHEDULE), (
            "the dev-diary lookback no longer matches its cron. A window "
            "narrower than the interval publishes an incomplete diary without "
            "erroring; a wider one double-reports work."
        )

    def test_the_job_class_uses_the_constant(self):
        assert RunDevDiaryPostJob.schedule == _DEFAULT_SCHEDULE

    def test_cadence_is_weekly(self):
        assert _cron_interval_hours(_DEFAULT_SCHEDULE) == 24 * 7


class TestSeededDefaultMatchesTheCode:
    """``0000_baseline.seeds.sql`` is what a fresh install actually gets; the
    class attribute only applies when the plugin row is absent."""

    def _seeded_config(self) -> dict:
        # Anchor on the FILE, not on a ``services/migrations`` directory:
        # tests/unit/services/migrations/ also exists, so a directory probe
        # matches tests/unit first and resolves to a path with no seeds file.
        rel = Path("services") / "migrations" / "0000_baseline.seeds.sql"
        seeds = next(
            p / rel for p in Path(__file__).resolve().parents if (p / rel).is_file()
        )
        sql = seeds.read_text(encoding="utf-8")
        line = next(
            ln for ln in sql.splitlines()
            if "'plugin.job.run_dev_diary_post'" in ln
        )
        payload = re.search(r"'(\{.*?\})',\s*'plugins'", line)
        assert payload, f"could not parse the seeded value from: {line[:120]}"
        return json.loads(payload.group(1))

    def test_seeded_schedule_matches(self):
        assert self._seeded_config()["config"]["schedule"] == _DEFAULT_SCHEDULE

    def test_seeded_lookback_matches(self):
        assert (
            self._seeded_config()["config"]["hours_lookback"]
            == _DEFAULT_LOOKBACK_HOURS
        )


class TestHeadlineLabelFollowsTheWindow:
    def _ctx(self, hours: int) -> DevDiaryContext:
        return DevDiaryContext(
            date="2026-08-09",
            merged_prs=[{"title": "x"}],
            notable_commits=[],
            brain_decisions=[],
            audit_resolved=[],
            recent_posts=[],
            cost_summary={},
            lookback_hours=hours,
        )

    @pytest.mark.parametrize(("hours", "word"), [
        (24, "Daily"),
        (36, "Daily"),      # a nudged daily run is still daily
        (168, "Weekly"),
        (144, "Weekly"),    # 6d
        (192, "Weekly"),    # 8d
        (72, "3-day"),      # neither — say what it actually covers
        (336, "14-day"),
    ])
    def test_period_label(self, hours, word):
        assert self._ctx(hours).period_label() == word

    def test_headline_uses_the_label_not_a_hardcoded_daily(self):
        assert self._ctx(168).headline().startswith("Weekly dev diary — 2026-08-09")

    def test_headline_at_the_job_default_says_weekly(self):
        """Ties the label to the shipped cadence, so flipping one without the
        other is caught here too."""
        headline = self._ctx(_DEFAULT_LOOKBACK_HOURS).headline()
        assert headline.startswith("Weekly dev diary")

    def test_default_lookback_on_the_dataclass_stays_daily(self):
        """Callers that don't pass the window (the CLI, older tests) must not
        silently start claiming 'Weekly'."""
        ctx = DevDiaryContext(
            date="2026-08-09", merged_prs=[], notable_commits=[],
            brain_decisions=[], audit_resolved=[], recent_posts=[], cost_summary={},
        )
        assert ctx.period_label() == "Daily"

    def test_counts_still_render(self):
        assert "(1 PR)" in self._ctx(168).headline()
