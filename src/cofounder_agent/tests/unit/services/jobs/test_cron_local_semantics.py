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
    # Asserts the FIRE TIME only — the day-of-week field is cadence, owned by
    # tests/unit/services/jobs/test_dev_diary_cadence.py (weekly since
    # 2026-08-09). Pinning the whole expression here made two files disagree
    # about who owns cadence.
    minute, hour, dom, month, _dow = RunDevDiaryPostJob.schedule.split()
    assert (minute, hour) == ("0", "9")
    assert (dom, month) == ("*", "*")
