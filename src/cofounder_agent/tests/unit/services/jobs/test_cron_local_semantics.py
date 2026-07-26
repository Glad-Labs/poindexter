"""The three wall-clock jobs are authored in operator-local time now."""
from __future__ import annotations

from services.jobs.findings_daily_digest import FindingsDailyDigestJob
from services.jobs.morning_brief import MorningBriefJob
from services.jobs.run_dev_diary_post import RunDevDiaryPostJob


def test_morning_brief_is_7am_local():
    assert MorningBriefJob.schedule == "0 7 * * *"


def test_findings_digest_is_9am_local():
    assert FindingsDailyDigestJob.schedule == "0 9 * * *"


def test_dev_diary_rebased_to_9am_local_not_1pm():
    # Was "0 13 * * *" (pre-baked to UTC for 9am EDT); now local semantics.
    assert RunDevDiaryPostJob.schedule == "0 9 * * *"
